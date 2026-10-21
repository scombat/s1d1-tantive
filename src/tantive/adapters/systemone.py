"""System One (TypeSafe / Laya): what LiteLLM's Auto Router sends to a self-hosted classifier (spec § 3.5).

POST /v1/systemone        {state, questions: {name: {type, instructions?, criteria?}}, model?, min_confidence?}
POST /v1/systemone/batch  {states: [...], questions}  →  {"results": [...]}, a response or an error per state
Extra fields (Laya's controls) are accepted and ignored. `min_confidence` adds Laya's abstention fields.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from tantive.adapters import register
from tantive.adapters.common import (
    MAX_BATCH,
    MAX_QUESTIONS,
    Classified,
    Core,
    answer,
    batch_status,
    check_count,
    classify_items,
    classify_text,
    item_error,
    record,
)
from tantive.ask import flatten_state
from tantive.errors import ApiError
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


def respond(core: Core, c: Classified, questions: dict[str, Question], min_confidence: float | None) -> dict:
    answers = {name: to_wire(answer(core, c.outputs, q), min_confidence) for name, q in questions.items()}
    return {
        "model": core.model_name,
        "answers": answers,
        "usage": {"input_tokens": c.outputs.prompt_tokens, "output_tokens": 0, "pieces": c.outputs.pieces},
        "routing": {
            "model": core.model_name,
            "latency_ms": round(c.latency_ms, 1),
            "complexity_score": c.outputs.complexity_score,
            "truncated": c.outputs.truncated,
            "pieces": c.outputs.pieces,
        },
    }


def record_state(core: Core, c: Classified) -> None:
    record(core, ROUTE, FORMAT, c.outputs, c.latency_ms, prompt=c.text)


def parse_questions(raw: dict[str, SystemOneQuestion]) -> dict[str, Question]:
    check_count(list(raw), MAX_QUESTIONS, "question")
    return {name: to_question(name, q) for name, q in raw.items()}


@register("systemone")
def router(_app: FastAPI) -> APIRouter:
    r = APIRouter(tags=["systemone"])

    @r.post(ROUTE)
    async def systemone(request: Request, body: SystemOneRequest) -> dict:
        core = Core.of(request)
        questions = parse_questions(body.questions)
        c = await classify_text(core, flatten_state(body.state))
        response = respond(core, c, questions, body.min_confidence)
        record_state(core, c)
        return response

    @r.post(ROUTE + "/batch")
    async def systemone_batch(request: Request, body: SystemOneBatchRequest) -> JSONResponse:
        """Per state: a state the single route would refuse (400, 413, 503) gets an error in its slot. The answers
        are built before anything is recorded, so a question outside the catalogue (422) still fails the body."""
        check_count(body.states, MAX_BATCH, "state")
        core, questions = Core.of(request), parse_questions(body.questions)
        items = await classify_items(core, [flatten_state(s) for s in body.states])
        results = [
            item_error(i) if isinstance(i, ApiError) else respond(core, i, questions, body.min_confidence)
            for i in items
        ]
        for i in items:
            if isinstance(i, Classified):
                record_state(core, i)
        return JSONResponse({"results": results}, status_code=batch_status(items))

    return r
