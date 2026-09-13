from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ScheduleStatus = Literal["complete", "estimated", "incomplete"]


class DaySchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_time: str
    end_time: str
    visit_minutes: int = Field(ge=0)
    route_minutes: int = Field(ge=0)
    meal_minutes: int = Field(ge=0)
    buffer_minutes: int = Field(ge=0)
    total_minutes: int = Field(ge=0)
    status: ScheduleStatus
