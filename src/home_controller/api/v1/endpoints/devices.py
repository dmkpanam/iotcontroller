from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from home_controller.database import as_utc, get_session
from home_controller.models.device import Device
from home_controller.models.telemetry import Telemetry
from home_controller.schemas.device import (
    DeviceResponse,
    LatestMeasurement,
    LatestTelemetryResponse,
)
from home_controller.schemas.envelope import ERROR_RESPONSES
from home_controller.security.authentication import get_current_device, require_capability

router = APIRouter(prefix="/api/v1", tags=["devices"])


@router.get(
    "/devices/me",
    response_model=DeviceResponse,
    responses=ERROR_RESPONSES,
    description="Returns the authenticated device's own metadata and provisioned capabilities.",
)
async def me(device: Device = Depends(get_current_device)) -> DeviceResponse:
    return _device_response(device)


@router.get(
    "/devices/me/telemetry/latest",
    response_model=LatestTelemetryResponse,
    responses=ERROR_RESPONSES,
    description="Requires telemetry.environment.read and returns this device's latest readings.",
)
async def latest_telemetry(
    request: Request,
    device: Device = Depends(require_capability("telemetry.environment.read")),
    session: AsyncSession = Depends(get_session),
) -> LatestTelemetryResponse:
    rows = (
        await session.scalars(
            select(Telemetry)
            .where(Telemetry.device_id == device.id)
            .order_by(Telemetry.device_timestamp.desc())
        )
    ).all()
    latest: dict[str, Telemetry] = {}
    for row in rows:
        latest.setdefault(row.metric, row)
    cutoff = datetime.now(UTC) - timedelta(
        seconds=request.app.state.settings.telemetry_freshness_seconds
    )
    measurements = [
        LatestMeasurement(
            metric=row.metric,
            value=row.value,
            unit=row.unit,
            timestamp=as_utc(row.device_timestamp),
            received_at=as_utc(row.received_at),
            stale=as_utc(row.device_timestamp) < cutoff,
        )
        for row in latest.values()
    ]
    return LatestTelemetryResponse(device_id=device.device_id, measurements=measurements)


def _device_response(device: Device) -> DeviceResponse:
    return DeviceResponse(
        device_id=device.device_id,
        name=device.name,
        device_type=device.device_type,
        status=device.status,
        capabilities=[item.capability for item in device.capabilities],
        description=device.description,
        firmware_version=device.firmware_version,
        hardware_model=device.hardware_model,
        location=device.location,
        created_at=as_utc(device.created_at),
        last_seen_at=as_utc(device.last_seen_at) if device.last_seen_at else None,
    )
