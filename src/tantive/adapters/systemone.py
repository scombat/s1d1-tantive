"""System One (TypeSafe / Laya): what LiteLLM's Auto Router sends to a self-hosted classifier (spec § 3.5).

POST /v1/systemone        {state, questions: {name: {type, instructions?, criteria?}}, model?, min_confidence?}
POST /v1/systemone/batch  {states: [...], questions}  →  {"results": [...]}
Extra fields (Laya's controls) are accepted and ignored. `min_confidence` adds Laya's abstention fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field

from tantive.adapters import register
from tantive.adapters.common import MAX_BATCH, MAX_QUESTIONS, Core, answer, ask_of, check_count, classify, record
from tantive.ask import flatten_state
from tantive.errors import ApiError
from tantive.model import Outputs
from tantive.questions import (
    ChoiceAnswer,
    ChoiceQuestion,
    PredicateAnswer,
    PredicateQuestion,
    Question,
    ScoreAnswer,
    ScoreQuestion,
)

ROUTE, FORMAT = "/v1/systemone", "systemone"


class SystemOneQuestion(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    instructions: Any = None
    criteria: Any = None


State = str | list | dict  # DecisionsJSON: a scalar is a client bug, not a text to classify


class SystemOneRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    state: State
    questions: dict[str, SystemOneQuestion] = Field(min_length=1)
    model: str | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class SystemOneBatchRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    states: list[State]
    questions: dict[str, SystemOneQuestion] = Field(min_length=1)
    model: str | None = None
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


def to_question(name: str, q: SystemOneQuestion) -> Question:
    instructions = _text(q.instructions)
    if q.type == "choice":
        if not isinstance(q.criteria, dict) or not q.criteria:
            raise ApiError(400, f"{name}: a choice question needs `criteria` as an object of options")
        return ChoiceQuestion(name, instructions, tuple(str(k) for k in q.criteria))
    if q.type == "score":
        if not isinstance(q.criteria, list) or not q.criteria:
            raise ApiError(400, f"{name}: a score question needs `criteria` as an ordered list of levels")
        return ScoreQuestion(name, instructions, tuple(_text(level) or "" for level in q.criteria))
    if q.type == "noul":
        criteria = q.criteria if isinstance(q.criteria, dict) else {}
        return PredicateQuestion(name, instructions, _text(criteria.get("true")))
    raise ApiError(422, f"{name}: question type {q.type!r} is not one of choice, score, noul")


def _text(value: Any) -> str | None:
    return None if value is None else flatten_state(value)


def to_wire(a: ChoiceAnswer | ScoreAnswer | PredicateAnswer, min_confidence: float | None) -> dict[str, Any]:
    if isinstance(a, ChoiceAnswer):
        wire: dict[str, Any] = {
            "type": "choice",
            "choice": a.choice,
            "probabilities": a.probabilities,
            "confidence": a.confidence,
        }
    elif isinstance(a, ScoreAnswer):
        wire = {
            "type": "score",
            "score": a.score,
            "probabilities": a.probabilities,
            "legend": a.legend,
            "confidence": a.confidence,
        }
    else:
        wire = {"type": "noul", "noul": a.probability}
    if min_confidence is not None:  # Laya's abstention gate: reported on every answer when a threshold was given
        confidence = wire.get("confidence")
        if confidence is None:  # a noul carries no confidence: the gate cannot decide
            wire["abstention"] = "unevaluated"
        elif confidence < min_confidence:
            wire["abstention"] = "abstained"
            wire["low_confidence"] = True
        else:
            wire["abstention"] = "passed"
        wire["abstention_threshold"] = min_confidence
    return wire


@dataclass(frozen=True)
class Decision:
    """One classified state: its wire response and what the decision line records once the request succeeds."""

    response: dict
    outputs: Outputs
    latency_ms: float
    text: str

    def record(self, core: Core) -> None:
        record(core, ROUTE, FORMAT, self.outputs, self.latency_ms, prompt=self.text)


async def decide(core: Core, state: Any, questions: dict[str, Question], min_confidence: float | None) -> Decision:
    text = ask_of(flatten_state(state))
    outputs, latency_ms = await classify(core, text)
    answers = {name: to_wire(answer(core, outputs, q), min_confidence) for name, q in questions.items()}
    response = {
        "model": core.model_name,
        "answers": answers,
        "usage": {"input_tokens": outputs.prompt_tokens, "output_tokens": 0, "pieces": outputs.pieces},
        "routing": {
            "model": core.model_name,
            "latency_ms": round(latency_ms, 1),
            "complexity_score": outputs.complexity_score,
            "truncated": outputs.truncated,
            "pieces": outputs.pieces,
        },
    }
    return Decision(response, outputs, latency_ms, text)


def parse_questions(raw: dict[str, SystemOneQuestion]) -> dict[str, Question]:
    check_count(list(raw), MAX_QUESTIONS, "question")
    return {name: to_question(name, q) for name, q in raw.items()}


@register("systemone")
def router(_app: FastAPI) -> APIRouter:
    r = APIRouter(tags=["systemone"])

    @r.post(ROUTE)
    async def systemone(request: Request, body: SystemOneRequest) -> dict:
        core = Core.of(request)
        decision = await decide(core, body.state, parse_questions(body.questions), body.min_confidence)
        decision.record(core)
        return decision.response

    @r.post(ROUTE + "/batch")
    async def systemone_batch(request: Request, body: SystemOneBatchRequest) -> dict:
        """All or nothing: a state that fails (503, 413, 400) fails the batch, and nothing is logged or counted."""
        check_count(body.states, MAX_BATCH, "state")
        core, questions = Core.of(request), parse_questions(body.questions)
        decisions = [await decide(core, s, questions, body.min_confidence) for s in body.states]
        for decision in decisions:
            decision.record(core)
        return {"results": [d.response for d in decisions]}

    return r
