"""What every adapter does the same way: limits, the one classification call, the decision line and the metrics."""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass

from fastapi import Request

from tantive.ask import EmptyAsk, current_ask
from tantive.engine import Engine, Overloaded
from tantive.errors import ApiError
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

    @classmethod
    def of(cls, request: Request) -> Core:
        s = request.app.state
        return cls(s.engine, s.catalogue, s.info, s.native)

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
