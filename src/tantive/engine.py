"""The scheduler: one inference thread, a cost model fitted at warm-up, shedding by expected wait (spec § 5).

ONNX Runtime already uses every core for one pass, so passes are serialised on a single worker thread. A request is
accepted only when the predicted wait (what is queued or running, plus its own predicted cost) fits in the deadline;
otherwise it is rejected at once with Overloaded, which adapters turn into 503 + Retry-After. The input limit
(S1D1_MAX_INPUT_TOKENS, docs/40 § 4) bounds that own cost; under `auto` it is computed once, after the warm-up. The
cache (S1D1_CACHE_TTL_S, docs/40 § 6), when on, answers before the tokenisation and the admission.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from functools import partial

from tantive.cache import Cache, cache_key, fingerprint
from tantive.longprompt import MIN_TOKENS, LongPromptPolicy, encode, merge, split
from tantive.model import Encoded, Model, Outputs
from tantive.telemetry import metrics

WARMUP_TOKENS = (16, 256, 1024)
AUTO_SHARE = 0.5  # of the deadline: a request that fills the limit can wait behind another one and still fit
AUTO_STEP = 64


@dataclass(frozen=True)
class CostModel:
    fixed_ms: float
    per_token_ms: float

    def predict(self, tokens: int) -> float:
        return self.fixed_ms + self.per_token_ms * tokens

    def predict_pieces(self, lengths: list[int]) -> float:
        """A text run as several pieces in one pass: the fixed cost of each piece plus the cost of all their tokens."""
        return self.fixed_ms * len(lengths) + self.per_token_ms * sum(lengths)


@dataclass(frozen=True)
class InputLimit:
    """S1D1_MAX_INPUT_TOKENS resolved once, after the warm-up (docs/40 § 4)."""

    setting: int | str | None  # as configured: None (empty), an integer or "auto"
    effective: int  # tokens passed to the model at most, CLS and SEP included
    source: str  # "window" (empty), "user" (an integer) or "auto"
    predicted_ms: float  # the cost model's prediction for a text that fills the limit
    capped: bool = False  # an integer above what the policy can pass
    warning: bool = False  # beyond the deadline: the prediction (half of it under auto), a full window when empty


def resolve_limit(
    setting: int | str | None, policy: LongPromptPolicy, cost: CostModel, deadline_ms: int
) -> InputLimit:
    """Empty: what the policy can pass, as before, with the warning as before when a full window exceeds the deadline
    (under `chunks` too, though the prediction counts every piece). An integer: that, capped to what the policy can
    pass. `auto`: the largest multiple of AUTO_STEP from MIN_TOKENS to that cap whose predicted cost fits in
    AUTO_SHARE of the deadline, else MIN_TOKENS with a warning (the service starts anyway: shedding sometimes beats a
    crash loop)."""
    ceiling = policy.ceiling

    def predicted(tokens: int) -> float:
        return cost.predict_pieces(policy.piece_lengths(tokens))

    if setting is None:
        window_ms = cost.predict(policy.window)
        return InputLimit(None, ceiling, "window", predicted(ceiling), warning=window_ms > deadline_ms)
    if setting == "auto":
        budget = AUTO_SHARE * deadline_ms
        candidates = range(ceiling // AUTO_STEP * AUTO_STEP, MIN_TOKENS - 1, -AUTO_STEP)
        effective = next((n for n in candidates if predicted(n) <= budget), MIN_TOKENS)
        ms = predicted(effective)
        return InputLimit("auto", effective, "auto", ms, warning=ms > budget)
    effective = min(int(setting), ceiling)
    ms = predicted(effective)
    return InputLimit(setting, effective, "user", ms, capped=int(setting) > ceiling, warning=ms > deadline_ms)


class Overloaded(Exception):
    def __init__(self, expected_wait_ms: float) -> None:
        super().__init__(f"expected wait {expected_wait_ms:.0f} ms exceeds the deadline")
        self.expected_wait_ms = expected_wait_ms


class Engine:
    def __init__(
        self,
        model: Model,
        deadline_ms: int,
        max_queue: int,
        cost: CostModel | None = None,
        policy: LongPromptPolicy | None = None,
        max_input_tokens: int | str | None = None,
        cache: Cache | None = None,
    ) -> None:
        self.model = model
        self.policy = policy or LongPromptPolicy(window=model.info.max_length)
        self.max_input_tokens = max_input_tokens
        self.input_limit: InputLimit | None = None  # resolved by start(), after the warm-up
        self.cache = cache
        self._fingerprint = b""  # of the settings the outputs depend on, computed by start() once the limit is known
        self._computing: dict[bytes, asyncio.Future] = {}  # identical texts admitted and not finished, by cache key
        self.deadline_ms = deadline_ms
        self.max_queue = max_queue
        self._cost = cost
        self._executor: ThreadPoolExecutor | None = None  # the single inference worker
        self._tokenizer_executor: ThreadPoolExecutor | None = None  # tokenisation, off the loop and off the queue
        self._lock = threading.Lock()
        self._in_flight = 0  # accepted and not finished (one of them runs, the others wait)
        self._pending_cost_ms = 0.0  # predicted cost of everything submitted and not finished
        self._running_cost_ms = 0.0
        self._running_since: float | None = None

    @property
    def ready(self) -> bool:
        return self._executor is not None and self._cost is not None

    @property
    def cost(self) -> CostModel | None:
        return self._cost

    @property
    def queued(self) -> int:
        """Requests waiting behind the one that runs."""
        return max(0, self._in_flight - 1)

    @property
    def pending_cost_ms(self) -> float:
        """Predicted milliseconds before a new request starts: queued work plus what remains of the running pass."""
        with self._lock:
            elapsed = 0.0 if self._running_since is None else (time.perf_counter() - self._running_since) * 1000
            return max(0.0, self._pending_cost_ms - min(elapsed, self._running_cost_ms))

    async def start(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="s1d1-inference")
        self._tokenizer_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="s1d1-tokenize")
        if self._cost is None:
            self._cost = await asyncio.get_running_loop().run_in_executor(self._executor, self.warmup)
        self.input_limit = resolve_limit(self.max_input_tokens, self.policy, self._cost, self.deadline_ms)
        if self.input_limit.source != "window":
            self.policy = replace(self.policy, max_input_tokens=self.input_limit.effective)
        self._fingerprint = fingerprint(self.model.info, self.policy)

    def warmup(self) -> CostModel:
        """Run three synthetic texts, one untimed pass then two timed ones each, and fit
        `fixed_ms + per_token_ms * tokens` by least squares on the faster timed pass of each."""
        points = []
        for n in WARMUP_TOKENS:
            text = " ".join(["warm"] * n)
            tokens = self.model.count_tokens(text)
            self.model.run([text])
            timings = []
            for _ in range(2):
                t0 = time.perf_counter()
                self.model.run([text])
                timings.append((time.perf_counter() - t0) * 1000)
            points.append((tokens, min(timings)))
        self._cost = _least_squares(points)
        return self._cost

    async def classify(self, text: str) -> tuple[Outputs, float]:
        """The model's outputs for `text` and the wall-clock milliseconds spent (queue included)."""
        outputs, ms, _ = await self.classify_cached(text)
        return outputs, ms

    async def classify_cached(self, text: str) -> tuple[Outputs, float, str | None]:
        """`classify`, and how the cache answered: None when it is off; else `hit` (a stored entry, even with the
        queue full: it costs the inference thread nothing), `shared` (an identical text already admitted: its pass is
        awaited, the queue is not entered) or `miss` (a pass started, then stored). The pass runs in its own task, so
        a client that leaves does not stop it for the others; it is shared only once admitted, so a shed request
        leaves nothing behind and the next identical one tries again."""
        if self._executor is None or self._cost is None:
            raise RuntimeError("engine not started")
        t0 = time.perf_counter()
        if self.cache is None:
            pieces, truncated, own = await self._prepare(text)
            self._admit(own)
            t0 = time.perf_counter()
            outputs = await self._execute(pieces, truncated, own)
            return outputs, (time.perf_counter() - t0) * 1000, None
        key = cache_key(self._fingerprint, text)
        found = self._reuse(key)
        if found is None:
            pieces, truncated, own = await self._prepare(text)
            found = self._reuse(key)  # an identical text may have been admitted or stored during the tokenisation
        if found is not None:
            status, outputs = found
            if isinstance(outputs, asyncio.Future):
                outputs = await asyncio.shield(outputs)
            metrics().cache_hit("stored" if status == "hit" else "in_flight")
            return outputs, (time.perf_counter() - t0) * 1000, status
        self._admit(own)
        t0 = time.perf_counter()
        task = asyncio.ensure_future(self._execute(pieces, truncated, own))
        self._computing[key] = task
        task.add_done_callback(partial(self._computed, key))
        metrics().cache_miss()
        outputs = await asyncio.shield(task)
        return outputs, (time.perf_counter() - t0) * 1000, "miss"

    def _reuse(self, key: bytes) -> tuple[str, Outputs | asyncio.Future] | None:
        outputs = self.cache.get(key)
        if outputs is not None:
            return "hit", outputs
        task = self._computing.get(key)
        return None if task is None else ("shared", task)

    def _computed(self, key: bytes, task: asyncio.Future) -> None:
        """A pass ended: it is no longer shared, and stored only when it succeeded (every waiter got its error)."""
        del self._computing[key]
        if not task.cancelled() and task.exception() is None:
            self.cache.put(key, task.result())

    async def _prepare(self, text: str) -> tuple[list[Encoded], bool, float]:
        """The text tokenised once, on a tokeniser thread (off the event loop, and not behind the running pass, so an
        overloaded request is still rejected at once), only as far as the policy can keep; its pieces and predicted
        cost."""
        loop = asyncio.get_running_loop()
        encoded, seconds = await loop.run_in_executor(self._tokenizer_executor, self._encode, text)
        metrics().tokenized(seconds)  # on the loop, like every other series
        pieces, truncated = split(encoded, self.policy)
        return pieces, truncated, self._cost.predict_pieces([len(p.ids) for p in pieces])

    def _admit(self, own: float) -> None:
        """Accept a request of predicted cost `own`, or raise Overloaded when the queue is full or the expected wait
        exceeds the deadline."""
        expected = self.pending_cost_ms + own
        if self.queued >= self.max_queue or expected > self.deadline_ms:
            raise Overloaded(expected)
        with self._lock:
            self._in_flight += 1
            self._pending_cost_ms += own

    async def _execute(self, pieces: list[Encoded], truncated: bool, own: float) -> Outputs:
        """The pass of an admitted request, released from the queue's accounting whatever happens."""
        try:
            return await asyncio.get_running_loop().run_in_executor(
                self._executor, self._run_one, pieces, truncated, own
            )
        finally:
            with self._lock:
                self._in_flight -= 1
                self._pending_cost_ms = max(0.0, self._pending_cost_ms - own)

    def _encode(self, text: str) -> tuple[Encoded, float]:
        t0 = time.perf_counter()
        encoded = encode(self.model, text, self.policy)
        return encoded, time.perf_counter() - t0

    def _run_one(self, pieces: list[Encoded], truncated: bool, cost_ms: float) -> Outputs:
        with self._lock:
            self._running_cost_ms, self._running_since = cost_ms, time.perf_counter()
        try:
            outputs = self.model.run_encoded(pieces)  # every piece of the text in one pass
        finally:
            with self._lock:
                self._running_cost_ms, self._running_since = 0.0, None
        info = self.model.info
        return merge(
            outputs,
            [len(p.ids) for p in pieces],
            self.policy.score,
            self.policy.task,
            info.score_center,
            info.score_scale,
            truncated,
        )

    async def close(self) -> None:
        executors = [e for e in (self._executor, self._tokenizer_executor) if e is not None]
        self._executor = self._tokenizer_executor = None
        for executor in executors:
            await asyncio.get_running_loop().run_in_executor(None, executor.shutdown)


def _least_squares(points: list[tuple[int, float]]) -> CostModel:
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    var = sum((x - mean_x) ** 2 for x, _ in points)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / var if var else 0.0
    slope = max(0.0, slope)
    return CostModel(fixed_ms=max(0.0, mean_y - slope * mean_x), per_token_ms=slope)
