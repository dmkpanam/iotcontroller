from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
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
    waste_provider: Literal["manual", "bury"] = "manual"
    waste_postcode: str = ""
    waste_address: str = ""
    waste_manual_schedule: str = "[]"
    waste_cache_ttl_seconds: int = Field(default=3600, ge=0)
    waste_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    google_calendar_id: str = ""
    google_calendar_auth_method: Literal["oauth", "service_account"] = "oauth"
    google_calendar_service_account_file: str = ""
    google_calendar_client_id: str = ""
    google_calendar_client_secret: SecretStr = SecretStr("")
    google_calendar_refresh_token: SecretStr = SecretStr("")
    google_calendar_cache_ttl_seconds: int = Field(default=300, ge=0)
    google_calendar_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    request_body_limit_bytes: int = Field(default=16384, ge=1024, le=1048576)

    @model_validator(mode="after")
    def require_google_calendar_credentials(self) -> "Settings":
        if not self.google_calendar_id.strip():
            return self
        if self.google_calendar_auth_method == "service_account":
            if not self.google_calendar_service_account_file.strip():
                raise ValueError(
                    "GOOGLE_CALENDAR_SERVICE_ACCOUNT_FILE is required for "
                    "GOOGLE_CALENDAR_AUTH_METHOD=service_account"
                )
            return self
        if not all(
            (
                self.google_calendar_client_id.strip(),
                self.google_calendar_client_secret.get_secret_value().strip(),
                self.google_calendar_refresh_token.get_secret_value().strip(),
            )
        ):
            raise ValueError(
                "GOOGLE_CALENDAR_CLIENT_ID, GOOGLE_CALENDAR_CLIENT_SECRET, and "
                "GOOGLE_CALENDAR_REFRESH_TOKEN are required when GOOGLE_CALENDAR_ID is set"
            )
        return self

    @model_validator(mode="after")
    def require_bury_address(self) -> "Settings":
        if self.waste_provider == "bury" and (
            not self.waste_postcode.strip() or not self.waste_address.strip()
        ):
            raise ValueError(
                "WASTE_POSTCODE and WASTE_ADDRESS are required for WASTE_PROVIDER=bury"
            )
        return self

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
