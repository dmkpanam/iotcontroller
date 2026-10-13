from datetime import date

import pytest

from home_controller.config import Settings
from home_controller.schemas.waste import CollectionEntry
from home_controller.services.waste_service import CachedWasteService


def test_health_authentication_and_device_states(test_app):
    client, _, token, credentials = test_app
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.get("/api/v1/devices/me").status_code == 401
    assert (
        client.get("/api/v1/devices/me", headers={"Authorization": f"Bearer {token}"}).json()[
            "device_id"
        ]
        == "sensor-1"
    )
    for device_id in ("disabled-device", "revoked-device"):
        response = client.get(
            "/api/v1/devices/me",
            headers={"Authorization": f"Bearer {credentials[device_id]}"},
        )
        assert response.status_code == 401
    unknown = client.get(
        "/api/v1/devices/me", headers={"Authorization": "Bearer invalid.credential"}
    )
    assert unknown.status_code == 401


def test_device_type_catalog_is_seeded(test_app):
    from sqlalchemy import select

    from home_controller.models.device import DeviceType

    _, factory, _, _ = test_app

    async def get_types():
        async with factory() as session:
            return (await session.scalars(select(DeviceType.name).order_by(DeviceType.name))).all()

    import asyncio

    assert asyncio.run(get_types()) == ["environment_sensor", "touchscreen"]


def test_provisioning_rejects_unknown_device_type(test_app, monkeypatch):
    import asyncio
    from argparse import Namespace

    from home_controller import cli
    from home_controller.config import Settings

    _, factory, _, _ = test_app
    database_url = str(factory.kw["bind"].url)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(database_url=database_url),
    )
    args = Namespace(
        command="create",
        device_id="unknown-type-device",
        name="Unknown type",
        device_type="unknown_type",
        capability=["weather.read"],
        location=None,
        description=None,
        firmware_version=None,
        hardware_model=None,
    )
    with pytest.raises(SystemExit, match="Unsupported device type"):
        asyncio.run(cli._run(args))


def test_cli_capabilities_management(test_app, monkeypatch, capsys):
    import asyncio
    import json
    from argparse import Namespace

    from home_controller import cli
    from home_controller.config import Settings

    _, factory, _, _ = test_app
    database_url = str(factory.kw["bind"].url)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(database_url=database_url),
    )

    # 1. Create a device with initial capabilities
    create_args = Namespace(
        command="create",
        device_id="viewer-01",
        name="Viewer 01",
        device_type="touchscreen",
        capability=["weather.read"],
        location=None,
        description=None,
        firmware_version=None,
        hardware_model=None,
    )
    asyncio.run(cli._run(create_args))
    capsys.readouterr()

    # 2. View capabilities using 'capabilities' command
    caps_args = Namespace(
        command="capabilities",
        device_id="viewer-01",
    )
    asyncio.run(cli._run(caps_args))
    out = capsys.readouterr().out
    assert json.loads(out) == ["weather.read"]

    # 3. Add capabilities using 'add-capability'
    add_args = Namespace(
        command="add-capability",
        device_id="viewer-01",
        capability=["waste_collection.read", "telemetry.environment.read"],
    )
    asyncio.run(cli._run(add_args))
    out = capsys.readouterr().out
    assert "Added capabilities" in out

    # Verify updated list
    asyncio.run(cli._run(caps_args))
    out = capsys.readouterr().out
    assert json.loads(out) == [
        "telemetry.environment.read",
        "waste_collection.read",
        "weather.read",
    ]

    # Adding already existing capability is a no-op without error
    add_existing_args = Namespace(
        command="add-capability",
        device_id="viewer-01",
        capability=["weather.read"],
    )
    asyncio.run(cli._run(add_existing_args))
    out = capsys.readouterr().out
    assert "No new capabilities added" in out

    # 4. Remove capability using 'remove-capability'
    remove_args = Namespace(
        command="remove-capability",
        device_id="viewer-01",
        capability="waste_collection.read",
    )
    asyncio.run(cli._run(remove_args))
    out = capsys.readouterr().out
    assert "Removed capability 'waste_collection.read'" in out

    # Verify remaining capabilities
    asyncio.run(cli._run(caps_args))
    out = capsys.readouterr().out
    assert json.loads(out) == [
        "telemetry.environment.read",
        "weather.read",
    ]

    # Removing a capability that is not present raises SystemExit
    with pytest.raises(SystemExit, match="Capability 'waste_collection.read' not found"):
        asyncio.run(cli._run(remove_args))


