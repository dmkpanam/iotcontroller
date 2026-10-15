from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class CalendarEntry(BaseModel):
    id: str
    source: str
    kind: str
    date: date
    title: str
    all_day: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    collection_date: date | None = None
    bins: list[str] = Field(default_factory=list)
    address: str | None = None


class CalendarDay(BaseModel):
    date: date
    entries: list[CalendarEntry] = Field(default_factory=list)


class CalendarSourceStatus(BaseModel):
    status: Literal["available", "unavailable"]
    stale: bool = False
    source_timestamp: datetime | None = None
    error: str | None = None


class CalendarOutlook(BaseModel):
    timezone: str
    days: list[CalendarDay] = Field(min_length=2, max_length=2)
    sources: dict[str, CalendarSourceStatus]
