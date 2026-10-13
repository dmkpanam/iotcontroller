from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./home_controller.db"
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    weather_provider: Literal["open_meteo", "openweathermap"] = "openweathermap"
    weather_api_key: str | None = None
    weather_latitude: float = Field(default=53.593, ge=-90, le=90)
    weather_longitude: float = Field(default=-2.298, ge=-180, le=180)
    weather_temperature_unit: Literal["celsius", "fahrenheit"] = "celsius"
    weather_wind_speed_unit: Literal["kmh", "ms", "mph", "kn"] = "kmh"
    weather_cache_ttl_seconds: int = Field(default=600, ge=0)
    weather_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    location_name: str = "Bury, Greater Manchester, UK"
    location_timezone: str = "Europe/London"
    environment_source_device_id: str | None = None
    telemetry_freshness_seconds: int = Field(default=1800, gt=0)
    waste_provider: Literal["manual"] = "manual"
    waste_address: str = ""
    waste_manual_schedule: str = "[]"
    waste_cache_ttl_seconds: int = Field(default=3600, ge=0)
    waste_reminder_days_before: int = Field(default=1, ge=0, le=30)
    request_body_limit_bytes: int = Field(default=16384, ge=1024, le=1048576)

    @field_validator("database_url")
    @classmethod
    def require_async_database_url(cls, value: str) -> str:
        if value.startswith("sqlite:///"):
            return value.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return value

    @field_validator("location_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