def test_telemetry_persists_and_is_idempotent(test_app, message):
    client, _, token, _ = test_app
    headers = {"Authorization": f"Bearer {token}"}
    first = client.post("/api/v1/telemetry", json=message, headers=headers)
    second = client.post("/api/v1/telemetry", json=message, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["data"] == {"accepted": True, "duplicate": False}
    assert second.json()["data"]["duplicate"] is True
    conflict = {
        **message,
        "payload": {"measurements": [{"metric": "temperature", "value": 20, "unit": "Cel"}]},
    }
    assert client.post("/api/v1/telemetry", json=conflict, headers=headers).status_code == 409


def test_telemetry_rejects_mismatch_values_units_and_future(test_app, message):
    client, _, token, _ = test_app
    headers = {"Authorization": f"Bearer {token}"}
    mismatch = {**message, "device_id": "other"}
    assert client.post("/api/v1/telemetry", json=mismatch, headers=headers).status_code == 403
    invalid_value = {
        **message,
        "payload": {"measurements": [{"metric": "temperature", "value": 150, "unit": "Cel"}]},
    }
    assert client.post("/api/v1/telemetry", json=invalid_value, headers=headers).status_code == 422
    invalid_unit = {
        **message,
        "payload": {"measurements": [{"metric": "temperature", "value": 20, "unit": "F"}]},
    }
    assert client.post("/api/v1/telemetry", json=invalid_unit, headers=headers).status_code == 422
    future = {**message, "timestamp": "2099-01-01T00:00:00Z"}
    assert client.post("/api/v1/telemetry", json=future, headers=headers).status_code == 422


def test_capability_is_required(test_app, message):
    client, _, _, credentials = test_app
    message = {**message, "device_id": "no-capabilities"}
    response = client.post(
        "/api/v1/telemetry",
        json=message,
        headers={"Authorization": f"Bearer {credentials['no-capabilities']}"},
    )
    assert response.status_code == 403


def test_telemetry_latest_and_environment(test_app, message):
    client, _, token, _ = test_app
    headers = {"Authorization": f"Bearer {token}"}
    assert client.post("/api/v1/telemetry", json=message, headers=headers).status_code == 200
    latest = client.get("/api/v1/devices/me/telemetry/latest", headers=headers)
    environment = client.get("/api/v1/environment/current", headers=headers)
    assert len(latest.json()["measurements"]) == 2
    assert environment.json()["status"] == "available"
    assert environment.json()["measurements"]["temperature"]["value"] == 22.4


def test_environment_is_unavailable_without_readings(test_app):
    client, _, token, _ = test_app
    response = client.get(
        "/api/v1/environment/current", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.json()["status"] == "unavailable"


def test_stale_environment_and_partial_dashboard_failures(test_app, message):
    from home_controller.services.waste_service import WasteUnavailable
    from home_controller.services.weather_service import WeatherUnavailable

    client, _, token, _ = test_app
    headers = {"Authorization": f"Bearer {token}"}
    stale_message = {**message, "timestamp": "2000-01-01T00:00:00Z"}
    assert client.post("/api/v1/telemetry", json=stale_message, headers=headers).status_code == 200
    assert client.get("/api/v1/environment/current", headers=headers).json()["status"] == "stale"
    latest = client.get("/api/v1/devices/me/telemetry/latest", headers=headers).json()
    assert all(item["stale"] for item in latest["measurements"])

    class FailingWeather:
        async def get(self, resource):
            raise WeatherUnavailable("weather is temporarily unavailable")

    class FailingWaste:
        async def next(self):
            raise WasteUnavailable("waste schedule unavailable")

    client.app.state.weather_service = FailingWeather()
    client.app.state.waste_service = FailingWaste()
    response = client.get("/api/v1/dashboard", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["weather"]["status"] == "unavailable"
    assert data["forecast"]["status"] == "unavailable"
    assert data["waste"]["status"] == "unavailable"
    assert data["environment"]["status"] == "stale"


def test_request_body_size_limit(test_app):
    client, _, _, _ = test_app
    response = client.post("/api/v1/telemetry", content=b"x" * 17000)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"


def test_credential_hashing_and_verification():
    from home_controller.models.device import Device
    from home_controller.security.provisioning import issue_credential, verify_credential

    device = Device(device_id="sensor-test", name="Test", device_type="sensor")
    credential, token = issue_credential(device)
    assert token not in credential.secret_hash
    assert verify_credential(credential, token)
    assert not verify_credential(credential, "hci_wrong.invalid")


def test_waste_reminders_use_actual_collection_date():
    class Provider:
        async def upcoming(self):
            return [CollectionEntry(collection_date=date(2026, 10, 16), bins=["General waste"])]

    import asyncio

    service = CachedWasteService(Provider(), Settings(location_timezone="Europe/London"))
    thursday_reminder = asyncio.run(service.next(today=date(2026, 10, 15)))
    wednesday_no_reminder = asyncio.run(service.next(today=date(2026, 10, 14)))
    assert thursday_reminder.reminder_due is True
    assert thursday_reminder.collection_date == date(2026, 10, 16)
    assert wednesday_no_reminder.reminder_due is False


def test_non_friday_collection_reminder():
    class Provider:
        async def upcoming(self):
            return [CollectionEntry(collection_date=date(2026, 10, 17), bins=["Food waste"])]

    import asyncio

    service = CachedWasteService(Provider(), Settings(location_timezone="Europe/London"))
    result = asyncio.run(service.next(today=date(2026, 10, 16)))
    assert result.collection_date == date(2026, 10, 17)
    assert result.reminder_due is True


def test_waste_cache_falls_back_to_stale_schedule():
    class Provider:
        fail = False

        async def upcoming(self):
            if self.fail:
                raise ValueError("schedule source is unavailable")
            return [CollectionEntry(collection_date=date(2026, 10, 16), bins=["Recycling"])]

    import asyncio

    provider = Provider()
    service = CachedWasteService(
        provider,
        Settings(location_timezone="Europe/London", waste_cache_ttl_seconds=0),
    )
    first = asyncio.run(service.next(today=date(2026, 10, 15)))
    provider.fail = True
    stale = asyncio.run(service.next(today=date(2026, 10, 15)))
    assert first.stale is False
    assert stale.stale is True
    assert stale.collection_date == date(2026, 10, 16)


def test_manual_waste_provider_normalizes_configured_schedule():
    import asyncio

    from home_controller.services.waste_service import ManualWasteProvider

    provider = ManualWasteProvider(
        '[{"collection_date":"2026-10-16","bins":["General waste","Recycling"]}]'
    )
    entries = asyncio.run(provider.upcoming())
    assert entries == [
        CollectionEntry(collection_date=date(2026, 10, 16), bins=["General waste", "Recycling"])
    ]
