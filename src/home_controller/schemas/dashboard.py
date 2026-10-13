from typing import Generic, Literal, TypeVar

from pydantic import BaseModel

from home_controller.schemas.environment import EnvironmentCurrent
from home_controller.schemas.waste import WasteNext
from home_controller.schemas.weather import WeatherCurrent, WeatherForecast

T = TypeVar("T")


class IntegrationResult(BaseModel, Generic[T]):
    status: Literal["available", "unavailable"]
    data: T | None
    error: str | None


class DashboardResponse(BaseModel):
    environment: EnvironmentCurrent
    weather: IntegrationResult[WeatherCurrent]
    forecast: IntegrationResult[WeatherForecast]
    waste: IntegrationResult[WasteNext]
