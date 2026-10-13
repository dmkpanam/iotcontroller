import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx

from home_controller.config import Settings
from home_controller.schemas.weather import ForecastPeriod, WeatherCurrent, WeatherForecast


class WeatherProvider(Protocol):
    async def current(self) -> WeatherCurrent: ...

    async def forecast(self) -> WeatherForecast: ...


class WeatherUnavailable(Exception):
    pass


class OpenMeteoProvider:
    endpoint = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
        self.cached_payload: tuple[datetime, dict[str, Any]] | None = None
        self.lock = asyncio.Lock()

    async def _fetch(self) -> dict[str, Any]:
        async with self.lock:
            now = datetime.now(UTC)
            if self.cached_payload is not None and now - self.cached_payload[0] <= timedelta(
                seconds=self.settings.weather_cache_ttl_seconds
            ):
                return self.cached_payload[1]
            params = {
                "latitude": self.settings.weather_latitude,
                "longitude": self.settings.weather_longitude,
                "timezone": "UTC",
                "temperature_unit": self.settings.weather_temperature_unit,
                "wind_speed_unit": self.settings.weather_wind_speed_unit,
                "current": (
                    "temperature_2m,relative_humidity_2m,apparent_temperature,"
                    "precipitation,weather_code,wind_speed_10m"
                ),
                "hourly": "temperature_2m,precipitation_probability,weather_code",
                "forecast_days": 2,
            }
            response = await self.client.get(self.endpoint, params=params)
            response.raise_for_status()
            data = response.json()
            self.cached_payload = (now, data)
            return data

    async def current(self) -> WeatherCurrent:
        data = await self._fetch()
        current = data["current"]
        source_timestamp = datetime.fromisoformat(current["time"]).replace(tzinfo=UTC)
        return WeatherCurrent(
            location=self.settings.location_name,
            temperature_c=_temperature_celsius(
                current["temperature_2m"], self.settings.weather_temperature_unit
            ),
            relative_humidity_percent=current.get("relative_humidity_2m"),
            apparent_temperature_c=(
                _temperature_celsius(
                    current["apparent_temperature"], self.settings.weather_temperature_unit
                )
                if current.get("apparent_temperature") is not None
                else None
            ),
            condition=_condition(current.get("weather_code")),
            precipitation_mm=current.get("precipitation"),
            wind_speed_kmh=(
                _wind_speed_kmh(current["wind_speed_10m"], self.settings.weather_wind_speed_unit)
                if current.get("wind_speed_10m") is not None
                else None
            ),
            source_timestamp=source_timestamp,
            fetched_at=datetime.now(UTC),
        )

    async def forecast(self) -> WeatherForecast:
        data = await self._fetch()
        hourly = data["hourly"]
        periods = [
            ForecastPeriod(
                timestamp=datetime.fromisoformat(time).replace(tzinfo=UTC),
                temperature_c=_temperature_celsius(
                    hourly["temperature_2m"][index], self.settings.weather_temperature_unit
                ),
                precipitation_probability_percent=hourly.get(
                    "precipitation_probability", [None] * len(hourly["time"])
                )[index],
                condition=_condition(hourly["weather_code"][index]),
            )
            for index, time in enumerate(hourly["time"])
        ]
        return WeatherForecast(
            location=self.settings.location_name,
            periods=periods,
            source_timestamp=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
        )


