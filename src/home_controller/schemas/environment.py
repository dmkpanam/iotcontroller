from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class EnvironmentMeasurement(BaseModel):
    value: float
    unit: str
    timestamp: datetime
    stale: bool


class EnvironmentCurrent(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "available",
                "source_device_id": "esp32-kitchen-environment-01",
                "timestamp": "2026-10-12T12:00:00Z",
                "stale": False,
                "measurements": {
                    "temperature": {
                        "value": 22.4,
                        "unit": "Cel",
                        "timestamp": "2026-10-12T12:00:00Z",
                        "stale": False,
                    },
                    "relative_humidity": {
                        "value": 56.2,
                        "unit": "%",
                        "timestamp": "2026-10-12T12:00:00Z",
                        "stale": False,
                    },
                },
            }
        }
    )

    status: Literal["available", "stale", "unavailable"]
    source_device_id: str | None
    timestamp: datetime | None = None
    stale: bool = False
    measurements: dict[str, EnvironmentMeasurement]
