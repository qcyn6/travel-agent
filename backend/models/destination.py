from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.models.city_stay import CityStay


class DestinationPlanResult(BaseModel):
    """Stable destination-planning result shared by the application contracts."""

    model_config = ConfigDict(extra="forbid")

    city_stays: list[CityStay] = Field(min_length=1, max_length=3)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_checkpoint(cls, value):
        """Old saved destinations may carry retired choice metadata."""
        if isinstance(value, dict):
            return {key: item for key, item in value.items()
                    if key not in {"alternative_city_stays", "requires_choice"}}
        return value
