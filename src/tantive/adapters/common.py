"""What every adapter does the same way: limits, the one classification call, the decision line and the metrics."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import Request

from tantive.ask import EmptyAsk, current_ask
from tantive.capture import Capture
from tantive.engine import Engine, Overloaded
from tantive.errors import ApiError, error_body
from tantive.model import ModelInfo, Outputs
from tantive.questions import Catalogue, Question, UnknownQuestion
from tantive.telemetry import log_decision, metrics
from tantive.tiers import TierSet

MAX_TEXT_CHARS = 50_000
MAX_QUESTIONS = 64
MAX_BATCH = 32


@dataclass(frozen=True)
class Core:
    """The app's shared objects, as adapters see them."""

    engine: Engine
    catalogue: Catalogue
    info: ModelInfo
    native: TierSet
    capture: Capture | None = None

    @classmethod
    def of(cls, request: Request) -> Core:
        s = request.app.state
        return cls(s.engine, s.catalogue, s.info, s.native, s.capture)

    @property
    def model_name(self) -> str:
        """`s1d1-<variant>`, without doubling the prefix the Hub variant names already carry."""
        v = self.info.variant
        return v if v.startswith("s1d1") else f"s1d1-{v}"


def ask_of(text: str, *, litellm_marker: bool = True) -> str:
    """The text to classify, within the size limit: after LiteLLM's last marker when `litellm_marker`."""
    if len(text) > MAX_TEXT_CHARS:
        raise ApiError(413, f"text exceeds {MAX_TEXT_CHARS} characters")
    try:
        return current_ask(text) if litellm_marker else _non_blank(text)
    except EmptyAsk as e:
        raise ApiError(400, str(e)) from None


def _non_blank(text: str) -> str:
    if not text.strip():
        raise EmptyAsk("no text to classify")
    return text


def check_count(items: Sequence[object], limit: int, what: str) -> None:
    if not items:
        raise ApiError(400, f"at least one {what} is required")
    if len(items) > limit:
        raise ApiError(400, f"at most {limit} {what}s per request")


async def classify(core: Core, text: str) -> tuple[Outputs, float]:
    """One pass through the engine; Overloaded becomes 503 + Retry-After: 1 and counts in the metrics."""
    with metrics().in_flight():
        try:
            return await core.engine.classify(text)
        except Overloaded as e:
            metrics().overloaded()
            raise ApiError(
                503, f"overloaded: expected wait {e.expected_wait_ms:.0f} ms", {"Retry-After": "1"}
            ) from None


@dataclass(frozen=True)
class Classified:
    """One text through the engine: what the response and the decision line need."""

    text: str
    outputs: Outputs
    latency_ms: float


async def classify_text(core: Core, raw: str, *, litellm_marker: bool = True) -> Classified:
    """The single route's path: the text within its limits (400, 413), then one pass through the engine (503)."""
    text = ask_of(raw, litellm_marker=litellm_marker)
    outputs, latency_ms = await classify(core, text)
    return Classified(text, outputs, latency_ms)


Item = Classified | ApiError  # a batch slot: the classified text, or what the single route would have answered


async def classify_items(core: Core, raws: Sequence[str], *, litellm_marker: bool = True) -> list[Item]:
    """The batch routes, per item: each text takes the single route's path, and what that route would refuse (blank
    or too long text, shed) takes the item's slot instead of failing the batch. Body-level checks come before."""
    items: list[Item] = []
    for raw in raws:
        try:
            items.append(await classify_text(core, raw, litellm_marker=litellm_marker))
        except ApiError as e:
            items.append(e)
    return items


def item_error(e: ApiError) -> dict:
    """The slot of a failed item: `{"error": {"message", "type"}}`, the shape of a refused request."""
    return error_body(e.status_code, str(e.detail))


def batch_status(items: Sequence[Item]) -> tuple[int, dict[str, str] | None]:
    """200 when at least one item succeeded; 503 + Retry-After when every item was shed (the client must back off,
    not fix its request); 422 when every item failed for any other mix of reasons."""
    if any(isinstance(i, Classified) for i in items):
        return 200, None
    if all(isinstance(i, ApiError) and i.status_code == 503 for i in items):
        return 503, {"Retry-After": "1"}
    return 422, None


def answer(core: Core, outputs: Outputs, question: Question):
    try:
        return core.catalogue.answer(outputs, question)
    except UnknownQuestion as e:
        raise ApiError(422, f"{e.name}: {e.reason}. {e.catalogue()}") from None


def record(
    core: Core,
    route: str,
    fmt: str,
    outputs: Outputs,
    latency_ms: float,
    allowed: Sequence[str] | None = None,
    prompt: str | None = None,
) -> str:
    """The decision line and the metrics; returns the native tier of the score (what logs and metrics count)."""
    tier = core.native.tier_for(outputs.complexity_score)
    log_decision(route, fmt, outputs, tier, latency_ms, allowed, prompt)
    metrics().record_request(route, fmt, latency_ms / 1000, tier, outputs.prompt_tokens)
    if core.capture is not None and prompt is not None:
        core.capture.offer(prompt, route, fmt, outputs, tier, latency_ms)
    return tier


def now_ms() -> float:
    return time.perf_counter() * 1000


def decoded_vector(outputs: Outputs) -> list[float]:
    """The 21 decoded values: complexity score, the 19 task probabilities (softmax), the needs_tools probability."""
    tasks = outputs.task_probabilities
    return [outputs.complexity_score, *(round(tasks[t], 4) for t in tasks), outputs.needs_tools_probability]


def raw_vector(outputs: Outputs) -> list[float]:
    """The 21 raw outputs: complexity in tier units, the 19 task logits, the needs_tools logit."""
    return [outputs.complexity_output, *(round(float(v), 4) for v in outputs.task_logits), outputs.needs_tools_logit]
