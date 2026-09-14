from pydantic import BaseModel, ConfigDict, Field

from backend.models.city_stay import CityStay


class DestinationPlanResult(BaseModel):
    """Stable destination-planning result shared by the application contracts."""

    model_config = ConfigDict(extra="forbid")

    city_stays: list[CityStay] = Field(min_length=1, max_length=3)
    alternative_city_stays: list[CityStay] | None = None
    requires_choice: bool = False
