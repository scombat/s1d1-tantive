import pytest

from tantive.tiers import (
    LITELLM_SET,
    TRAINING_SET,
    TierSet,
    choose,
    confidence,
    match_tier_set,
    native_set,
    score_levels,
)


def test_parse_and_pick():
    t = TierSet.parse("TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0")
    assert t.tier_for(0.4) == "LOW"
    assert t.tier_for(0.401) == "MEDIUM"
    assert t.tier_for(1.0) == "EXPERT"
    assert t.tier_for(0.01) == "TRIVIAL"


def test_tier_for_rounds_to_three_decimals_and_covers_above_last_bound():
    t = TierSet.parse("A=0.5,B=0.9")
    assert t.tier_for(0.5004) == "A"
    assert t.tier_for(0.95) == "B"


def test_parse_rejects_non_increasing():
    with pytest.raises(ValueError):
        TierSet.parse("A=0.5,B=0.4")


def test_parse_rejects_single_tier_and_garbage():
    with pytest.raises(ValueError):
        TierSet.parse("A=0.5")
    with pytest.raises(ValueError):
        TierSet.parse("A:0.5,B=1")


def test_native_set_defaults_to_training():
    assert native_set(None) == TRAINING_SET
    assert native_set("A=0.5,B=1.0").names == ("A", "B")


def test_probabilities_sum_to_one_and_peak_in_right_tier():
    p = TRAINING_SET.probabilities(0.62, 0.10)
    assert abs(sum(p.values()) - 1) < 1e-6
    assert max(p, key=p.get) == "MEDIUM"
    assert p["HIGH"] > p["LOW"]


def test_probabilities_example_from_spec():
    p = TRAINING_SET.probabilities(0.62, 0.10)
    assert 0.6 < p["MEDIUM"] < 0.8
    assert 0.2 < p["HIGH"] < 0.35
    assert p["LOW"] < 0.05


def test_intervals_open_at_both_ends():
    assert TierSet.parse("A=0.5,B=1.0").intervals() == [("A", 0.0, 0.5), ("B", 0.5, 1.0)]


def test_match_native_exact_case_insensitive():
    s, names = match_tier_set(["trivial", "low", "medium", "high", "expert"], TRAINING_SET)
    assert s == TRAINING_SET
    assert names["LOW"] == "low"


def test_match_litellm_exact():
    s, _ = match_tier_set(["SIMPLE", "MEDIUM", "COMPLEX", "REASONING"], TRAINING_SET)
    assert s == LITELLM_SET


def test_match_subset_restricts():
    s, _ = match_tier_set(["LOW", "HIGH"], TRAINING_SET)
    assert s.names == ("LOW", "HIGH")
    assert s.bounds == (0.4, 0.9)


def test_match_litellm_subset():
    s, names = match_tier_set(["reasoning", "simple"], TRAINING_SET)
    assert s.names == ("SIMPLE", "REASONING")
    assert names == {"SIMPLE": "simple", "REASONING": "reasoning"}


def test_match_superset_is_none():
    assert match_tier_set(["SIMPLE", "MEDIUM", "COMPLEX", "REASONING", "ULTRA"], TRAINING_SET) is None


def test_match_empty_is_none():
    assert match_tier_set([], TRAINING_SET) is None


def test_match_duplicate_is_none():
    assert match_tier_set(["LOW", "low"], TRAINING_SET) is None


def test_choose_renormalises_over_present_and_picks_the_nearest_present_tier():
    # 0.62 is MEDIUM, absent: HIGH (0.08 away) is nearer than LOW (0.22 away)
    choice, p = choose(0.62, 0.10, TRAINING_SET, TRAINING_SET.restrict(["LOW", "HIGH"]))
    assert set(p) == {"LOW", "HIGH"}
    assert abs(sum(p.values()) - 1) < 1e-6
    assert choice == "HIGH"


def test_choose_is_positional_not_argmax():
    # 0.52 lies in MEDIUM (0.5, 0.6] although SIMPLE carries more Gaussian mass
    choice, p = choose(0.52, 0.10, LITELLM_SET, LITELLM_SET)
    assert p["SIMPLE"] > p["MEDIUM"]
    assert choice == "MEDIUM"


def test_choose_bound_is_inclusive():
    two = TierSet.parse("A=0.5,B=1.0")
    assert choose(0.5, 0.10, two, two)[0] == "A"
    assert choose(0.501, 0.10, two, two)[0] == "B"


def test_choose_equidistant_present_tiers_goes_to_the_higher():
    # 0.55 is MEDIUM, absent; LOW ends at 0.4 and HIGH starts at 0.7: both 0.15 away
    assert choose(0.55, 0.10, TRAINING_SET, TRAINING_SET.restrict(["LOW", "HIGH"]))[0] == "HIGH"


def test_confidence_bounds():
    assert confidence({"A": 1.0, "B": 0.0}) == 1.0
    assert confidence({"A": 0.5, "B": 0.5}) == 0.0
    assert confidence({"A": 1.0}) == 1.0


def test_score_levels_uniform_and_native():
    assert score_levels(5, TRAINING_SET) == TRAINING_SET
    u = score_levels(4, TRAINING_SET)
    assert u.names == ("0", "1", "2", "3")
    assert u.bounds[-1] == pytest.approx(1.0)


def test_choose_far_from_present_tiers_picks_the_nearest():
    # TRIVIAL and LOW both carry ~0 mass at 0.85: the nearest present tier wins instead of a division by zero
    choice, p = choose(0.85, 0.10, TRAINING_SET, TRAINING_SET.restrict(["TRIVIAL", "LOW"]))
    assert choice == "LOW"
    assert p == {"TRIVIAL": 0.0, "LOW": 1.0}


def test_choose_subset_uses_unrounded_masses():
    # MEDIUM's mass at 1.0 on the LiteLLM set is ~3e-5: it must not vanish before the renormalisation
    choice, p = choose(1.0, 0.10, LITELLM_SET, LITELLM_SET.restrict(["SIMPLE", "MEDIUM"]))
    assert choice == "MEDIUM"
    assert p["MEDIUM"] > 0.99


def test_match_single_option_is_none():
    # a tier set needs two tiers; one option cannot be a subset we answer
    assert match_tier_set(["LOW"], TRAINING_SET) is None
