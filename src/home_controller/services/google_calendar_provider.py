import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from google.auth import jwt
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import service_account
from pydantic import BaseModel, Field, SecretStr, model_validator

from home_controller.config import Settings
from home_controller.schemas.calendar import CalendarEntry
from home_controller.services.calendar_service import (
    CalendarSourceEvents,
    CalendarSourceUnavailable,
)

logger = logging.getLogger(__name__)


class _Token(BaseModel):
    access_token: SecretStr = Field(min_length=1)
    expires_in: int = Field(gt=0)


class _EventTime(BaseModel):
    day: date | None = Field(default=None, alias="date")
    dateTime: datetime | None = None
    timeZone: str | None = None

    @model_validator(mode="after")
    def validate_time(self) -> "_EventTime":
        if (self.day is None) == (self.dateTime is None):
            raise ValueError("Expected either date or dateTime")
        if self.dateTime is not None and self.dateTime.utcoffset() is None:
            if self.timeZone is None:
                raise ValueError("Event timestamp requires an offset or timezone")
            self.dateTime = self.dateTime.replace(tzinfo=ZoneInfo(self.timeZone))
        return self


class _Event(BaseModel):
    id: str = Field(min_length=1)
    summary: str | None = None
    status: str = "confirmed"
    start: _EventTime | None = None
    end: _EventTime | None = None
    location: str | None = None


class _Page(BaseModel):
    items: list[_Event] = Field(default_factory=list)
    nextPageToken: str | None = None


