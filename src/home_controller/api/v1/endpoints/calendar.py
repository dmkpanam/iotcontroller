from fastapi import APIRouter, Depends, Request

from home_controller.models.device import Device
from home_controller.schemas.calendar import CalendarOutlook
from home_controller.schemas.envelope import ERROR_RESPONSES
from home_controller.security.authentication import require_capability

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])


@router.get(
    "",
    response_model=CalendarOutlook,
    responses=ERROR_RESPONSES,
    description=(
        "Requires calendar.read. Returns today and tomorrow in LOCATION_TIMEZONE, including "
        "collection events only, without reminders. Empty days are included. Source failures and "
        "stale data are reported independently in sources, not treated as an empty calendar."
    ),
)
async def calendar_outlook(
    request: Request, device: Device = Depends(require_capability("calendar.read"))
) -> CalendarOutlook:
    del device
    return await request.app.state.calendar_service.outlook()
