from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AttractionKnowledge(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    poi_id: str
    description: str
    source_url: str | None = None
    updated_at: datetime

