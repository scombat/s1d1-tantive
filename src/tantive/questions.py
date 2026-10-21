"""The catalogue of questions the service can answer about a text (spec § 3.2), independent of any wire format.

A question is recognised by its type and its options: a `choice` over a declared tier set (or a subset of one) or
over task names; a `score` over 2 to 10 ordered levels (the native tiers when the count matches); a predicate about
tools. Anything else raises UnknownQuestion: the service never invents an answer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from tantive.model import Outputs
from tantive.tasks import TASKS
from tantive.telemetry import log_event
from tantive.tiers import TierSet, choose, confidence, normalised, resolve_tier_set, score_levels

TOOL_QUESTION_NAMES = frozenset({"needs_tools", "tools", "tool_use"})
SCORE_LEVELS_MIN, SCORE_LEVELS_MAX = 2, 10


@dataclass(frozen=True)
class ChoiceQuestion:
    name: str
    instructions: str | None
    options: tuple[str, ...]


@dataclass(frozen=True)
class ScoreQuestion:
    name: str
    instructions: str | None
    levels: tuple[str, ...]


@dataclass(frozen=True)
class PredicateQuestion:
    name: str
    instructions: str | None
    criteria_true: str | None


Question = ChoiceQuestion | ScoreQuestion | PredicateQuestion


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    probabilities: dict[str, float]  # keyed by the options as the caller wrote them
    confidence: float


@dataclass(frozen=True)
class ScoreAnswer:
    score: float  # expected index, fractional
    probabilities: dict[str, float]  # keyed "0".."k-1"
    legend: dict[str, str]  # index -> level text
    confidence: float


@dataclass(frozen=True)
class PredicateAnswer:
    probability: float


Answer = ChoiceAnswer | ScoreAnswer | PredicateAnswer


class UnknownQuestion(ValueError):
    """The request is valid but this question is outside the catalogue (HTTP 422)."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"{name}: {reason}")
        self.name = name
        self.reason = reason

    @staticmethod
    def catalogue() -> str:
        return (
            "This service answers: a choice over a declared tier set (or a subset of one); a choice over task "
            f"names ({', '.join(TASKS)}); a score over 2 to 10 ordered levels; a predicate about tool use "
            "(question named needs_tools, tools or tool_use, or whose instructions mention a tool)."
        )


@dataclass(frozen=True)
class Catalogue:
    native: TierSet
    sigma: float
    _warned_subsets: set[frozenset[str]] = field(default_factory=set, repr=False, compare=False)

    def answer(self, outputs: Outputs, q: Question) -> Answer:
        if isinstance(q, ChoiceQuestion):
            return self._choice(outputs, q)
        if isinstance(q, ScoreQuestion):
            return self._score(outputs, q)
        return self._predicate(outputs, q)

    def _choice(self, outputs: Outputs, q: ChoiceQuestion) -> ChoiceAnswer:
        matched = resolve_tier_set(q.options, self.native)
        if matched is not None:
            full, present, spelling = matched
            if present != full and frozenset(present.names) not in self._warned_subsets:  # once per option set
                self._warned_subsets.add(frozenset(present.names))
                log_event(
                    "tier_subset",
                    level=logging.WARNING,
                    question=q.name,
                    present=list(present.names),
                    missing=[n for n in full.names if n not in present.names],
                )
            choice, probabilities = choose(outputs.complexity_score, self.sigma, full, present)
            spelled = {spelling[name]: p for name, p in probabilities.items()}
            return ChoiceAnswer(spelling[choice], spelled, confidence(spelled))
        by_lower = {t.lower(): t for t in TASKS}
        lowered = [o.lower() for o in q.options]
        if q.options and len(set(lowered)) == len(lowered) and all(o in by_lower for o in lowered):
            all_probabilities = outputs.task_probabilities
            masses = {o: all_probabilities[by_lower[lo]] for o, lo in zip(q.options, lowered, strict=True)}
            probabilities = normalised(masses)
            choice = max(q.options, key=probabilities.get)
            return ChoiceAnswer(choice, probabilities, confidence(probabilities))
        raise UnknownQuestion(q.name, self._choice_reason(q.options))

    def _choice_reason(self, options: tuple[str, ...]) -> str:
        from tantive.tiers import LITELLM_SET

        known = {n.lower() for n in (*self.native.names, *LITELLM_SET.names, *TASKS)}
        unknown = [o for o in options if o.lower() not in known]
        if len(options) < 2 and all(o.lower() not in {t.lower() for t in TASKS} for o in options):
            return "a tier choice needs at least two options"
        if unknown:
            return f"option(s) {', '.join(unknown)} belong to no declared tier set and are not task names"
        return f"options {', '.join(options)} are neither one tier set nor a list of task names"

    def _score(self, outputs: Outputs, q: ScoreQuestion) -> ScoreAnswer:
        k = len(q.levels)
        if not SCORE_LEVELS_MIN <= k <= SCORE_LEVELS_MAX:
            raise UnknownQuestion(q.name, f"a score question needs {SCORE_LEVELS_MIN} to {SCORE_LEVELS_MAX} levels")
        levels = score_levels(k, self.native)
        by_level = levels.probabilities(outputs.complexity_score, self.sigma)
        probabilities = {str(i): by_level[name] for i, name in enumerate(levels.names)}
        score = round(sum(i * p for i, p in enumerate(probabilities.values())), 4)
        legend = {str(i): text for i, text in enumerate(q.levels)}
        return ScoreAnswer(score, probabilities, legend, confidence(probabilities))

    @staticmethod
    def _predicate(outputs: Outputs, q: PredicateQuestion) -> PredicateAnswer:
        about_tools = q.name.lower() in TOOL_QUESTION_NAMES or any(
            "tool" in (text or "").lower() for text in (q.instructions, q.criteria_true)
        )
        if not about_tools:
            raise UnknownQuestion(q.name, "the only predicate the model answers is about tool use")
        return PredicateAnswer(outputs.needs_tools_probability)
