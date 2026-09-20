from pydantic import BaseModel, ConfigDict, Field

class CityStay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    city: str
    days: int = Field(ge=1)
