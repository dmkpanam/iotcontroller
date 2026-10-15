import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from home_controller.config import Settings
from home_controller.main import create_app
from home_controller.schemas.waste import CollectionEntry
from home_controller.services.calendar_service import CalendarService, WasteCalendarSource
from home_controller.services.waste_service import (
    BuryWasteProvider,
    CachedWasteService,
    ManualWasteProvider,
    WasteUnavailable,
    build_waste_provider,
)

ADDRESS = {
    "id": "620258",
    "addressLine1": "Willow Brae 46 Hunstanton Drive",
    "city": "Bury",
    "postcode": "BL8 1EG",
}
SCHEDULE = {
    "address": ADDRESS,
    "calendar": [
        {
            "date": "October 2026",
            "dates": [
                {"date": "Friday 30th", "bins": ["brown", "green"]},
                {"date": "Friday 16th", "bins": ["blue", "brown"]},
                {"date": "Friday 23rd", "bins": ["grey"]},
                {"date": "Friday 16th", "bins": ["blue"]},
            ],
        },
        {
            "date": "January 2027",
            "dates": [{"date": "Friday 1st", "bins": ["green"]}],
        },
    ],
}


def bury_settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "waste_provider": "bury",
            "waste_postcode": "bl8 1eg",
            "waste_address": " willow brae 46  Hunstanton Drive ",
            "waste_timeout_seconds": 7,
            **overrides,
        },
    )


async def test_bury_lookup_calendar_grouping_and_property_id_reuse():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        assert request.extensions["timeout"]["read"] == 7
        if request.url.path.endswith("/getProperties"):
            assert request.url.params["postcode"] == "BL81EG"
            return httpx.Response(
                200,
                json={
                    "error": None,
                    "response": [
                        {**ADDRESS, "id": "644979", "addressLine1": "44 Hunstanton Drive"},
                        ADDRESS,
                    ],
                },
            )
        assert request.url.path.endswith("/getPropertyById")
        assert request.url.params["id"] == "620258"
        return httpx.Response(200, json={"error": None, "response": SCHEDULE})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = build_waste_provider(bury_settings(), client)
        assert isinstance(provider, BuryWasteProvider)
        entries = await provider.upcoming()
        assert entries == [
            CollectionEntry(collection_date=date(2026, 10, 16), bins=["blue", "brown"]),
            CollectionEntry(collection_date=date(2026, 10, 23), bins=["grey"]),
            CollectionEntry(collection_date=date(2026, 10, 30), bins=["brown", "green"]),
            CollectionEntry(collection_date=date(2027, 1, 1), bins=["green"]),
        ]
        assert await provider.upcoming() == entries
        assert calls.count("/app-services/getProperties") == 1
        assert calls.count("/app-services/getPropertyById") == 2


@pytest.mark.parametrize(
    "addresses",
    [
        [],
        [{**ADDRESS, "addressLine1": "46 Hunstanton Drive"}],
        [{**ADDRESS, "postcode": "BL8 1AA"}],
        [ADDRESS, ADDRESS],
    ],
)
async def test_bury_rejects_missing_or_ambiguous_address(addresses):
    def handler(request):
        assert request.url.path.endswith("/getProperties")
        return httpx.Response(200, json={"error": None, "response": addresses})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="exactly one property"):
            await BuryWasteProvider(bury_settings(), client).upcoming()


@pytest.mark.parametrize(
    "payload",
    [
        {"error": "Service unavailable", "response": {}},
        {"response": {}},
        {"response": {**SCHEDULE, "calendar": []}},
        {"response": {**SCHEDULE, "calendar": [{"date": "October 2026", "dates": []}]}},
        {"response": {**SCHEDULE, "address": {**ADDRESS, "id": "wrong"}}},
        {"response": {**SCHEDULE, "address": {**ADDRESS, "addressLine1": "Wrong address"}}},
        {
            "response": {
                **SCHEDULE,
                "calendar": [
                    {"date": "Not a month", "dates": []},
                ],
            }
        },
        {
            "response": {
                **SCHEDULE,
                "calendar": [
                    {"date": "October 2026", "dates": [{"date": "Friday 32nd", "bins": ["blue"]}]},
                ],
            }
        },
        {
            "response": {
                **SCHEDULE,
                "calendar": [
                    {"date": "October 2026", "dates": [{"date": "invalid", "bins": ["blue"]}]},
                ],
            }
        },
        {
            "response": {
                **SCHEDULE,
                "calendar": [
                    {
                        "date": "October 2026",
                        "dates": [{"date": "Friday 16th", "bins": ["purple"]}],
                    },
                ],
            }
        },
        {
            "response": {
                **SCHEDULE,
                "calendar": [
                    {"date": "October 2026", "dates": [{"date": "Friday 16th", "bins": []}]},
                ],
            }
        },
    ],
)
async def test_bury_rejects_invalid_schedule(payload):
    def handler(request):
        if request.url.path.endswith("/getProperties"):
            return httpx.Response(200, json={"response": [ADDRESS]})
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            await BuryWasteProvider(bury_settings(), client).upcoming()


