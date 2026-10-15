from datetime import date, datetime

from pydantic import BaseModel, Field


class CollectionEntry(BaseModel):
    collection_date: date
    bins: list[str] = Field(min_length=1)


class WasteSchedule(BaseModel):
    entries: list[CollectionEntry]
    source_timestamp: datetime
    stale: bool = False
