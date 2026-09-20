from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator


IssueType = Literal["preference", "schedule_density", "route", "weather"]
IssueSeverity = Literal["low", "medium", "high"]


class EvaluationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: IssueType
    severity: IssueSeverity
    day: int | None = None
    message: str
    suggestion: str | None = None


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    issues: list[EvaluationIssue]

    @model_validator(mode="before")
    @classmethod
    def drop_legacy_replanning_field(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data.pop("replan_required", None)
        return data
