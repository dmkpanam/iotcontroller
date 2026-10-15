import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from home_controller.database import as_utc, get_session
from home_controller.models.device import Device
from home_controller.models.telemetry import Telemetry
from home_controller.schemas.dashboard import DashboardResponse
from home_controller.schemas.envelope import ERROR_RESPONSES
from home_controller.security.authentication import get_current_device
from home_controller.services.weather_service import WeatherUnavailable

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    responses=ERROR_RESPONSES,
    description=(
        "Requires telemetry.environment.read, weather.read, and calendar.read. "
        "Sections report unavailable integrations independently."
    ),
)
async def dashboard(
    request: Request,
    device: Device = Depends(get_current_device),
    session: AsyncSession = Depends(get_session),
) -> DashboardResponse:
    capabilities = {item.capability for item in device.capabilities}
    required = {
        "telemetry.environment.read",
        "weather.read",
        "calendar.read",
    }
    missing = required - capabilities
    if missing:
        raise HTTPException(status_code=403, detail="Missing dashboard read capabilities")

    settings = request.app.state.settings
    source = (
        await session.scalar(
            select(Device).where(Device.device_id == settings.environment_source_device_id)
        )
        if settings.environment_source_device_id
        else None
    )
    indoor: dict[str, Any]
    if source is None:
        indoor = {"status": "unavailable", "source_device_id": None, "measurements": {}}
    else:
        records = (
            await session.scalars(
                select(Telemetry)
                .where(Telemetry.device_id == source.id)
                .order_by(Telemetry.device_timestamp.desc())
            )
        ).all()
        latest: dict[str, Telemetry] = {}
        for record in records:
            latest.setdefault(record.metric, record)
        cutoff = datetime.now(UTC) - timedelta(seconds=settings.telemetry_freshness_seconds)
        latest_time = max(
            (as_utc(record.device_timestamp) for record in latest.values()), default=None
        )
        stale = latest_time is not None and latest_time < cutoff
        indoor = {
            "status": "stale" if stale else ("available" if latest else "unavailable"),
            "source_device_id": source.device_id,
            "timestamp": latest_time,
            "stale": stale,
            "measurements": {
                metric: {
                    "value": row.value,
                    "unit": row.unit,
                    "timestamp": as_utc(row.device_timestamp),
                    "stale": as_utc(row.device_timestamp) < cutoff,
                }
                for metric, row in latest.items()
            },
        }
    weather_current, weather_forecast, calendar = await asyncio.gather(
        _integration(lambda: request.app.state.weather_service.get("current")),
        _integration(lambda: request.app.state.weather_service.get("forecast")),
        request.app.state.calendar_service.outlook(),
    )
    return DashboardResponse(
        environment=indoor,
        weather=weather_current,
        forecast=weather_forecast,
        calendar=calendar,
    )


async def _integration(call: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    try:
        return {"status": "available", "data": await call(), "error": None}
    except (WeatherUnavailable, ValueError) as exc:
        return {"status": "unavailable", "data": None, "error": str(exc)}
