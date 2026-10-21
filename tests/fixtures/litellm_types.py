# Copied from BerriAI/litellm (MIT), commit c82e8244a57862feecfb48218bdd20c72c2afe6d (main, 2026-10-19), for the
# contract tests only. `LiteLLMBaseModel` / `LiteLLMPydanticObjectBase` are replaced by pydantic.BaseModel with
# extra="allow"; everything else is verbatim.
#
# - JevChoiceQuestion .. JevSystemOneResponse: litellm/router_strategy/complexity_router/jev_classifier.py
#   (what the Auto Router's `oss_classifier` validates when it calls POST /v1/systemone)
# - NoulAnswer .. DecisionsResponse: litellm/types/decisions.py (the proxy's /v1/decisions passthrough)
# - OpenAIDecision*: litellm/types/decisions.py (the OpenAI Decisions wire format)
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

JevProbability: TypeAlias = Annotated[float, Field(ge=0.0, le=1.0)]
DecisionsJSON: TypeAlias = str | Mapping[str, object] | Sequence[object]


class LiteLLMBaseModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class JevChoiceQuestion(LiteLLMBaseModel):
    model_config = ConfigDict(frozen=True)

    type: Literal["choice"] = "choice"
    instructions: str
    criteria: Mapping[str, str]


class JevSystemOneRequest(LiteLLMBaseModel):
    model_config = ConfigDict(frozen=True)

    state: str
    model: str
    questions: Mapping[str, JevChoiceQuestion]


class JevChoiceAnswer(LiteLLMBaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    type: Literal["choice"]
    choice: str
    probabilities: Mapping[str, JevProbability]
    confidence: JevProbability


class JevUsage(LiteLLMBaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0, strict=True)
    output_tokens: int = Field(default=0, ge=0, strict=True)


class JevSystemOneResponse(LiteLLMBaseModel):
    model_config = ConfigDict(frozen=True)

    model: str | None = None
    answers: Mapping[str, JevChoiceAnswer]
    classifier_cost: float | None = None
    classifier_provider: str | None = None
    usage: JevUsage | None = None


class NoulAnswer(LiteLLMBaseModel):
    type: Literal["noul"]
    noul: float

    model_config = ConfigDict(extra="allow", frozen=True)


class ChoiceAnswer(LiteLLMBaseModel):
    type: Literal["choice"]
    choice: str
    confidence: float
    probabilities: Mapping[str, float]

    model_config = ConfigDict(extra="allow", frozen=True)


class ScoreAnswer(LiteLLMBaseModel):
    type: Literal["score"]
    score: float
    confidence: float
    legend: Mapping[str, DecisionsJSON]
    probabilities: Mapping[str, float]

    model_config = ConfigDict(extra="allow", frozen=True)


DecisionAnswer: TypeAlias = Annotated[NoulAnswer | ChoiceAnswer | ScoreAnswer, Field(discriminator="type")]


class DecisionsUsage(LiteLLMBaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: Annotated[int, Field(exclude=True)] = 0
    cache_write_tokens: Annotated[int, Field(exclude=True)] = 0

    model_config = ConfigDict(extra="allow", frozen=True)


class DecisionsResponse(LiteLLMBaseModel):
    model: str | None = None
    answers: Mapping[str, DecisionAnswer]
    usage: DecisionsUsage | None = None

    model_config = ConfigDict(extra="allow", frozen=True)


class OpenAIPredicateAnswer(LiteLLMBaseModel):
    type: Literal["predicate"] = "predicate"
    name: str | None
    probability: float

    model_config = ConfigDict(frozen=True)


class OpenAIChoiceProbability(LiteLLMBaseModel):
    value: str | bool
    probability: float

    model_config = ConfigDict(frozen=True)


class OpenAIChoiceAnswer(LiteLLMBaseModel):
    type: Literal["choice"] = "choice"
    name: str | None
    choice: str | bool
    probabilities: tuple[OpenAIChoiceProbability, ...]
    confidence: float

    model_config = ConfigDict(frozen=True)


class OpenAIScoreProbability(LiteLLMBaseModel):
    value: int
    label: str
    probability: float

    model_config = ConfigDict(frozen=True)


class OpenAIScoreAnswer(LiteLLMBaseModel):
    type: Literal["score"] = "score"
    name: str | None
    score: float
    probabilities: tuple[OpenAIScoreProbability, ...]
    confidence: float

    model_config = ConfigDict(frozen=True)


class OpenAIRefusalAnswer(LiteLLMBaseModel):
    type: Literal["refusal"] = "refusal"
    name: str | None

    model_config = ConfigDict(frozen=True)


OpenAIDecisionAnswer: TypeAlias = Annotated[
    OpenAIPredicateAnswer | OpenAIChoiceAnswer | OpenAIScoreAnswer | OpenAIRefusalAnswer,
    Field(discriminator="type"),
]


class OpenAIDecisionInputTokensDetails(LiteLLMBaseModel):
    cached_tokens: int = 0
    cache_write_tokens: int = 0

    model_config = ConfigDict(frozen=True)


class OpenAIDecisionOutputTokensDetails(LiteLLMBaseModel):
    reasoning_tokens: int = 0

    model_config = ConfigDict(frozen=True)


class OpenAIDecisionUsage(LiteLLMBaseModel):
    input_tokens: int
    input_tokens_details: OpenAIDecisionInputTokensDetails = OpenAIDecisionInputTokensDetails()
    output_tokens: int
    output_tokens_details: OpenAIDecisionOutputTokensDetails = OpenAIDecisionOutputTokensDetails()
    total_tokens: int

    model_config = ConfigDict(frozen=True)


class OpenAIDecisionResponse(LiteLLMBaseModel):
    model: str
    answers: tuple[OpenAIDecisionAnswer, ...]
    usage: OpenAIDecisionUsage

    model_config = ConfigDict(extra="allow", frozen=True)
