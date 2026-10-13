import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from home_controller.config import Settings
from home_controller.database import Base
from home_controller.main import create_app
from home_controller.models.device import Device, DeviceType
from home_controller.security.provisioning import add_capabilities, issue_credential


@pytest.fixture
def test_app(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        environment_source_device_id="sensor-1",
    )
    app = create_app(settings)
    app.state.engine = engine
    app.state.session_factory = factory
    credentials = {}

    async def prepare():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            session.add_all(
                [
                    DeviceType(
                        name="environment_sensor",
                        description=(
                            "Publishes environmental measurements such as temperature and humidity."
                        ),
                    ),
                    DeviceType(
                        name="touchscreen",
                        description=(
                            "Reads environment, weather, waste collection, and dashboard data."
                        ),
                    ),
                ]
            )
            devices = (
                (
                    "sensor-1",
                    "active",
                    [
                        "telemetry.temperature.publish",
                        "telemetry.humidity.publish",
                        "telemetry.environment.read",
                        "weather.read",
                        "waste_collection.read",
                    ],
                ),
                ("no-capabilities", "active", []),
                ("disabled-device", "disabled", ["weather.read"]),
                ("revoked-device", "revoked", ["weather.read"]),
            )
            for device_id, status, capabilities in devices:
                device = Device(
                    device_id=device_id,
                    name=device_id,
                    device_type="environment_sensor",
                    status=status,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                add_capabilities(device, capabilities)
                credential, token = issue_credential(device)
                credentials[device_id] = token
                if device_id == "revoked-device":
                    credential.revoked_at = datetime.now(UTC)
                session.add(device)
                session.add(credential)
            await session.commit()

    asyncio.run(prepare())
    with TestClient(app) as client:
        yield client, factory, credentials["sensor-1"], credentials
    asyncio.run(engine.dispose())


@pytest.fixture
def message() -> dict:
    return {
        "protocol_version": "1.0",
        "message_id": str(uuid4()),
        "device_id": "sensor-1",
        "message_type": "telemetry.publish",
        "timestamp": datetime.now(UTC).isoformat(),
        "payload": {
            "measurements": [
                {"metric": "temperature", "value": 22.4, "unit": "Cel"},
                {"metric": "relative_humidity", "value": 56.2, "unit": "%"},
            ]
        },
    }
