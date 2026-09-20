from pydantic import BaseModel, ConfigDict, Field


class Province(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    adcode: str
    tags: list[str] = Field(default_factory=list)
    description: str


class City(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    province_id: int
    name: str
    adcode: str
    tags: list[str] = Field(default_factory=list)
    description: str
