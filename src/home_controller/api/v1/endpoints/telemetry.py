from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from home_controller.database import get_session
from home_controller.models.device import Device
from home_controller.schemas.envelope import (
    ERROR_RESPONSES,
    DeviceMessage,
    ErrorResponse,
    ProtocolData,
    ProtocolResponse,
)
from home_controller.security.authentication import get_current_device
from home_controller.services.telemetry_service import permitted_metrics, persist_message

router = APIRouter(prefix="/api/v1", tags=["telemetry"])


@router.post(
    "/telemetry",
    response_model=ProtocolResponse,
    responses={
        **ERROR_RESPONSES,
        409: {"model": ErrorResponse, "description": "Conflicting message ID reuse"},
    },
    description=(
        "Requires telemetry.temperature.publish and/or telemetry.humidity.publish for each "
        "submitted metric. Repeated identical device/message_id submissions are idempotent; "
        "reusing an ID with different content returns HTTP 409. Timestamps must include a "
        "timezone and be no more than five minutes in the future."
    ),
)
async def submit_telemetry(
    message: DeviceMessage,
    device: Device = Depends(get_current_device),
    session: AsyncSession = Depends(get_session),
) -> ProtocolResponse:
    now = datetime.now(UTC)
    if message.timestamp > now + timedelta(minutes=5):
        raise HTTPException(
            status_code=422, detail="timestamp must not be more than 5 minutes in the future"
        )
    if message.device_id != device.device_id:
        raise HTTPException(status_code=403, detail="device_id does not match authenticated device")
    permitted_metrics(device, {item.metric for item in message.payload.measurements})
    duplicate = await persist_message(session, device, message)
    return ProtocolResponse(
        request_id=UUID(str(message.message_id)),
        data=ProtocolData(accepted=True, duplicate=duplicate),
    )
