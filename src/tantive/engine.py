"""The scheduler: one inference thread, a cost model fitted at warm-up, shedding by expected wait (spec § 5).

ONNX Runtime already uses every core for one pass, so passes are serialised on a single worker thread. A request is
accepted only when the predicted wait (what is queued or running, plus its own predicted cost) fits in the deadline;
otherwise it is rejected at once with Overloaded, which adapters turn into 503 + Retry-After.
"""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from tantive.longprompt import LongPromptPolicy, merge, split
from tantive.model import Encoded, Model, Outputs

WARMUP_TOKENS = (16, 256, 1024)


@dataclass(frozen=True)
class CostModel:
    fixed_ms: float
    per_token_ms: float

    def predict(self, tokens: int) -> float:
        return self.fixed_ms + self.per_token_ms * tokens

    def predict_pieces(self, lengths: list[int]) -> float:
        """A text run as several pieces in one pass: the fixed cost of each piece plus the cost of all their tokens."""
        return self.fixed_ms * len(lengths) + self.per_token_ms * sum(lengths)


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
    ) -> None:
        self.model = model
        self.policy = policy or LongPromptPolicy(window=model.info.max_length)
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
        """The model's outputs for `text` and the wall-clock milliseconds spent (queue included). The text is
        tokenised once, on a tokeniser thread (off the event loop, and not behind the running pass, so an
        overloaded request is still rejected at once), and the cost is predicted from the token count before the
        request is admitted."""
        if self._executor is None or self._cost is None:
            raise RuntimeError("engine not started")
        loop = asyncio.get_running_loop()
        encoded = await loop.run_in_executor(self._tokenizer_executor, self.model.encode, text)
        pieces, truncated = split(encoded, self.policy)
        own = self._cost.predict_pieces([len(p.ids) for p in pieces])
        expected = self.pending_cost_ms + own
        if self.queued >= self.max_queue or expected > self.deadline_ms:
            raise Overloaded(expected)
        with self._lock:
            self._in_flight += 1
            self._pending_cost_ms += own
        t0 = time.perf_counter()
        try:
            outputs = await loop.run_in_executor(self._executor, self._run_one, pieces, truncated, own)
        finally:
            with self._lock:
                self._in_flight -= 1
                self._pending_cost_ms = max(0.0, self._pending_cost_ms - own)
        return outputs, (time.perf_counter() - t0) * 1000

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
