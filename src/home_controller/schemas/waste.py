from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CollectionEntry(BaseModel):
    collection_date: date
    bins: list[str] = Field(min_length=1)


class WasteNext(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "available": True,
                "address": "Configured Bury address",
                "collection_date": "2026-10-16",
                "bins": ["General waste", "Recycling"],
                "days_remaining": 1,
                "reminder_date": "2026-10-15",
                "reminder_due": True,
                "source_timestamp": "2026-10-12T12:00:00Z",
                "stale": False,
            }
        }
    )

    available: bool
    address: str | None = None
    collection_date: date | None = None
    bins: list[str] = Field(default_factory=list)
    days_remaining: int | None = None
    reminder_date: date | None = None
    reminder_due: bool = False
    source_timestamp: datetime | None = None
    stale: bool = False