@pytest.mark.parametrize("failure", ["http", "timeout", "json", "schema"])
async def test_bury_failure_stale_cache_and_unavailable(failure, caplog):
    fail = False

    def handler(request):
        if fail:
            if failure == "http":
                return httpx.Response(503)
            if failure == "timeout":
                raise httpx.ReadTimeout("Council timed out", request=request)
            if failure == "json":
                return httpx.Response(200, text="<html>Unavailable</html>")
            return httpx.Response(200, json={"response": []})
        if request.url.path.endswith("/getProperties"):
            return httpx.Response(200, json={"response": [ADDRESS]})
        return httpx.Response(200, json={"response": SCHEDULE})

    settings = bury_settings(waste_cache_ttl_seconds=0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = CachedWasteService(BuryWasteProvider(settings, client), settings)
        fresh = await service.schedule()
        assert fresh.entries[0].bins == ["blue", "brown"]
        assert not fresh.stale
        fail = True
        stale = await service.schedule()
        assert stale.stale
        assert stale.entries == fresh.entries
        assert stale.source_timestamp == fresh.source_timestamp
        assert "Waste collection schedule refresh failed" in caplog.text
        with pytest.raises(WasteUnavailable):
            await CachedWasteService(BuryWasteProvider(settings, client), settings).schedule()


async def test_bury_cache_coalesces_concurrent_requests():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        if request.url.path.endswith("/getProperties"):
            return httpx.Response(200, json={"response": [ADDRESS]})
        return httpx.Response(200, json={"response": SCHEDULE})

    settings = bury_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = CachedWasteService(BuryWasteProvider(settings, client), settings)
        results = await asyncio.gather(*(service.schedule() for _ in range(5)))
        assert all(result.entries[0].bins == ["blue", "brown"] for result in results)
        assert calls == 2


@pytest.mark.parametrize(
    "overrides",
    [
        {"waste_postcode": ""},
        {"waste_address": " "},
    ],
)
def test_bury_requires_configuration(overrides):
    with pytest.raises(ValidationError, match="WASTE_POSTCODE and WASTE_ADDRESS"):
        bury_settings(**overrides)


async def test_manual_provider_remains_default():
    settings = Settings(_env_file=None, waste_provider="manual")
    async with httpx.AsyncClient() as client:
        assert isinstance(build_waste_provider(settings, client), ManualWasteProvider)


def test_app_wires_bury_provider(tmp_path):
    settings = bury_settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    app = create_app(settings)
    with TestClient(app):
        assert isinstance(app.state.waste_service.provider, BuryWasteProvider)


@pytest.mark.parametrize("fail", [False, True])
async def test_bury_calendar_endpoint(test_app, fail):
    test_client, _, token, _ = test_app
    today = datetime.now(ZoneInfo("Europe/London")).date()
    suffix = (
        "th"
        if 10 <= today.day % 100 <= 20
        else {1: "st", 2: "nd", 3: "rd"}.get(today.day % 10, "th")
    )
    schedule = {
        "address": ADDRESS,
        "calendar": [
            {
                "date": f"{BuryWasteProvider.months[today.month - 1]} {today.year}",
                "dates": [{"date": f"{today:%A} {today.day}{suffix}", "bins": ["blue", "brown"]}],
            }
        ],
    }

    def handler(request):
        if fail:
            return httpx.Response(503)
        if request.url.path.endswith("/getProperties"):
            return httpx.Response(200, json={"response": [ADDRESS]})
        return httpx.Response(200, json={"response": schedule})

    settings = bury_settings()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        test_client.app.state.waste_service = CachedWasteService(
            BuryWasteProvider(settings, client), settings
        )
        test_client.app.state.calendar_service = CalendarService(
            [WasteCalendarSource(test_client.app.state.waste_service, settings)], "Europe/London"
        )
        response = test_client.get("/api/v1/calendar", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    if fail:
        assert data["sources"]["waste"]["status"] == "unavailable"
        assert data["sources"]["waste"]["error"]
        assert all(day["entries"] == [] for day in data["days"])
    else:
        assert data["days"][0]["date"] == today.isoformat()
        assert data["days"][0]["entries"][0]["bins"] == ["blue", "brown"]
        assert data["days"][0]["entries"][0]["kind"] == "collection"
        assert data["sources"]["waste"]["status"] == "available"
        assert data["sources"]["waste"]["stale"] is False
