import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from google.auth import jwt
from pydantic import ValidationError

from home_controller.config import Settings
from home_controller.main import create_app
from home_controller.services.calendar_service import (
    CalendarService,
    CalendarSourceUnavailable,
    WasteCalendarSource,
)
from home_controller.services.google_calendar_provider import GoogleCalendarProvider
from home_controller.services.waste_service import CachedWasteService, ManualWasteProvider
from home_controller.services.weather_service import WeatherUnavailable


def settings(**overrides):
    return Settings(
        _env_file=None,
        google_calendar_id="family/test@example.com",
        google_calendar_client_id="test-client",
        google_calendar_client_secret="test-secret",
        google_calendar_refresh_token="test-refresh",
        **overrides,
    )


def event(identifier, start, end, **extra):
    return {"id": identifier, "start": start, "end": end, **extra}


def token_response():
    return httpx.Response(200, json={"access_token": "test-access", "expires_in": 3600})


async def test_google_calendar_oauth_pagination_and_merged_outlook():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 10
        if request.url.host == "oauth2.googleapis.com":
            assert parse_qs(request.content.decode()) == {
                "grant_type": ["refresh_token"],
                "client_id": ["test-client"],
                "client_secret": ["test-secret"],
                "refresh_token": ["test-refresh"],
            }
            return token_response()
        assert request.url.raw_path.split(b"?")[0].endswith(b"/family%2Ftest%40example.com/events")
        assert request.headers["Authorization"] == "Bearer test-access"
        assert request.url.params["singleEvents"] == "true"
        assert request.url.params["showDeleted"] == "false"
        assert request.url.params["timeZone"] == "Europe/London"
        assert request.url.params["timeMin"] == "2026-10-15T00:00:00+01:00"
        assert request.url.params["timeMax"] == "2026-10-17T00:00:00+01:00"
        if "pageToken" not in request.url.params:
            return httpx.Response(
                200,
                json={
                    "items": [
                        event(
                            "trip",
                            {"date": "2026-10-14"},
                            {"date": "2026-10-17"},
                            summary="Trip",
                        ),
                        {"id": "cancelled", "status": "cancelled"},
                    ],
                    "nextPageToken": "next",
                },
            )
        assert request.url.params["pageToken"] == "next"
        return httpx.Response(
            200,
            json={
                "items": [
                    event(
                        "meeting",
                        {"dateTime": "2026-10-15T23:30:00Z"},
                        {"dateTime": "2026-10-16T01:00:00Z"},
                        summary="Meeting",
                        location="Office",
                    )
                ]
            },
        )

    config = settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleCalendarProvider(config, client)
        waste = WasteCalendarSource(
            CachedWasteService(
                ManualWasteProvider('[{"collection_date":"2026-10-16","bins":["blue"]}]'),
                config,
            ),
            config,
        )
        service = CalendarService([waste, provider], config.location_timezone)
        now = datetime(2026, 10, 15, 12, tzinfo=UTC)
        result = await service.outlook(now)
        assert [entry.title for entry in result.days[0].entries] == ["Trip"]
        assert {entry.title for entry in result.days[1].entries} == {
            "Trip",
            "Meeting",
            "Bin collection",
        }
        meeting = next(entry for entry in result.days[1].entries if entry.title == "Meeting")
        assert not meeting.all_day
        assert meeting.starts_at == datetime(2026, 10, 15, 23, 30, tzinfo=UTC)
        assert meeting.ends_at == datetime(2026, 10, 16, 1, tzinfo=UTC)
        assert meeting.address == "Office"
        assert meeting.source == "google" and meeting.kind == "event"
        assert result.sources["google"].status == "available"
        assert not result.sources["google"].stale
        assert await service.outlook(now) == result
        assert len(calls) == 3


