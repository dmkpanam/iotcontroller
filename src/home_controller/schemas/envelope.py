from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Missing or invalid device credentials"},
    403: {"model": ErrorResponse, "description": "Device lacks the required capability"},
    413: {"model": ErrorResponse, "description": "Request body exceeds the configured limit"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    500: {"model": ErrorResponse, "description": "Unexpected internal error"},
}


class Measurement(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    metric: Literal["temperature", "relative_humidity"]
    value: float = Field(strict=True)
    unit: Literal["Cel", "%"]

    @field_validator("value")
    @classmethod
    def validate_range(cls, value: float, info: Any) -> float:
        metric = info.data.get("metric")
        if metric == "temperature" and not -50 <= value <= 100:
            raise ValueError("temperature must be between -50 and 100 Cel")
        if metric == "relative_humidity" and not 0 <= value <= 100:
            raise ValueError("relative_humidity must be between 0 and 100 percent")
        return value

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, unit: str, info: Any) -> str:
        metric = info.data.get("metric")
        if metric == "temperature" and unit != "Cel":
            raise ValueError("temperature must use Cel")
        if metric == "relative_humidity" and unit != "%":
            raise ValueError("relative_humidity must use %")
        return unit


class TelemetryPayload(BaseModel):
    measurements: list[Measurement] = Field(min_length=1, max_length=10)

    @field_validator("measurements")
    @classmethod
    def require_distinct_metrics(cls, value: list[Measurement]) -> list[Measurement]:
        metrics = [item.metric for item in value]
        if len(metrics) != len(set(metrics)):
            raise ValueError("each metric may appear only once per message")
        return value


class DeviceMessage(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "protocol_version": "1.0",
                "message_id": "e0a87b47-3f19-4c55-94d8-5f50e1e9c68a",
                "device_id": "esp32-kitchen-environment-01",
                "message_type": "telemetry.publish",
                "timestamp": "2026-10-12T12:00:00Z",
                "payload": {
                    "measurements": [
                        {"metric": "temperature", "value": 22.4, "unit": "Cel"},
                        {"metric": "relative_humidity", "value": 56.2, "unit": "%"},
                    ]
                },
            }
        }
    )

    protocol_version: Literal["1.0"]
    message_id: UUID
    device_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
    message_type: Literal["telemetry.publish"]
    timestamp: datetime

    @field_validator("timestamp", mode="before")
    @classmethod
    def require_iso_timestamp_string(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("timestamp must be an ISO 8601 string")
        return value

    payload: TelemetryPayload

    @field_validator("timestamp")
    @classmethod
    def require_timezone_and_normalize_to_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


class ProtocolData(BaseModel):
    accepted: bool
    duplicate: bool = False


class ProtocolResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "protocol_version": "1.0",
                "request_id": "e0a87b47-3f19-4c55-94d8-5f50e1e9c68a",
                "status": "success",
                "data": {"accepted": True, "duplicate": False},
                "error": None,
            }
        }
    )

    protocol_version: Literal["1.0"] = "1.0"
    request_id: UUID
    status: Literal["success"] = "success"
    data: ProtocolData
    error: None = None
