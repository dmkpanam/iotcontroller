from fastapi import APIRouter, Depends, HTTPException, Request

from home_controller.models.device import Device
from home_controller.schemas.envelope import ERROR_RESPONSES, ErrorResponse
from home_controller.schemas.weather import WeatherCurrent, WeatherForecast
from home_controller.security.authentication import require_capability
from home_controller.services.weather_service import WeatherUnavailable

router = APIRouter(prefix="/api/v1/weather", tags=["weather"])


@router.get(
    "/current",
    response_model=WeatherCurrent,
    responses={
        **ERROR_RESPONSES,
        503: {"model": ErrorResponse, "description": "Weather unavailable and no cache exists"},
    },
    description="Requires weather.read. Returns normalized current conditions and cache freshness.",
)
async def current_weather(
    request: Request, device: Device = Depends(require_capability("weather.read"))
) -> WeatherCurrent:
    del device
    try:
        return await request.app.state.weather_service.get("current")
    except WeatherUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get(
    "/forecast",
    response_model=WeatherForecast,
    responses={
        **ERROR_RESPONSES,
        503: {"model": ErrorResponse, "description": "Forecast unavailable and no cache exists"},
    },
    description="Requires weather.read. Returns a normalized forecast with source timestamps.",
)
async def forecast(
    request: Request, device: Device = Depends(require_capability("weather.read"))
) -> WeatherForecast:
    del device
    try:
        return await request.app.state.weather_service.get("forecast")
    except WeatherUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
