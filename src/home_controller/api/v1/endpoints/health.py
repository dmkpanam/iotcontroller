import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from home_controller.database import get_session
from home_controller.schemas.envelope import ErrorResponse

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/health/ready",
    responses={503: {"model": ErrorResponse, "description": "Database is not ready"}},
)
async def ready(request: Request, session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1 FROM devices LIMIT 1"))
    except Exception as exc:
        request.app.state.logger.error(
            json.dumps(
                {"event": "database_readiness_failed", "exception_type": type(exc).__name__}
            ),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service is not ready",
        ) from exc
    return {"status": "ready"}
