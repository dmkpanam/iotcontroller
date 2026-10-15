import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import delete

from home_controller import cli
from home_controller.config import Settings
from home_controller.database import Base
from home_controller.models.device import Device


def test_list_devices(test_app, monkeypatch, capsys):
    _, factory, _, credentials = test_app
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(database_url=str(factory.kw["bind"].url)),
    )
    last_seen = datetime(2026, 10, 15, 12, tzinfo=UTC)

    async def update_device():
        async with factory() as session:
            from sqlalchemy import select

            device = await session.scalar(select(Device).where(Device.device_id == "sensor-1"))
            device.last_seen_at = last_seen
            device.location = "Kitchen"
            await session.commit()

    asyncio.run(update_device())
    asyncio.run(cli._run(cli.parser().parse_args(["list-devices"])))
    output = capsys.readouterr().out
    devices = json.loads(output)
    assert [device["device_id"] for device in devices] == [
        "disabled-device",
        "no-capabilities",
        "revoked-device",
        "sensor-1",
    ]
    assert [device["status"] for device in devices] == ["disabled", "active", "revoked", "active"]
    assert devices[1]["capabilities"] == []
    assert devices[1]["last_seen_at"] is None
    sensor = devices[-1]
    assert sensor["location"] == "Kitchen"
    assert sensor["last_seen_at"] == last_seen.isoformat()
    assert datetime.fromisoformat(sensor["created_at"]).utcoffset().total_seconds() == 0
    assert sensor["capabilities"] == sorted(sensor["capabilities"])
    assert set(sensor) == {
        "device_id",
        "name",
        "device_type",
        "status",
        "capabilities",
        "location",
        "created_at",
        "last_seen_at",
    }
    assert all(token not in output for token in credentials.values())
    asyncio.run(cli._run(cli.parser().parse_args(["show", "--device-id", "sensor-1"])))
    assert json.loads(capsys.readouterr().out) == sensor


def test_list_devices_empty_database(test_app, monkeypatch, capsys):
    _, factory, _, _ = test_app
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(database_url=str(factory.kw["bind"].url)),
    )

    async def clear_database():
        async with factory() as session:
            for table in reversed(Base.metadata.sorted_tables):
                await session.execute(delete(table))
            await session.commit()

    asyncio.run(clear_database())
    asyncio.run(cli._run(cli.parser().parse_args(["list-devices"])))
    assert json.loads(capsys.readouterr().out) == []
