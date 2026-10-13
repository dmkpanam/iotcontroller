import json
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import ValidationError

from home_controller.config import Settings
from home_controller.schemas.waste import CollectionEntry, WasteNext


class WasteProvider(Protocol):
    async def upcoming(self) -> list[CollectionEntry]: ...


class WasteUnavailable(Exception):
    pass


class ManualWasteProvider:
    def __init__(self, schedule_json: str):
        self.schedule_json = schedule_json

    async def upcoming(self) -> list[CollectionEntry]:
        try:
            records = json.loads(self.schedule_json)
            return [CollectionEntry.model_validate(item) for item in records]
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise ValueError(
                "WASTE_MANUAL_SCHEDULE must be a JSON list of collection records"
            ) from exc


class CachedWasteService:
    def __init__(self, provider: WasteProvider, settings: Settings):
        self.provider = provider
        self.settings = settings
        self.cached_at: datetime | None = None
        self.cached_entries: list[CollectionEntry] | None = None

    async def next(self, today: date | None = None) -> WasteNext:
        now = datetime.now(UTC)
        local_today = today or datetime.now(ZoneInfo(self.settings.location_timezone)).date()
        if (
            self.cached_entries is None
            or self.cached_at is None
            or now - self.cached_at > timedelta(seconds=self.settings.waste_cache_ttl_seconds)
        ):
            try:
                self.cached_entries = await self.provider.upcoming()
                self.cached_at = now
            except (ValueError, httpx.HTTPError) as exc:
                if self.cached_entries is None or self.cached_at is None:
                    raise WasteUnavailable("Waste collection schedule is unavailable") from exc
                return self._normalise(self.cached_entries, local_today, self.cached_at, stale=True)
        return self._normalise(self.cached_entries, local_today, self.cached_at, stale=False)

    def _normalise(
        self,
        entries: list[CollectionEntry],
        local_today: date,
        cached_at: datetime,
        *,
        stale: bool,
    ) -> WasteNext:
        upcoming = sorted(
            (entry for entry in entries if entry.collection_date >= local_today),
            key=lambda entry: entry.collection_date,
        )
        if not upcoming:
            return WasteNext(
                available=bool(entries),
                address=self.settings.waste_address or None,
                source_timestamp=cached_at,
                stale=stale,
            )
        collection = upcoming[0]
        days_remaining = (collection.collection_date - local_today).days
        reminder_date = collection.collection_date - timedelta(
            days=self.settings.waste_reminder_days_before
        )
        return WasteNext(
            available=True,
            address=self.settings.waste_address or None,
            collection_date=collection.collection_date,
            bins=collection.bins,
            days_remaining=days_remaining,
            reminder_date=reminder_date,
            reminder_due=local_today == reminder_date,
            source_timestamp=cached_at,
            stale=stale,
        )


def validate_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc
