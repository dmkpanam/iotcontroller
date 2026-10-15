from datetime import UTC, date, datetime

import httpx
import pytest

from home_controller.config import Settings
from home_controller.schemas.calendar import CalendarEntry
from home_controller.services.calendar_service import (
    CalendarService,
    CalendarSourceEvents,
    CalendarSourceUnavailable,
    WasteCalendarSource,
)
from home_controller.services.waste_service import CachedWasteService, ManualWasteProvider


def calendar_service(schedule, **overrides):
    settings = Settings(
        _env_file=None,
        waste_provider="manual",
        waste_address="Test address",
        **overrides,
    )
    waste = CachedWasteService(ManualWasteProvider(schedule), settings)
    return CalendarService([WasteCalendarSource(waste, settings)], settings.location_timezone)


async def test_calendar_today_and_tomorrow_include_distinct_collections_only():
    service = calendar_service(
        '[{"collection_date":"2026-10-15","bins":["grey"]},'
        '{"collection_date":"2026-10-16","bins":["blue"]},'
        '{"collection_date":"2026-10-16","bins":["brown","blue"]},'
        '{"collection_date":"2026-10-17","bins":["green"]},'
        '{"collection_date":"2026-10-20","bins":["grey"]}]'
    )
    result = await service.outlook(datetime(2026, 10, 15, 12, tzinfo=UTC))
    assert result.timezone == "Europe/London"
    assert [day.date for day in result.days] == [date(2026, 10, 15), date(2026, 10, 16)]
    today, tomorrow = result.days
    assert [(entry.kind, entry.bins) for entry in today.entries] == [
        ("collection", ["grey"]),
    ]
    assert [(entry.kind, entry.bins) for entry in tomorrow.entries] == [
        ("collection", ["blue", "brown"]),
    ]
    collection = tomorrow.entries[0]
    assert collection.collection_date == date(2026, 10, 16)
    assert collection.date == date(2026, 10, 16)
    assert collection.title == "Bin collection"
    assert collection.all_day and collection.address == "Test address"
    assert tomorrow.entries[0].id == "waste:2026-10-16:collection"
    assert result.sources["waste"].status == "available"
    assert result.sources["waste"].source_timestamp is not None
    assert not result.sources["waste"].stale


@pytest.mark.parametrize(
    "instant,expected",
    [
        (datetime(2026, 10, 15, 23, 30, tzinfo=UTC), date(2026, 10, 16)),
        (datetime(2026, 12, 31, 23, 30, tzinfo=UTC), date(2026, 12, 31)),
        (datetime(2026, 3, 29, 23, 30, tzinfo=UTC), date(2026, 3, 30)),
        (datetime(2026, 10, 25, 0, 30, tzinfo=UTC), date(2026, 10, 25)),
    ],
)
async def test_calendar_local_days_and_empty_schedule(instant, expected):
    result = await calendar_service("[]").outlook(instant)
    assert len(result.days) == 2
    assert result.days[0].date == expected
    assert (result.days[1].date - result.days[0].date).days == 1
    assert all(day.entries == [] for day in result.days)
    assert result.sources["waste"].status == "available"


async def test_calendar_single_collection_only_appears_on_its_date():
    service = calendar_service(
        '[{"collection_date":"2026-10-14","bins":["grey"]},'
        '{"collection_date":"2026-10-16","bins":["blue","brown"]},'
        '{"collection_date":"2026-10-17","bins":["green"]}]',
    )
    result = await service.outlook(datetime(2026, 10, 15, 12, tzinfo=UTC))
    assert result.days[0].entries == []
    assert len(result.days[1].entries) == 1
    assert result.days[1].entries[0].id == "waste:2026-10-16:collection"
    assert result.days[1].entries[0].bins == ["blue", "brown"]


async def test_calendar_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone-aware"):
        await calendar_service("[]").outlook(datetime(2026, 10, 15))


async def test_calendar_stale_cache_and_failure_are_explicit(caplog):
    class Provider:
        fail = False

        async def upcoming(self):
            if self.fail:
                raise httpx.ReadTimeout("upstream timed out")
            return await ManualWasteProvider(
                '[{"collection_date":"2026-10-16","bins":["blue","brown"]}]'
            ).upcoming()

    settings = Settings(_env_file=None, waste_provider="manual", waste_cache_ttl_seconds=0)
    provider = Provider()
    source = WasteCalendarSource(CachedWasteService(provider, settings), settings)
    service = CalendarService([source], "Europe/London")
    now = datetime(2026, 10, 15, 12, tzinfo=UTC)
    fresh = await service.outlook(now)
    provider.fail = True
    stale = await service.outlook(now)
    assert stale.days == fresh.days
    assert stale.sources["waste"].stale
    assert stale.sources["waste"].status == "available"
    assert stale.sources["waste"].source_timestamp == fresh.sources["waste"].source_timestamp
    failed = await CalendarService(
        [WasteCalendarSource(CachedWasteService(provider, settings), settings)], "Europe/London"
    ).outlook(now)
    assert failed.sources["waste"].status == "unavailable"
    assert failed.sources["waste"].error
    assert failed.sources["waste"].source_timestamp is None
    assert all(day.entries == [] for day in failed.days)
    assert "Waste collection schedule refresh failed" in caplog.text


async def test_calendar_independent_sources_and_ordering():
    class Unavailable:
        name = "waste"

        async def events(self, start, end):
            raise CalendarSourceUnavailable("Waste temporarily unavailable")

    class Appointments:
        name = "appointments"

        async def events(self, start, end):
            return CalendarSourceEvents(
                entries=[
                    CalendarEntry(
                        id=identifier,
                        source=self.name,
                        kind="appointment",
                        date=start,
                        title=identifier,
                    )
                    for identifier in ("b", "a")
                ],
                stale=False,
                source_timestamp=datetime(2026, 10, 15, tzinfo=UTC),
            )

    result = await CalendarService([Unavailable(), Appointments()], "Europe/London").outlook(
        datetime(2026, 10, 15, 12, tzinfo=UTC)
    )
    assert result.sources["waste"].status == "unavailable"
    assert result.sources["appointments"].status == "available"
    assert [entry.id for entry in result.days[0].entries] == ["a", "b"]


def test_calendar_authentication_capabilities_and_removed_endpoint(test_app):
    client, _, token, credentials = test_app
    assert client.get("/api/v1/calendar").status_code == 401
    assert (
        client.get(
            "/api/v1/calendar",
            headers={"Authorization": f"Bearer {credentials['no-capabilities']}"},
        ).status_code
        == 403
    )
    response = client.get("/api/v1/calendar", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert len(response.json()["days"]) == 2
    assert (
        client.get("/api/v1/waste/next", headers={"Authorization": f"Bearer {token}"}).status_code
        == 404
    )
    schema = client.app.openapi()
    assert "/api/v1/calendar" in schema["paths"]
    assert "/api/v1/waste/next" not in schema["paths"]
    assert "WasteNext" not in schema["components"]["schemas"]


def test_dashboard_requires_calendar_read(test_app):
    import asyncio

    from sqlalchemy import delete

    from home_controller.models.device import DeviceCapability

    client, factory, token, _ = test_app

    async def remove_calendar_read():
        async with factory() as session:
            await session.execute(
                delete(DeviceCapability).where(DeviceCapability.capability == "calendar.read")
            )
            await session.commit()

    asyncio.run(remove_calendar_read())
    assert (
        client.get("/api/v1/dashboard", headers={"Authorization": f"Bearer {token}"}).status_code
        == 403
    )
    assert (
        client.get("/api/v1/calendar", headers={"Authorization": f"Bearer {token}"}).status_code
        == 403
    )
