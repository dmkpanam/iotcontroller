import asyncio
import json
import logging
import re
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from home_controller.config import Settings
from home_controller.schemas.waste import CollectionEntry, WasteSchedule


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


class _BuryAddress(BaseModel):
    id: str = Field(min_length=1)
    addressLine1: str
    postcode: str


class _BuryDay(BaseModel):
    date: str
    bins: list[str] = Field(min_length=1)


class _BuryMonth(BaseModel):
    date: str
    dates: list[_BuryDay]


class _BurySchedule(BaseModel):
    address: _BuryAddress
    calendar: list[_BuryMonth] = Field(min_length=1)


class BuryWasteProvider:
    endpoint = "https://www.bury.gov.uk/app-services"
    months = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    colours = frozenset({"grey", "brown", "green", "blue"})

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self.property_id: str | None = None

    @staticmethod
    def _normalise_address(value: str) -> str:
        return " ".join(value.casefold().split())

    def _matches(self, address: _BuryAddress) -> bool:
        return self._normalise_address(address.addressLine1) == self._normalise_address(
            self.settings.waste_address
        ) and "".join(address.postcode.upper().split()) == "".join(
            self.settings.waste_postcode.upper().split()
        )

    async def _fetch(self, route: str, params: dict[str, str]) -> object:
        response = await self.client.get(
            f"{self.endpoint}/{route}",
            params=params,
            timeout=self.settings.waste_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error") or "response" not in payload:
            raise ValueError("Bury Council returned an invalid or unsuccessful response")
        return payload["response"]

    async def upcoming(self) -> list[CollectionEntry]:
        if self.property_id is None:
            records = await self._fetch(
                "getProperties",
                {"postcode": "".join(self.settings.waste_postcode.upper().split())},
            )
            addresses = TypeAdapter(list[_BuryAddress]).validate_python(records)
            matches = [address for address in addresses if self._matches(address)]
            if len(matches) != 1:
                raise ValueError("Bury Council address lookup must match exactly one property")
            self.property_id = matches[0].id

        schedule = _BurySchedule.model_validate(
            await self._fetch("getPropertyById", {"id": self.property_id})
        )
        if schedule.address.id != self.property_id or not self._matches(schedule.address):
            self.property_id = None
            raise ValueError("Bury Council returned a schedule for a different address")

        grouped: dict[date, set[str]] = {}
        for month in schedule.calendar:
            month_match = re.fullmatch(r"([A-Za-z]+) (\d{4})", month.date)
            if month_match is None or month_match[1] not in self.months:
                raise ValueError("Bury Council returned an invalid calendar month")
            for day in month.dates:
                day_match = re.fullmatch(
                    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday) "
                    r"(\d{1,2})(st|nd|rd|th)",
                    day.date,
                )
                if day_match is None:
                    raise ValueError("Bury Council returned an invalid collection day")
                collection_date = date(
                    int(month_match[2]), self.months.index(month_match[1]) + 1, int(day_match[2])
                )
                if not set(day.bins) <= self.colours:
                    raise ValueError("Bury Council returned an unknown bin colour")
                grouped.setdefault(collection_date, set()).update(day.bins)
        if not grouped:
            raise ValueError("Bury Council returned an empty collection calendar")
        return [
            CollectionEntry(collection_date=day, bins=sorted(bins))
            for day, bins in sorted(grouped.items())
        ]


def build_waste_provider(settings: Settings, client: httpx.AsyncClient) -> WasteProvider:
    if settings.waste_provider == "bury":
        return BuryWasteProvider(settings, client)
    return ManualWasteProvider(settings.waste_manual_schedule)


class CachedWasteService:
    def __init__(self, provider: WasteProvider, settings: Settings):
        self.provider = provider
        self.settings = settings
        self.cached_at: datetime | None = None
        self.cached_entries: list[CollectionEntry] | None = None
        self.lock = asyncio.Lock()

    async def schedule(self) -> WasteSchedule:
        async with self.lock:
            return await self._schedule()

    async def _schedule(self) -> WasteSchedule:
        now = datetime.now(UTC)
        if (
            self.cached_entries is None
            or self.cached_at is None
            or now - self.cached_at > timedelta(seconds=self.settings.waste_cache_ttl_seconds)
        ):
            try:
                self.cached_entries = await self.provider.upcoming()
                self.cached_at = now
            except (ValueError, httpx.HTTPError) as exc:
                logging.getLogger("home_controller").warning(
                    "Waste collection schedule refresh failed", exc_info=True
                )
                if self.cached_entries is None or self.cached_at is None:
                    raise WasteUnavailable("Waste collection schedule is unavailable") from exc
                return WasteSchedule(
                    entries=self.cached_entries, source_timestamp=self.cached_at, stale=True
                )
        return WasteSchedule(entries=self.cached_entries, source_timestamp=self.cached_at)


def validate_timezone(name: str) -> None:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {name}") from exc
