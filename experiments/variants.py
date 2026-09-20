from dataclasses import dataclass


@dataclass(frozen=True)
class ExperimentVariant:
    """One deterministic scoring configuration for an offline comparison."""

    variant_id: str
    label: str
    preference_weight: float
    distance_weight: float


EXPERIMENT_VARIANTS: tuple[ExperimentVariant, ...] = (
    ExperimentVariant(
        variant_id="preference_only",
        label="偏好排序基线",
        preference_weight=1.0,
        distance_weight=0.0,
    ),
    ExperimentVariant(
        variant_id="balanced",
        label="完整算法 0.7/0.3",
        preference_weight=0.7,
        distance_weight=0.3,
    ),
    ExperimentVariant(
        variant_id="distance_heavy",
        label="距离敏感 0.5/0.5",
        preference_weight=0.5,
        distance_weight=0.5,
    ),
)


__all__ = ["EXPERIMENT_VARIANTS", "ExperimentVariant"]
