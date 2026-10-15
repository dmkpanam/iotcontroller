import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from home_controller.config import Settings
from home_controller.schemas.calendar import (
    CalendarDay,
    CalendarEntry,
    CalendarOutlook,
    CalendarSourceStatus,
)
from home_controller.services.waste_service import CachedWasteService, WasteUnavailable


class CalendarSourceUnavailable(Exception):
    pass


class CalendarSourceEvents(BaseModel):
    entries: list[CalendarEntry]
    stale: bool
    source_timestamp: datetime


class CalendarSource(Protocol):
    name: str

    async def events(self, start: date, end: date) -> CalendarSourceEvents: ...


class WasteCalendarSource:
    name = "waste"

    def __init__(self, waste_service: CachedWasteService, settings: Settings):
        self.waste_service = waste_service
        self.settings = settings

    async def events(self, start: date, end: date) -> CalendarSourceEvents:
        try:
            schedule = await self.waste_service.schedule()
        except WasteUnavailable as exc:
            raise CalendarSourceUnavailable(str(exc)) from exc
        grouped: dict[date, set[str]] = {}
        for collection in schedule.entries:
            grouped.setdefault(collection.collection_date, set()).update(collection.bins)
        entries: list[CalendarEntry] = []
        for collection_date, bins in sorted(grouped.items()):
            if start <= collection_date <= end:
                entries.append(
                    CalendarEntry(
                        id=f"waste:{collection_date.isoformat()}:collection",
                        source=self.name,
                        kind="collection",
                        date=collection_date,
                        title="Bin collection",
                        collection_date=collection_date,
                        bins=sorted(bins),
                        address=self.settings.waste_address or None,
                    )
                )
        return CalendarSourceEvents(
            entries=entries, stale=schedule.stale, source_timestamp=schedule.source_timestamp
        )


class CalendarService:
    def __init__(self, sources: list[CalendarSource], timezone: str):
        self.sources = sources
        self.timezone = timezone

    async def outlook(self, now: datetime | None = None) -> CalendarOutlook:
        instant = now or datetime.now(UTC)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("Calendar outlook requires a timezone-aware timestamp")
        today = instant.astimezone(ZoneInfo(self.timezone)).date()
        tomorrow = today + timedelta(days=1)
        days = [CalendarDay(date=today), CalendarDay(date=tomorrow)]

        async def load(
            source: CalendarSource,
        ) -> tuple[str, list[CalendarEntry], CalendarSourceStatus]:
            try:
                result = await source.events(today, tomorrow)
            except CalendarSourceUnavailable as exc:
                return source.name, [], CalendarSourceStatus(status="unavailable", error=str(exc))
            return (
                source.name,
                result.entries,
                CalendarSourceStatus(
                    status="available",
                    stale=result.stale,
                    source_timestamp=result.source_timestamp,
                ),
            )

        statuses: dict[str, CalendarSourceStatus] = {}
        for name, entries, status in await asyncio.gather(
            *(load(source) for source in self.sources)
        ):
            statuses[name] = status
            for day in days:
                day.entries.extend(entry for entry in entries if entry.date == day.date)
        for day in days:
            day.entries.sort(key=lambda entry: (entry.source, entry.kind, entry.id))
        return CalendarOutlook(timezone=self.timezone, days=days, sources=statuses)
