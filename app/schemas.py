from pydantic import BaseModel, Field


class DensityIn(BaseModel):
    point: str = Field(default="Новая точка", max_length=128)
    couriers: int = Field(default=0, ge=0)
    wait: int = Field(default=0, ge=0)
    lat: float | None = None
    lng: float | None = None


class RequestIn(BaseModel):
    type: str = Field(default="Смены", max_length=32)
    area: str = Field(default="", max_length=128)


class MenteeIn(BaseModel):
    name: str = Field(default="Новичок", max_length=64)
    handle: str = Field(default="newbie", max_length=64)


class StepIn(BaseModel):
    step: str


class FlagIn(BaseModel):
    flag: str          # week2 | d30
    value: bool


class FeedbackIn(BaseModel):
    rating: int = Field(default=0, ge=0, le=5)
    text: str = Field(default="", max_length=1000)
