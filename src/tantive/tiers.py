"""Tier sets: parsing, matching a caller's options, calibrated probabilities and confidence (spec § 3.2, § 3.3, § 4).

A tier takes the scores up to its bound inclusive; the last tier takes everything above the previous bound. The
probability of a tier is the mass of a Gaussian centred on the score, of width sigma, falling in its interval.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

SCORE_MIN = 0.01


@dataclass(frozen=True)
class TierSet:
    names: tuple[str, ...]
    bounds: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.names) < 2 or len(self.names) != len(self.bounds):
            raise ValueError("a tier set needs at least two tiers, one bound each")
        if any(b <= a for a, b in zip(self.bounds, self.bounds[1:], strict=False)):
            raise ValueError("tier bounds must be strictly increasing")

    @classmethod
    def parse(cls, spec: str) -> TierSet:
        """`"NAME=bound,NAME=bound,..."`, bounds strictly increasing, at least two tiers."""
        names, bounds = [], []
        for item in spec.split(","):
            name, sep, bound = item.strip().partition("=")
            if not sep or not name.strip():
                raise ValueError(f"tier {item.strip()!r} is not NAME=bound")
            try:
                bounds.append(float(bound))
            except ValueError:
                raise ValueError(f"tier {name.strip()!r}: bound {bound!r} is not a number") from None
            names.append(name.strip())
        return cls(tuple(names), tuple(bounds))

    def tier_for(self, score: float) -> str:
        score = round(score, 3)
        for name, bound in zip(self.names, self.bounds, strict=True):
            if score <= bound:
                return name
        return self.names[-1]

    def intervals(self) -> list[tuple[str, float, float]]:
        lows = (0.0, *self.bounds[:-1])
        return [(n, lo, hi) for n, lo, hi in zip(self.names, lows, self.bounds, strict=True)]

    def masses(self, score: float, sigma: float) -> dict[str, float]:
        """Gaussian mass per tier (first interval open to -inf, last to +inf), unrounded."""
        cdf = [_normal_cdf((b - score) / sigma) for b in self.bounds[:-1]]
        masses = [hi - lo for lo, hi in zip([0.0, *cdf], [*cdf, 1.0], strict=True)]
        return dict(zip(self.names, masses, strict=True))

    def probabilities(self, score: float, sigma: float) -> dict[str, float]:
        """`masses`, summing to 1 after rounding to 4 decimals."""
        return normalised(self.masses(score, sigma))

    def nearest(self, score: float, among: Sequence[str] | None = None) -> str:
        """The tier whose interval is nearest the score (its own tier when it falls in one), among the tiers named
        (all by default); equidistant tiers go to the higher one."""
        score = round(score, 3)
        keep = set(self.names if among is None else among)
        own = self.tier_for(score)
        if own in keep:
            return own
        candidates = [(n, lo, hi) for n, lo, hi in self.intervals() if n in keep]
        if score > candidates[-1][2]:  # the last tier is open above
            return candidates[-1][0]
        return min(reversed(candidates), key=lambda t: max(t[1] - score, score - t[2], 0.0))[0]

    def restrict(self, names: Sequence[str]) -> TierSet:
        """The tiers named, in this set's order, with their bounds."""
        keep = set(names)
        pairs = [(n, b) for n, b in zip(self.names, self.bounds, strict=True) if n in keep]
        return TierSet(tuple(n for n, _ in pairs), tuple(b for _, b in pairs))

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.names, self.bounds, strict=True))


TRAINING_SET = TierSet.parse("TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0")
LITELLM_SET = TierSet.parse("SIMPLE=0.5,MEDIUM=0.6,COMPLEX=0.9,REASONING=1.0")


def native_set(spec: str | None) -> TierSet:
    return TierSet.parse(spec) if spec else TRAINING_SET


def match_tier_set(options: Sequence[str], native: TierSet) -> tuple[TierSet, dict[str, str]] | None:
    """The declared set a caller's options name (case-insensitive): native exact, LiteLLM exact, native subset,
    LiteLLM subset, in that order. Returns the (possibly restricted) set and `canonical name -> option as written`;
    None when there are fewer than two options, repeated ones, or a tier no set declares."""
    lowered = [o.lower() for o in options]
    if len(lowered) < 2 or len(set(lowered)) != len(lowered):  # a tier set has at least two tiers
        return None
    for candidate in (native, LITELLM_SET):
        by_lower = {n.lower(): n for n in candidate.names}
        if set(lowered) == set(by_lower):
            return candidate, {by_lower[lo]: o for lo, o in zip(lowered, options, strict=True)}
    for candidate in (native, LITELLM_SET):
        by_lower = {n.lower(): n for n in candidate.names}
        if set(lowered) <= set(by_lower):
            names = {by_lower[lo]: o for lo, o in zip(lowered, options, strict=True)}
            return candidate.restrict(list(names)), names
    return None


def resolve_tier_set(options: Sequence[str], native: TierSet) -> tuple[TierSet, TierSet, dict[str, str]] | None:
    """`match_tier_set` plus the full set the match came from: (full, present, canonical -> as written), or None."""
    matched = match_tier_set(options, native)
    if matched is None:
        return None
    present, spelling = matched
    full = native if set(present.names) <= set(native.names) else LITELLM_SET
    return full, present, spelling


def choose(score: float, sigma: float, full: TierSet, present: TierSet) -> tuple[str, dict[str, float]]:
    """The tier of the score by its bounds in `full`, or the nearest present tier when that one is absent (spec
    § 3.2); the probabilities over `full` renormalised over the tiers of `present`, as the calibration view."""
    masses = full.masses(score, sigma)
    restricted = {n: masses[n] for n in present.names}
    choice = full.nearest(score, present.names)
    if sum(restricted.values()) <= 0.0:  # every present tier is so far that its mass underflows
        restricted = {n: float(n == choice) for n in present.names}
    return choice, normalised(restricted)


def confidence(probabilities: Mapping[str, float]) -> float:
    """1 - normalised entropy: 1.0 when one tier takes everything, 0.0 when they are all equal."""
    if len(probabilities) < 2:
        return 1.0
    entropy = -sum(p * math.log(p) for p in probabilities.values() if p > 0)
    return round(max(0.0, 1 - entropy / math.log(len(probabilities))), 4)


def score_levels(k: int, native: TierSet) -> TierSet:
    """The native set when the caller's ordered scale has as many levels, else `k` uniform levels named "0".."k-1"."""
    if k == len(native.names):
        return native
    return TierSet(tuple(str(i) for i in range(k)), tuple(SCORE_MIN + (1 - SCORE_MIN) * (i + 1) / k for i in range(k)))


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def normalised(masses: dict[str, float]) -> dict[str, float]:
    """Masses scaled to sum to 1 and rounded to 4 decimals, the rounding residual going to the largest."""
    total = sum(masses.values())
    rounded = {n: round(m / total, 4) for n, m in masses.items()}
    largest = max(rounded, key=rounded.get)
    rounded[largest] = round(rounded[largest] + (1 - sum(rounded.values())), 4)
    return rounded
