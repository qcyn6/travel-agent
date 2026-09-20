from pydantic import BaseModel, ConfigDict


class AttractionKnowledge(BaseModel):
    """Compatibility response model for the description stored on attractions."""

    model_config = ConfigDict(extra="forbid")

    poi_id: str
    description: str
