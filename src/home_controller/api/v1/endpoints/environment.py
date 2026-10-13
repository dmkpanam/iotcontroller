from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from home_controller.database import as_utc, get_session
from home_controller.models.device import Device
from home_controller.models.telemetry import Telemetry
from home_controller.schemas.envelope import ERROR_RESPONSES
from home_controller.schemas.environment import EnvironmentCurrent
from home_controller.security.authentication import require_capability

router = APIRouter(prefix="/api/v1", tags=["environment"])


@router.get(
    "/environment/current",
    response_model=EnvironmentCurrent,
    responses=ERROR_RESPONSES,
    description="Requires the telemetry.environment.read capability.",
)
async def current_environment(
    request: Request,
    device: Device = Depends(require_capability("telemetry.environment.read")),
    session: AsyncSession = Depends(get_session),
) -> EnvironmentCurrent:
    del device
    settings = request.app.state.settings
    source_id = settings.environment_source_device_id
    if not source_id:
        return EnvironmentCurrent(status="unavailable", source_device_id=None, measurements={})
    source = await session.scalar(select(Device).where(Device.device_id == source_id))
    if source is None:
        return EnvironmentCurrent(status="unavailable", source_device_id=source_id, measurements={})
    rows = (
        await session.scalars(
            select(Telemetry)
            .where(Telemetry.device_id == source.id)
            .order_by(Telemetry.device_timestamp.desc())
        )
    ).all()
    latest: dict[str, Telemetry] = {}
    for row in rows:
        latest.setdefault(row.metric, row)
    if not latest:
        return EnvironmentCurrent(status="unavailable", source_device_id=source_id, measurements={})
    measurement_time = as_utc(max(item.device_timestamp for item in latest.values()))
    freshness_cutoff = datetime.now(UTC) - timedelta(seconds=settings.telemetry_freshness_seconds)
    stale = measurement_time < freshness_cutoff
    values = {
        metric: {
            "value": row.value,
            "unit": row.unit,
            "timestamp": as_utc(row.device_timestamp),
            "stale": as_utc(row.device_timestamp) < freshness_cutoff,
        }
        for metric, row in latest.items()
    }
    return EnvironmentCurrent(
        status="stale" if stale else "available",
        source_device_id=source_id,
        timestamp=measurement_time,
        stale=stale,
        measurements=values,
    )