@pytest.mark.parametrize(
    "start,end,expected_days",
    [
        ({"date": "2026-10-15"}, {"date": "2026-10-16"}, [15]),
        ({"date": "2026-10-13"}, {"date": "2026-10-15"}, []),
        (
            {"dateTime": "2026-10-15T23:00:00+01:00"},
            {"dateTime": "2026-10-16T00:00:00+01:00"},
            [15],
        ),
        (
            {"dateTime": "2026-10-15T23:00:00+01:00"},
            {"dateTime": "2026-10-16T01:00:00+01:00"},
            [15, 16],
        ),
        (
            {"dateTime": "2026-10-16T09:00:00", "timeZone": "Europe/London"},
            {"dateTime": "2026-10-16T10:00:00", "timeZone": "Europe/London"},
            [16],
        ),
    ],
)
async def test_event_boundaries(start, end, expected_days):
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return token_response()
        return httpx.Response(200, json={"items": [event("test", start, end)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GoogleCalendarProvider(settings(), client).events(
            date(2026, 10, 15), date(2026, 10, 16)
        )
        assert [entry.date.day for entry in result.entries] == expected_days
        assert all(entry.title == "(Untitled event)" for entry in result.entries)
        assert len({entry.id for entry in result.entries}) == len(result.entries)


@pytest.mark.parametrize(
    "start,end",
    [(date(2026, 3, 28), date(2026, 3, 29)), (date(2026, 10, 24), date(2026, 10, 25))],
)
async def test_query_boundaries_follow_daylight_saving(start, end):
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return token_response()
        lower = datetime.fromisoformat(request.url.params["timeMin"])
        upper = datetime.fromisoformat(request.url.params["timeMax"])
        assert lower.date() == start
        assert (upper.astimezone(UTC) - lower.astimezone(UTC)).total_seconds() in (
            47 * 3600,
            49 * 3600,
        )
        return httpx.Response(200, json={"items": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await GoogleCalendarProvider(settings(), client).events(start, end)
        assert result.entries == []


async def test_google_failure_stale_cache_is_limited_to_same_window(caplog):
    fail = False

    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return token_response()
        if fail:
            return httpx.Response(403, json={"error": "private upstream details"})
        return httpx.Response(
            200,
            json={"items": [event("day", {"date": "2026-10-15"}, {"date": "2026-10-16"})]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleCalendarProvider(settings(google_calendar_cache_ttl_seconds=0), client)
        start, end = date(2026, 10, 15), date(2026, 10, 16)
        fresh = await provider.events(start, end)
        fail = True
        stale = await provider.events(start, end)
        assert stale.stale and stale.entries == fresh.entries
        assert stale.source_timestamp == fresh.source_timestamp
        with pytest.raises(CalendarSourceUnavailable, match="HTTP 403"):
            await provider.events(end, date(2026, 10, 17))
    assert "Google Calendar refresh failed" in caplog.text
    assert "private upstream details" not in caplog.text


@pytest.mark.parametrize("expired", [False, True])
async def test_access_token_refresh(expired):
    tokens = 0
    requests = 0

    def handler(request):
        nonlocal tokens, requests
        if request.url.host == "oauth2.googleapis.com":
            tokens += 1
            return token_response()
        requests += 1
        if not expired and requests == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"items": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleCalendarProvider(settings(google_calendar_cache_ttl_seconds=0), client)
        start, end = date(2026, 10, 15), date(2026, 10, 16)
        await provider.events(start, end)
        if expired:
            provider.token_expires_at = datetime.min.replace(tzinfo=UTC)
            await provider.events(start, end)
        assert tokens == 2


@pytest.mark.parametrize(
    "failure",
    ["token", "timeout", "invalid_json", "invalid_event", "repeated_page", "unauthorized"],
)
async def test_failures_are_explicit_and_do_not_hide_waste(failure, caplog):
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            if failure == "token":
                return httpx.Response(400, json={"error": "secret-token-details"})
            return token_response()
        if failure == "timeout":
            raise httpx.ReadTimeout("secret-token-details")
        if failure == "invalid_json":
            return httpx.Response(200, text="not JSON")
        if failure == "invalid_event":
            return httpx.Response(200, json={"items": [{"id": "broken"}]})
        if failure == "unauthorized":
            return httpx.Response(401)
        return httpx.Response(200, json={"items": [], "nextPageToken": "same"})

    config = settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        waste = WasteCalendarSource(CachedWasteService(ManualWasteProvider("[]"), config), config)
        service = CalendarService(
            [GoogleCalendarProvider(config, client), waste], config.location_timezone
        )
        result = await service.outlook(datetime(2026, 10, 15, tzinfo=UTC))
        assert result.sources["google"].status == "unavailable"
        assert result.sources["google"].error
        assert result.sources["waste"].status == "available"
        assert all(day.entries == [] for day in result.days)
        assert "secret-token-details" not in result.model_dump_json()
        assert "secret-token-details" not in caplog.text


async def test_google_events_reach_calendar_and_dashboard(test_app):
    client, _, token, _ = test_app
    today = datetime.now(ZoneInfo("Europe/London")).date()

    class UnavailableWeather:
        async def get(self, kind):
            raise WeatherUnavailable("Weather is not configured in this test")

    client.app.state.weather_service = UnavailableWeather()

    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return token_response()
        return httpx.Response(
            200,
            json={
                "items": [
                    event(
                        "appointment",
                        {"date": today.isoformat()},
                        {"date": (today + timedelta(days=1)).isoformat()},
                        summary="Family appointment",
                    )
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as google_client:
        client.app.state.calendar_service.sources.append(
            GoogleCalendarProvider(settings(), google_client)
        )
        headers = {"Authorization": f"Bearer {token}"}
        calendar = client.get("/api/v1/calendar", headers=headers)
        dashboard = client.get("/api/v1/dashboard", headers=headers)
        assert calendar.status_code == dashboard.status_code == 200
        for payload in (calendar.json(), dashboard.json()["calendar"]):
            assert payload["sources"]["google"]["status"] == "available"
            assert payload["sources"]["waste"]["status"] == "available"
            appointment = next(
                entry for entry in payload["days"][0]["entries"] if entry["source"] == "google"
            )
            assert appointment["title"] == "Family appointment"
            assert appointment["starts_at"] is None and appointment["ends_at"] is None


def test_google_configuration_requires_credentials_and_redacts_secrets():
    with pytest.raises(ValidationError, match="GOOGLE_CALENDAR_CLIENT_ID"):
        Settings(_env_file=None, google_calendar_id="selected-calendar")
    config = settings()
    assert "test-secret" not in repr(config)
    assert "test-refresh" not in config.model_dump_json()

    with pytest.raises(ValidationError, match="GOOGLE_CALENDAR_SERVICE_ACCOUNT_FILE"):
        Settings(
            _env_file=None,
            google_calendar_id="selected-calendar",
            google_calendar_auth_method="service_account",
        )
    service_config = Settings(
        _env_file=None,
        google_calendar_id="selected-calendar",
        google_calendar_auth_method="service_account",
        google_calendar_service_account_file="/private/calendar-key.json",
    )
    assert not service_config.google_calendar_refresh_token.get_secret_value()


@pytest.mark.parametrize("refresh_reason", ["expiry", "unauthorized"])
async def test_service_account_signed_authentication_without_refresh_token(
    tmp_path, refresh_reason
):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_key = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_file = tmp_path / "service-account.json"
    key_file.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "calendar-reader@example.iam.gserviceaccount.com",
                "private_key": private_key,
                "private_key_id": "test-key",
                "token_uri": GoogleCalendarProvider.token_endpoint,
            }
        )
    )
    config = Settings(
        _env_file=None,
        google_calendar_id="shared-calendar",
        google_calendar_auth_method="service_account",
        google_calendar_service_account_file=str(key_file),
        google_calendar_cache_ttl_seconds=0,
    )
    token_calls = 0
    event_calls = 0

    def handler(request):
        nonlocal token_calls, event_calls
        if request.url.host == "oauth2.googleapis.com":
            token_calls += 1
            form = parse_qs(request.content.decode())
            assert set(form) == {"grant_type", "assertion"}
            assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
            claims = jwt.decode(
                form["assertion"][0],
                certs=public_key,
                audience=GoogleCalendarProvider.token_endpoint,
            )
            assert claims["iss"] == "calendar-reader@example.iam.gserviceaccount.com"
            assert claims["scope"] == "https://www.googleapis.com/auth/calendar.events.readonly"
            assert claims["exp"] - claims["iat"] == 3600
            assert "sub" not in claims
            return token_response()
        event_calls += 1
        assert request.headers["Authorization"] == "Bearer test-access"
        if refresh_reason == "unauthorized" and event_calls == 1:
            return httpx.Response(401)
        return httpx.Response(
            200,
            json={"items": [event("shared", {"date": "2026-10-15"}, {"date": "2026-10-16"})]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GoogleCalendarProvider(config, client)
        first = await provider.events(date(2026, 10, 15), date(2026, 10, 16))
        assert len(first.entries) == 1
        if refresh_reason == "expiry":
            provider.token_expires_at = datetime.min.replace(tzinfo=UTC)
        second = await provider.events(date(2026, 10, 15), date(2026, 10, 16))
        assert second.entries == first.entries
        assert token_calls == 2


@pytest.mark.parametrize("key_contents", [None, "not JSON", '{"private_key": "private-details"}'])
async def test_service_account_key_failures_are_sanitized(tmp_path, key_contents, caplog):
    key_file = tmp_path / "invalid-key.json"
    if key_contents is not None:
        key_file.write_text(key_contents)
    config = Settings(
        _env_file=None,
        google_calendar_id="selected-calendar",
        google_calendar_auth_method="service_account",
        google_calendar_service_account_file=str(key_file),
    )

    def handler(request):
        pytest.fail("Invalid key must not issue a network request")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = CalendarService([GoogleCalendarProvider(config, client)], "Europe/London")
        result = await service.outlook(datetime(2026, 10, 15, tzinfo=UTC))
        assert result.sources["google"].status == "unavailable"
        assert "service-account key" in result.sources["google"].error
        assert "private-details" not in result.model_dump_json()
        assert str(key_file) not in caplog.text
        assert "private-details" not in caplog.text


@pytest.mark.parametrize("enabled", [False, True])
def test_application_registers_google_only_when_configured(tmp_path, enabled):
    config = (
        settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        if enabled
        else Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    )
    with TestClient(create_app(config)) as client:
        assert [source.name for source in client.app.state.calendar_service.sources] == (
            ["waste", "google"] if enabled else ["waste"]
        )
