"""Aggregate quality score (0..1) over the validation signals.

A simple weighted sum: leakage is the dominant negative signal; layout
similarity contributes; replacement coverage contributes. Tunable thresholds
may live in the YAML profiles (see ``validation.thresholds`` field).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class QualityInputs:
    leaked_originals: int
    residual_high_confidence_pii: int
    inconsistent_entities: int
    layout_similarity: float
    total_canonical_entities: int
    replacement_count: int


def compute_quality_score(inputs: QualityInputs) -> float:
    """Compute a 0..1 quality score from the given signals."""
    if inputs.total_canonical_entities == 0 and inputs.replacement_count == 0:
        # Nothing to anonymise — trivially clean.
        return 1.0

    score = 1.0

    # Leakage is the worst — heavy penalty.
    if inputs.leaked_originals > 0:
        score -= min(0.6, 0.1 * inputs.leaked_originals + 0.2)

    if inputs.residual_high_confidence_pii > 0:
        score -= min(0.4, 0.1 * inputs.residual_high_confidence_pii)

    if inputs.inconsistent_entities > 0:
        score -= min(0.3, 0.05 * inputs.inconsistent_entities)

    # Layout
    score -= max(0.0, (1.0 - inputs.layout_similarity) * 0.2)

    return max(0.0, min(1.0, score))