class GoogleCalendarProvider:
    name = "google"
    token_endpoint = "https://oauth2.googleapis.com/token"

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self.calendar_id = settings.google_calendar_id.strip()
        self.endpoint = (
            "https://www.googleapis.com/calendar/v3/calendars/"
            f"{quote(self.calendar_id, safe='')}/events"
        )
        self.token: _Token | None = None
        self.token_expires_at = datetime.min.replace(tzinfo=UTC)
        self.cached: tuple[date, date, CalendarSourceEvents] | None = None
        self.lock = asyncio.Lock()
        self.service_account_credentials: service_account.Credentials | None = None

    def _service_account_assertion(self) -> str:
        if self.service_account_credentials is None:
            self.service_account_credentials = (
                service_account.Credentials.from_service_account_file(
                    self.settings.google_calendar_service_account_file.strip()
                )
            )
        credentials = self.service_account_credentials
        issued_at = int(datetime.now(UTC).timestamp())
        return jwt.encode(
            credentials.signer,
            {
                "iss": credentials.service_account_email,
                "scope": "https://www.googleapis.com/auth/calendar.events.readonly",
                "aud": self.token_endpoint,
                "iat": issued_at,
                "exp": issued_at + 3600,
            },
        ).decode("ascii")

    async def _access_token(self) -> str:
        if self.token is None or datetime.now(UTC) >= self.token_expires_at:
            if self.settings.google_calendar_auth_method == "service_account":
                try:
                    assertion = await asyncio.to_thread(self._service_account_assertion)
                except (OSError, ValueError, GoogleAuthError) as exc:
                    raise CalendarSourceUnavailable(
                        "Google Calendar service-account key could not be loaded or signed"
                    ) from exc
                data = {
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                }
            else:
                data = {
                    "grant_type": "refresh_token",
                    "client_id": self.settings.google_calendar_client_id,
                    "client_secret": self.settings.google_calendar_client_secret.get_secret_value(),
                    "refresh_token": self.settings.google_calendar_refresh_token.get_secret_value(),
                }
            response = await self.client.post(
                self.token_endpoint,
                data=data,
                timeout=self.settings.google_calendar_timeout_seconds,
            )
            if response.is_error:
                raise CalendarSourceUnavailable(
                    f"Google Calendar authorization failed (HTTP {response.status_code})"
                )
            self.token = _Token.model_validate(response.json())
            self.token_expires_at = datetime.now(UTC) + timedelta(
                seconds=max(0, self.token.expires_in - 60)
            )
        return self.token.access_token.get_secret_value()

    async def _page(self, params: dict[str, str]) -> _Page:
        for attempt in range(2):
            token = await self._access_token()
            response = await self.client.get(
                self.endpoint,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.settings.google_calendar_timeout_seconds,
            )
            if response.status_code == 401 and attempt == 0:
                self.token = None
                continue
            if response.is_error:
                raise CalendarSourceUnavailable(
                    f"Google Calendar event request failed (HTTP {response.status_code})"
                )
            return _Page.model_validate(response.json())
        raise CalendarSourceUnavailable("Google Calendar authorization failed")

    async def events(self, start: date, end: date) -> CalendarSourceEvents:
        if start > end:
            raise ValueError("Calendar range start must not be after end")
        async with self.lock:
            cached = (
                self.cached[2]
                if self.cached is not None and self.cached[:2] == (start, end)
                else None
            )
            if cached is not None and datetime.now(UTC) - cached.source_timestamp < timedelta(
                seconds=self.settings.google_calendar_cache_ttl_seconds
            ):
                return cached
            try:
                result = await self._fetch(start, end)
            except (
                CalendarSourceUnavailable,
                httpx.HTTPError,
                ValueError,
                ZoneInfoNotFoundError,
            ) as exc:
                message = (
                    str(exc)
                    if isinstance(exc, CalendarSourceUnavailable)
                    else "Google Calendar request failed or returned invalid data"
                )
                logger.warning("Google Calendar refresh failed: %s", message)
                if cached is not None:
                    return cached.model_copy(update={"stale": True})
                raise CalendarSourceUnavailable(message) from exc
            self.cached = (start, end, result)
            return result

    async def _fetch(self, start: date, end: date) -> CalendarSourceEvents:
        timezone = ZoneInfo(self.settings.location_timezone)
        params = {
            "timeMin": datetime.combine(start, time.min, timezone).isoformat(),
            "timeMax": datetime.combine(end + timedelta(days=1), time.min, timezone).isoformat(),
            "timeZone": self.settings.location_timezone,
            "singleEvents": "true",
            "orderBy": "startTime",
            "showDeleted": "false",
            "maxResults": "2500",
        }
        entries: list[CalendarEntry] = []
        seen_tokens: set[str] = set()
        while True:
            page = await self._page(params)
            for event in page.items:
                if event.status != "cancelled":
                    entries.extend(self._entries(event, start, end, timezone))
            if not page.nextPageToken:
                break
            if page.nextPageToken in seen_tokens:
                raise ValueError("Repeated Google Calendar page token")
            seen_tokens.add(page.nextPageToken)
            params["pageToken"] = page.nextPageToken
        return CalendarSourceEvents(
            entries=entries, stale=False, source_timestamp=datetime.now(UTC)
        )

    def _entries(
        self, event: _Event, start: date, end: date, timezone: ZoneInfo
    ) -> list[CalendarEntry]:
        if event.start is None or event.end is None:
            raise ValueError("Google Calendar event is missing its start or end")
        all_day = event.start.day is not None
        starts_at = event.start.dateTime
        ends_at = event.end.dateTime
        if all_day:
            if event.start.day is None or event.end.day is None:
                raise ValueError("Inconsistent all-day event")
            first = event.start.day
            last = event.end.day - timedelta(days=1)
            if event.end.day <= first:
                raise ValueError("Invalid all-day event range")
        else:
            if starts_at is None or ends_at is None:
                raise ValueError("Inconsistent timed event")
            if ends_at.astimezone(UTC) <= starts_at.astimezone(UTC):
                raise ValueError("Invalid timed event range")
            first = starts_at.astimezone(timezone).date()
            last = (ends_at.astimezone(UTC) - timedelta(microseconds=1)).astimezone(timezone).date()
        entries = []
        day = max(start, first)
        while day <= min(end, last):
            entries.append(
                CalendarEntry(
                    id=f"google:{self.calendar_id}:{event.id}:{day.isoformat()}",
                    source=self.name,
                    kind="event",
                    date=day,
                    title=event.summary or "(Untitled event)",
                    all_day=all_day,
                    starts_at=starts_at,
                    ends_at=ends_at,
                    address=event.location,
                )
            )
            day += timedelta(days=1)
        return entries
