from typing import Literal

from pydantic import BaseModel, ConfigDict


IssueType = Literal["preference", "schedule_density", "route", "weather", "opening_hours"]
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
    replan_required: bool
