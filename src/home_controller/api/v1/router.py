from fastapi import APIRouter

from home_controller.api.v1.endpoints import (
    dashboard,
    devices,
    environment,
    health,
    telemetry,
    waste,
    weather,
)

router = APIRouter()
for endpoint in (health, devices, telemetry, environment, weather, waste, dashboard):
    router.include_router(endpoint.router)
