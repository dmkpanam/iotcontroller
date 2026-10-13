from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from home_controller.database import get_session
from home_controller.models.device import Credential, Device
from home_controller.security.provisioning import verify_credential

device_bearer = HTTPBearer(auto_error=False, scheme_name="DeviceBearer")


async def get_current_device(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(device_bearer),
    session: AsyncSession = Depends(get_session),
) -> Device:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credentials"
        )
    token = credentials.credentials
    try:
        prefix, _ = token.removeprefix("hci_").split(".", 1)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credentials"
        ) from None
    credential = await session.scalar(
        select(Credential)
        .where(Credential.selector == prefix, Credential.revoked_at.is_(None))
        .options(selectinload(Credential.device).selectinload(Device.capabilities))
    )
    if (
        credential is None
        or not verify_credential(credential, token)
        or credential.device.status != "active"
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid device credentials"
        )
    credential.device.last_seen_at = datetime.now(UTC)
    await session.commit()
    request.state.device = credential.device
    return credential.device


def require_capability(capability: str) -> Callable[..., Awaitable[Device]]:
    async def dependency(device: Device = Depends(get_current_device)) -> Device:
        capabilities = {item.capability for item in device.capabilities}
        if capability not in capabilities:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Missing device capability"
            )
        return device

    return dependency
