from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


IssueType = Literal["preference", "schedule_density", "route", "weather"]
IssueSeverity = Literal["low", "medium", "high"]


class EvaluationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: IssueType
    severity: IssueSeverity
    day: int | None = None
    message: str
    suggestion: str | None = None


class DayEvaluationMetric(BaseModel):
    """Transient per-day quality facts for the plan response and UI."""

    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=1)
    attraction_count: int = Field(ge=0)
    preference_coverage: float = Field(ge=0, le=1)
    route_minutes: int = Field(ge=0)
    route_segments: int = Field(ge=0)
    estimated_route_ratio: float = Field(ge=0, le=1)


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[EvaluationIssue]
    daily_metrics: list[DayEvaluationMetric] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_replanning_field(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data.pop("replan_required", None)
        return data
