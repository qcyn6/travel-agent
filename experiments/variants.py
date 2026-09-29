from dataclasses import dataclass


@dataclass(frozen=True)
class ExperimentVariant:
    """One deterministic scoring configuration for an offline comparison."""

    variant_id: str
    label: str
    preference_weight: float
    distance_weight: float
    semantic_weight: float | None = None
    keyword_weight: float | None = None


EXPERIMENT_VARIANTS: tuple[ExperimentVariant, ...] = (
    ExperimentVariant(
        variant_id="preference_only",
        label="偏好排序基线",
        preference_weight=1.0,
        distance_weight=0.0,
    ),
    ExperimentVariant(
        variant_id="balanced",
        label="生产算法 偏好0.4/距离0.6",
        preference_weight=0.4,
        distance_weight=0.6,
    ),
    ExperimentVariant(
        variant_id="distance_heavy",
        label="路线优先 偏好0.2/距离0.8",
        preference_weight=0.2,
        distance_weight=0.8,
    ),
)


RETRIEVAL_ABLATION_VARIANTS: tuple[ExperimentVariant, ...] = (
    ExperimentVariant(
        variant_id="semantic_only",
        label="仅语义检索",
        preference_weight=0.7,
        distance_weight=0.3,
        semantic_weight=1.0,
        keyword_weight=0.0,
    ),
    ExperimentVariant(
        variant_id="keyword_only",
        label="仅关键词检索",
        preference_weight=0.7,
        distance_weight=0.3,
        semantic_weight=0.0,
        keyword_weight=1.0,
    ),
    ExperimentVariant(
        variant_id="semantic_keyword_hybrid",
        label="语义/关键词混合 0.7/0.3",
        preference_weight=0.7,
        distance_weight=0.3,
        semantic_weight=0.7,
        keyword_weight=0.3,
    ),
)


__all__ = [
    "EXPERIMENT_VARIANTS",
    "RETRIEVAL_ABLATION_VARIANTS",
    "ExperimentVariant",
]
