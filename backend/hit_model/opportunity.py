"""Probability contract for the V3 plate-appearance opportunity layer."""

from __future__ import annotations

import math
from typing import Mapping

PA_BUCKETS = ("0", "1", "2", "3", "4", "5", "6+")


def plate_appearance_bucket(plate_appearances: int) -> str:
    """Map an observed plate-appearance count to the model's label space."""
    if plate_appearances < 0:
        raise ValueError("plate_appearances cannot be negative.")
    return str(plate_appearances) if plate_appearances <= 5 else "6+"


def validate_pa_distribution(
    probabilities: Mapping[str, float],
    *,
    tolerance: float = 1e-6,
) -> dict[str, float]:
    """Validate a complete P(PA=n) distribution without silently repairing it."""
    missing = [bucket for bucket in PA_BUCKETS if bucket not in probabilities]
    extra = [bucket for bucket in probabilities if bucket not in PA_BUCKETS]
    if missing or extra:
        raise ValueError(
            f"PA distribution must contain exactly {PA_BUCKETS}; "
            f"missing={missing}, extra={extra}."
        )
    values = {bucket: float(probabilities[bucket]) for bucket in PA_BUCKETS}
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in values.values()):
        raise ValueError("Every PA probability must be finite and between 0 and 1.")
    total = sum(values.values())
    if not math.isclose(total, 1.0, abs_tol=tolerance):
        raise ValueError(f"PA probabilities must sum to 1.0; got {total:.12f}.")
    return values


def conditional_hit_probability(
    per_pa_hit_probability: float,
    plate_appearances: float,
) -> float:
    """P(1+ hits | PA=n), assuming a constant independent per-PA hit rate."""
    if not 0 <= per_pa_hit_probability <= 1:
        raise ValueError("per_pa_hit_probability must be between 0 and 1.")
    if plate_appearances < 0:
        raise ValueError("plate_appearances cannot be negative.")
    return 1.0 - (1.0 - per_pa_hit_probability) ** plate_appearances


def marginal_hit_probability(
    pa_distribution: Mapping[str, float],
    hit_probability_given_pa: Mapping[str, float],
) -> float:
    """Combine the opportunity and contact layers over every PA bucket."""
    distribution = validate_pa_distribution(pa_distribution)
    missing = [bucket for bucket in PA_BUCKETS if bucket not in hit_probability_given_pa]
    if missing:
        raise ValueError(f"Missing conditional hit probabilities for: {missing}.")
    conditionals = {
        bucket: float(hit_probability_given_pa[bucket]) for bucket in PA_BUCKETS
    }
    if any(
        not math.isfinite(value) or value < 0 or value > 1
        for value in conditionals.values()
    ):
        raise ValueError("Conditional hit probabilities must be between 0 and 1.")
    if not math.isclose(conditionals["0"], 0.0, abs_tol=1e-12):
        raise ValueError("P(1+ hits | PA=0) must equal 0.")
    return sum(
        distribution[bucket] * conditionals[bucket] for bucket in PA_BUCKETS
    )


def marginal_hit_probability_constant_rate(
    pa_distribution: Mapping[str, float],
    per_pa_hit_probability: float,
    *,
    six_plus_representative_pa: float = 6.25,
) -> float:
    """Convenience calculation for one constant per-PA hit probability.

    The 6+ bucket needs a representative PA count. Production V3 should learn
    or validate that tail value rather than assuming 6.25 without evidence.
    """
    representative_pa = {
        **{str(pa): float(pa) for pa in range(6)},
        "6+": float(six_plus_representative_pa),
    }
    conditionals = {
        bucket: conditional_hit_probability(
            per_pa_hit_probability, representative_pa[bucket]
        )
        for bucket in PA_BUCKETS
    }
    return marginal_hit_probability(pa_distribution, conditionals)
