from datetime import datetime

from pydantic import BaseModel


class DeviceResponse(BaseModel):
    device_id: str
    name: str
    device_type: str
    status: str
    capabilities: list[str]
    description: str | None = None
    firmware_version: str | None = None
    hardware_model: str | None = None
    location: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None


class LatestMeasurement(BaseModel):
    metric: str
    value: float
    unit: str
    timestamp: datetime
    received_at: datetime
    stale: bool


class LatestTelemetryResponse(BaseModel):
    device_id: str
    measurements: list[LatestMeasurement]
