"""OpenAI Decisions (spec § 3.5): POST /v1/decisions, for clients written against the OpenAI format without LiteLLM.

Body: {model?, input: str | [{type: "message", role: "user", content: str | [parts]}], questions: [...]}. Answers come
back in question order with `name` echoed. A question outside the catalogue is 422, never a refusal answer.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, FastAPI, Request
from pydantic import BaseModel, ConfigDict

from tantive.adapters import register
from tantive.adapters.common import Core, answer, ask_of, check_count, classify, record
from tantive.ask import text_of
from tantive.errors import ApiError
from tantive.questions import ChoiceAnswer, ChoiceQuestion, PredicateQuestion, Question, ScoreAnswer, ScoreQuestion

ROUTE, FORMAT = "/v1/decisions", "decisions"
MAX_DECISION_QUESTIONS = 128  # OpenAI's bound, as LiteLLM's types state it


class InputMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["message"] = "message"
    role: Literal["user"] = "user"
    content: Any


class ChoiceOption(BaseModel):
    model_config = ConfigDict(extra="allow")

    value: str | bool
    description: str | None = None


class ScoreLevel(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str
    description: str | None = None


class DecisionQuestion(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    name: str | None = None
    instructions: str | None = None
    choices: list[ChoiceOption] | None = None
    levels: list[ScoreLevel] | None = None


class DecisionsRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    input: str | list[InputMessage]
    questions: list[DecisionQuestion]
    model: str | None = None
    safety_identifier: str | None = None


def choice_key(value: str | bool) -> str:
    """LiteLLM's `systemone_choice_key`: booleans become the words true / false."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def to_question(i: int, q: DecisionQuestion) -> Question:
    name = q.name or f"question {i}"
    if q.type == "choice":
        if not q.choices or len(q.choices) < 2:
            raise ApiError(400, f"{name}: a choice question needs at least two `choices`")
        return ChoiceQuestion(name, q.instructions, tuple(choice_key(c.value) for c in q.choices))
    if q.type == "score":
        if not q.levels or len(q.levels) < 2:
            raise ApiError(400, f"{name}: a score question needs at least two `levels`")
        return ScoreQuestion(name, q.instructions, tuple(level.label for level in q.levels))
    if q.type == "predicate":
        return PredicateQuestion(name, q.instructions, None)
    raise ApiError(422, f"{name}: question type {q.type!r} is not one of predicate, choice, score")


def to_wire(q: DecisionQuestion, a: Any) -> dict[str, Any]:
    if isinstance(a, ChoiceAnswer):
        by_key = {choice_key(c.value): c.value for c in q.choices or ()}
        return {
            "type": "choice",
            "name": q.name,
            "choice": by_key.get(a.choice, a.choice),
            "probabilities": [{"value": by_key.get(k, k), "probability": p} for k, p in a.probabilities.items()],
            "confidence": a.confidence,
        }
    if isinstance(a, ScoreAnswer):
        return {
            "type": "score",
            "name": q.name,
            "score": a.score,
            "probabilities": [
                {"value": int(k), "label": a.legend[k], "probability": p} for k, p in a.probabilities.items()
            ],
            "confidence": a.confidence,
        }
    return {"type": "predicate", "name": q.name, "probability": a.probability}


def input_text(value: str | list[InputMessage]) -> str:
    if isinstance(value, str):
        return value
    return "\n".join(text_of(m.content) for m in value)


@register("decisions")
def router(_app: FastAPI) -> APIRouter:
    r = APIRouter(tags=["decisions"])

    @r.post(ROUTE)
    async def decisions(request: Request, body: DecisionsRequest) -> dict:
        core = Core.of(request)
        check_count(body.questions, MAX_DECISION_QUESTIONS, "question")
        questions = [to_question(i, q) for i, q in enumerate(body.questions)]
        text = ask_of(input_text(body.input))
        outputs, latency_ms = await classify(core, text)
        answers = [
            to_wire(q, answer(core, outputs, question)) for q, question in zip(body.questions, questions, strict=True)
        ]
        record(core, ROUTE, FORMAT, outputs, latency_ms, prompt=text)
        tokens = outputs.prompt_tokens
        return {
            "model": core.model_name,
            "answers": answers,
            "usage": {
                "input_tokens": tokens,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 0,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": tokens,
                "pieces": outputs.pieces,
            },
        }

    return r
