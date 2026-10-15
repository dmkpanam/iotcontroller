from fastapi import APIRouter

from home_controller.api.v1.endpoints import (
    calendar,
    dashboard,
    devices,
    environment,
    health,
    telemetry,
    weather,
)

router = APIRouter()
for endpoint in (health, devices, telemetry, environment, weather, calendar, dashboard):
    router.include_router(endpoint.router)
