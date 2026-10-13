from fastapi import APIRouter, Depends, HTTPException, Request

from home_controller.models.device import Device
from home_controller.schemas.envelope import ERROR_RESPONSES, ErrorResponse
from home_controller.schemas.waste import WasteNext
from home_controller.security.authentication import require_capability
from home_controller.services.waste_service import WasteUnavailable

router = APIRouter(prefix="/api/v1/waste", tags=["waste"])


@router.get(
    "/next",
    response_model=WasteNext,
    responses={
        **ERROR_RESPONSES,
        503: {"model": ErrorResponse, "description": "Waste schedule is unavailable or invalid"},
    },
    description=(
        "Requires waste_collection.read. Collection dates are local calendar dates and reminder "
        "dates are calculated from the actual next collection."
    ),
)
async def next_collection(
    request: Request, device: Device = Depends(require_capability("waste_collection.read"))
) -> WasteNext:
    del device
    try:
        return await request.app.state.waste_service.next()
    except (ValueError, WasteUnavailable) as exc:
        raise HTTPException(status_code=503, detail="Waste collection schedule is invalid") from exc
