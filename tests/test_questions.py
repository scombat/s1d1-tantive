import pytest

from tantive.questions import (
    Catalogue,
    ChoiceQuestion,
    PredicateQuestion,
    ScoreQuestion,
    UnknownQuestion,
)
from tantive.tiers import TRAINING_SET
from tests.conftest import outputs

cat = Catalogue(TRAINING_SET, 0.10)
out = outputs(score=0.62, task="coding_debugging", tools=0.2)


def test_tier_choice_litellm_names():
    a = cat.answer(out, ChoiceQuestion("tier", None, ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")))
    assert a.choice == "COMPLEX"
    assert set(a.probabilities) == {"SIMPLE", "MEDIUM", "COMPLEX", "REASONING"}
    assert 0 <= a.confidence <= 1


def test_tier_choice_is_the_tier_of_the_score():
    a = cat.answer(outputs(score=0.52), ChoiceQuestion("tier", None, ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")))
    assert a.choice == "MEDIUM"
    assert a.probabilities["SIMPLE"] > a.probabilities["MEDIUM"]


def test_tier_choice_keeps_caller_spelling():
    a = cat.answer(out, ChoiceQuestion("tier", None, ("low", "medium", "high")))
    assert a.choice == "medium"
    assert set(a.probabilities) == {"low", "medium", "high"}
    assert abs(sum(a.probabilities.values()) - 1) < 1e-6


def test_unknown_tier_set_422():
    with pytest.raises(UnknownQuestion) as e:
        cat.answer(out, ChoiceQuestion("tier", None, ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING", "ULTRA")))
    assert "ULTRA" in e.value.reason
    assert e.value.name == "tier"


def test_task_choice_subset():
    a = cat.answer(out, ChoiceQuestion("task", None, ("coding_debugging", "writing", "Math")))
    assert a.choice == "coding_debugging"
    assert set(a.probabilities) == {"coding_debugging", "writing", "Math"}
    assert abs(sum(a.probabilities.values()) - 1) < 1e-6


def test_choice_mixing_tiers_and_tasks_is_unknown():
    with pytest.raises(UnknownQuestion):
        cat.answer(out, ChoiceQuestion("x", None, ("LOW", "writing")))


def test_score_native_levels():
    a = cat.answer(out, ScoreQuestion("level", None, ("trivial", "low", "medium", "high", "expert")))
    assert 1.5 < a.score < 3.0
    assert a.legend["2"] == "medium"
    assert set(a.probabilities) == {"0", "1", "2", "3", "4"}


def test_score_three_levels_uniform():
    a = cat.answer(out, ScoreQuestion("level", None, ("easy", "mid", "hard")))
    assert max(a.probabilities, key=a.probabilities.get) == "1"
    assert a.legend == {"0": "easy", "1": "mid", "2": "hard"}


def test_score_needs_two_to_ten_levels():
    with pytest.raises(UnknownQuestion):
        cat.answer(out, ScoreQuestion("level", None, ("only",)))
    with pytest.raises(UnknownQuestion):
        cat.answer(out, ScoreQuestion("level", None, tuple(str(i) for i in range(11))))


def test_predicate_by_name_and_by_instructions():
    assert cat.answer(out, PredicateQuestion("needs_tools", None, None)).probability == 0.2
    assert cat.answer(out, PredicateQuestion("q", "Does this need a tool call?", None)).probability == 0.2
    assert cat.answer(out, PredicateQuestion("q", None, "The request needs external tools")).probability == 0.2


def test_predicate_unknown():
    with pytest.raises(UnknownQuestion):
        cat.answer(out, PredicateQuestion("q", "Is the customer angry?", None))


def test_catalogue_text_lists_four_kinds():
    text = UnknownQuestion("q", "x").catalogue()
    assert "needs_tools" in text
    assert "score" in text


def test_subset_tier_question_warns_in_the_log(capsys):
    from tantive.telemetry import configure_logging

    configure_logging(False)
    cat.answer(out, ChoiceQuestion("tier", None, ("LOW", "HIGH")))
    line = capsys.readouterr().out
    assert '"event": "tier_subset"' in line
    assert '"level": "warning"' in line
    assert "LOW" in line and "MEDIUM" in line


def test_full_set_tier_question_does_not_warn(capsys):
    from tantive.telemetry import configure_logging

    configure_logging(False)
    cat.answer(out, ChoiceQuestion("tier", None, ("SIMPLE", "MEDIUM", "COMPLEX", "REASONING")))
    assert "tier_subset" not in capsys.readouterr().out


def test_subset_warning_is_logged_once_per_option_set(capsys):
    from tantive.telemetry import configure_logging

    configure_logging(False)
    fresh = Catalogue(TRAINING_SET, 0.10)
    for _ in range(2):
        fresh.answer(out, ChoiceQuestion("tier", None, ("LOW", "HIGH")))
    fresh.answer(out, ChoiceQuestion("tier", None, ("high", "low")))  # same set, other spelling and order
    fresh.answer(out, ChoiceQuestion("tier", None, ("LOW", "MEDIUM")))  # another set: warned once too
    lines = [line for line in capsys.readouterr().out.splitlines() if "tier_subset" in line]
    assert len(lines) == 2