class OpenWeatherMapProvider:
    current_endpoint = "https://api.openweathermap.org/data/2.5/weather"
    forecast_endpoint = "https://api.openweathermap.org/data/2.5/forecast"

    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client

    def _require_api_key(self) -> str:
        if not self.settings.weather_api_key:
            raise WeatherUnavailable("OpenWeatherMap API key is not configured")
        return self.settings.weather_api_key

    async def current(self) -> WeatherCurrent:
        api_key = self._require_api_key()
        params = {
            "lat": self.settings.weather_latitude,
            "lon": self.settings.weather_longitude,
            "appid": api_key,
            "units": "metric",
        }
        response = await self.client.get(self.current_endpoint, params=params)
        response.raise_for_status()
        data = response.json()

        main = data.get("main", {})
        weather_list = data.get("weather", [])
        weather_info = weather_list[0] if weather_list else {}
        condition = (
            weather_info.get("description", "").capitalize()
            or weather_info.get("main", "Unknown")
        )
        wind = data.get("wind", {})
        wind_speed_kmh = (
            round(float(wind["speed"]) * 3.6, 2)
            if "speed" in wind and wind["speed"] is not None
            else None
        )
        precipitation = 0.0
        if isinstance(data.get("rain"), dict):
            precipitation = float(data["rain"].get("1h", data["rain"].get("3h", 0.0)) or 0.0)
        elif isinstance(data.get("snow"), dict):
            precipitation = float(data["snow"].get("1h", data["snow"].get("3h", 0.0)) or 0.0)

        source_timestamp = datetime.fromtimestamp(data["dt"], tz=UTC)
        return WeatherCurrent(
            location=self.settings.location_name,
            temperature_c=float(main["temp"]),
            relative_humidity_percent=(
                float(main["humidity"])
                if "humidity" in main and main["humidity"] is not None
                else None
            ),
            apparent_temperature_c=(
                float(main["feels_like"])
                if "feels_like" in main and main["feels_like"] is not None
                else None
            ),
            condition=condition,
            precipitation_mm=precipitation,
            wind_speed_kmh=wind_speed_kmh,
            source_timestamp=source_timestamp,
            fetched_at=datetime.now(UTC),
        )

    async def forecast(self) -> WeatherForecast:
        api_key = self._require_api_key()
        params = {
            "lat": self.settings.weather_latitude,
            "lon": self.settings.weather_longitude,
            "appid": api_key,
            "units": "metric",
        }
        response = await self.client.get(self.forecast_endpoint, params=params)
        response.raise_for_status()
        data = response.json()

        periods: list[ForecastPeriod] = []
        for item in data.get("list", []):
            item_main = item.get("main", {})
            item_weather = item.get("weather", [])
            w_info = item_weather[0] if item_weather else {}
            cond = (
                w_info.get("description", "").capitalize()
                or w_info.get("main", "Unknown")
            )
            pop = item.get("pop")
            pop_percent = int(round(float(pop) * 100)) if pop is not None else None
            periods.append(
                ForecastPeriod(
                    timestamp=datetime.fromtimestamp(item["dt"], tz=UTC),
                    temperature_c=float(item_main["temp"]),
                    precipitation_probability_percent=pop_percent,
                    condition=cond,
                )
            )

        return WeatherForecast(
            location=self.settings.location_name,
            periods=periods,
            source_timestamp=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
        )


def build_weather_provider(settings: Settings, client: httpx.AsyncClient) -> WeatherProvider:
    if settings.weather_provider == "open_meteo":
        return OpenMeteoProvider(settings, client)
    return OpenWeatherMapProvider(settings, client)


def _condition(code: int | None) -> str:
    codes = {
        0: "Clear",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Drizzle",
        55: "Dense drizzle",
        61: "Light rain",
        63: "Rain",
        65: "Heavy rain",
        71: "Light snow",
        73: "Snow",
        75: "Heavy snow",
        80: "Rain showers",
        81: "Rain showers",
        82: "Heavy rain showers",
        95: "Thunderstorm",
    }
    return codes.get(code, "Unknown")


def _temperature_celsius(value: float, unit: str) -> float:
    return (value - 32) * 5 / 9 if unit == "fahrenheit" else value


def _wind_speed_kmh(value: float, unit: str) -> float:
    factors = {"kmh": 1, "ms": 3.6, "mph": 1.609344, "kn": 1.852}
    return value * factors[unit]


class CachedWeatherService:
    def __init__(self, provider: WeatherProvider, ttl_seconds: int):
        self.provider = provider
        self.ttl = timedelta(seconds=ttl_seconds)
        self.cache: dict[str, tuple[datetime, Any]] = {}
        self.lock = asyncio.Lock()

    async def get(self, resource: str) -> WeatherCurrent | WeatherForecast:
        async with self.lock:
            now = datetime.now(UTC)
            cached = self.cache.get(resource)
            if cached and now - cached[0] <= self.ttl:
                return cached[1]
            try:
                result = await getattr(self.provider, resource)()
            except (httpx.HTTPError, WeatherUnavailable, KeyError, ValueError, TypeError) as exc:
                if cached:
                    return cached[1].model_copy(update={"stale": True})
                raise WeatherUnavailable("Weather provider is unavailable") from exc
            self.cache[resource] = (now, result)
            return result
