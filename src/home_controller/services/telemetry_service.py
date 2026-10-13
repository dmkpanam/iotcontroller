import hashlib
import json
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from home_controller.models.device import Device
from home_controller.models.telemetry import MessageReceipt, Telemetry
from home_controller.schemas.envelope import DeviceMessage

CAPABILITIES = {
    "temperature": "telemetry.temperature.publish",
    "relative_humidity": "telemetry.humidity.publish",
}


def message_fingerprint(message: DeviceMessage) -> str:
    body = json.dumps(message.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


async def persist_message(session: AsyncSession, device: Device, message: DeviceMessage) -> bool:
    fingerprint = message_fingerprint(message)
    receipt = await session.scalar(
        select(MessageReceipt).where(
            MessageReceipt.device_id == device.id,
            MessageReceipt.message_id == str(message.message_id),
        )
    )
    if receipt:
        if receipt.fingerprint != fingerprint:
            raise HTTPException(
                status_code=409, detail="message_id was reused with different content"
            )
        return True

    try:
        session.add(
            MessageReceipt(
                device_id=device.id, message_id=str(message.message_id), fingerprint=fingerprint
            )
        )
        received_at = datetime.now(UTC)
        for measurement in message.payload.measurements:
            session.add(
                Telemetry(
                    device_id=device.id,
                    message_id=str(message.message_id),
                    metric=measurement.metric,
                    value=measurement.value,
                    unit=measurement.unit,
                    device_timestamp=message.timestamp,
                    received_at=received_at,
                )
            )
        await session.commit()
        return False
    except IntegrityError:
        await session.rollback()
        receipt = await session.scalar(
            select(MessageReceipt).where(
                MessageReceipt.device_id == device.id,
                MessageReceipt.message_id == str(message.message_id),
            )
        )
        if receipt and receipt.fingerprint == fingerprint:
            return True
        if receipt:
            raise HTTPException(
                status_code=409, detail="message_id was reused with different content"
            ) from None
        raise


def permitted_metrics(device: Device, metric_names: set[str]) -> None:
    allowed = {item.capability for item in device.capabilities}
    missing = {CAPABILITIES[name] for name in metric_names} - allowed
    if missing:
        raise HTTPException(status_code=403, detail="Missing capability for submitted telemetry")
