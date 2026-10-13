from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WeatherCurrent(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "location": "Bury, Greater Manchester, UK",
                "temperature_c": 14.2,
                "relative_humidity_percent": 78,
                "apparent_temperature_c": 12.7,
                "condition": "Partly cloudy",
                "precipitation_mm": 0,
                "wind_speed_kmh": 12.4,
                "source_timestamp": "2026-10-12T12:00:00Z",
                "fetched_at": "2026-10-12T12:00:03Z",
                "stale": False,
            }
        }
    )

    location: str
    temperature_c: float
    relative_humidity_percent: float | None = None
    apparent_temperature_c: float | None = None
    condition: str
    precipitation_mm: float | None = None
    wind_speed_kmh: float | None = None
    source_timestamp: datetime
    fetched_at: datetime
    stale: bool = False


class ForecastPeriod(BaseModel):
    timestamp: datetime
    temperature_c: float
    precipitation_probability_percent: int | None = None
    condition: str


class WeatherForecast(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "location": "Bury, Greater Manchester, UK",
                "periods": [
                    {
                        "timestamp": "2026-10-12T13:00:00Z",
                        "temperature_c": 14.0,
                        "precipitation_probability_percent": 20,
                        "condition": "Partly cloudy",
                    }
                ],
                "source_timestamp": "2026-10-12T12:00:03Z",
                "fetched_at": "2026-10-12T12:00:03Z",
                "stale": False,
            }
        }
    )

    location: str
    periods: list[ForecastPeriod]
    source_timestamp: datetime
    fetched_at: datetime
    stale: bool = False
