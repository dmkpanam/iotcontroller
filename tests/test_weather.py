import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from home_controller.config import Settings
from home_controller.schemas.weather import WeatherCurrent
from home_controller.services.weather_service import (
    CachedWeatherService,
    OpenMeteoProvider,
    OpenWeatherMapProvider,
    WeatherUnavailable,
    build_weather_provider,
)


def test_open_meteo_normalizes_response():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["timezone"] == "UTC"
        assert request.url.params["temperature_unit"] == "fahrenheit"
        assert request.url.params["wind_speed_unit"] == "mph"
        return httpx.Response(
            200,
            json={
                "current": {
                    "time": "2026-10-12T12:00",
                    "temperature_2m": 68,
                    "relative_humidity_2m": 67,
                    "apparent_temperature": 62.96,
                    "precipitation": 0,
                    "weather_code": 2,
                    "wind_speed_10m": 10,
                },
                "hourly": {
                    "time": ["2026-10-12T12:00"],
                    "temperature_2m": [68],
                    "precipitation_probability": [20],
                    "weather_code": [2],
                },
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenMeteoProvider(
                Settings(
                    weather_temperature_unit="fahrenheit",
                    weather_wind_speed_unit="mph",
                ),
                client,
            )
            current = await provider.current()
            forecast = await provider.forecast()
            assert current.condition == "Partly cloudy"
            assert current.temperature_c == 20
            assert current.wind_speed_kmh == 16.09344
            assert forecast.periods[0].temperature_c == 20
            assert forecast.periods[0].precipitation_probability_percent == 20
            assert forecast.periods[0].timestamp.tzinfo == UTC
            assert calls == 1

    asyncio.run(run())


def test_weather_cache_fallback_and_no_cache_failure():
    class Provider:
        fail = False
        current_calls = 0

        async def current(self):
            self.current_calls += 1
            if self.fail:
                raise httpx.ReadTimeout("upstream timeout")
            return WeatherCurrent(
                location="Bury",
                temperature_c=15,
                condition="Cloudy",
                source_timestamp=datetime.now(UTC),
                fetched_at=datetime.now(UTC),
            )

        async def forecast(self):
            raise httpx.ReadTimeout("upstream timeout")

    async def run():
        provider = Provider()
        service = CachedWeatherService(provider, ttl_seconds=600)
        fresh = await service.get("current")
        assert not fresh.stale
        cached = await service.get("current")
        assert not cached.stale
        assert provider.current_calls == 1
        provider.fail = True
        service.ttl = service.ttl * 0
        stale = await service.get("current")
        assert stale.stale
        with pytest.raises(WeatherUnavailable):
            await service.get("forecast")

    asyncio.run(run())


def test_open_weather_map_normalizes_response():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["appid"] == "test-api-key"
        assert request.url.params["units"] == "metric"
        if request.url.path.endswith("/weather"):
            return httpx.Response(
                200,
                json={
                    "dt": 1791906300,
                    "main": {
                        "temp": 14.3,
                        "feels_like": 13.9,
                        "humidity": 80,
                    },
                    "weather": [
                        {
                            "id": 804,
                            "main": "Clouds",
                            "description": "overcast clouds",
                        }
                    ],
                    "wind": {"speed": 5.0},
                    "rain": {"1h": 1.2},
                },
            )
        if request.url.path.endswith("/forecast"):
            return httpx.Response(
                200,
                json={
                    "list": [
                        {
                            "dt": 1791914400,
                            "main": {"temp": 14.15},
                            "weather": [
                                {
                                    "id": 802,
                                    "main": "Clouds",
                                    "description": "scattered clouds",
                                }
                            ],
                            "pop": 0.25,
                        }
                    ]
                },
            )
        return httpx.Response(404)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenWeatherMapProvider(
                Settings(
                    weather_provider="openweathermap",
                    weather_api_key="test-api-key",
                ),
                client,
            )
            current = await provider.current()
            assert current.temperature_c == 14.3
            assert current.apparent_temperature_c == 13.9
            assert current.relative_humidity_percent == 80.0
            assert current.condition == "Overcast clouds"
            assert current.wind_speed_kmh == 18.0
            assert current.precipitation_mm == 1.2
            assert current.source_timestamp.tzinfo == UTC

            forecast = await provider.forecast()
            assert len(forecast.periods) == 1
            assert forecast.periods[0].temperature_c == 14.15
            assert forecast.periods[0].precipitation_probability_percent == 25
            assert forecast.periods[0].condition == "Scattered clouds"
            assert forecast.periods[0].timestamp.tzinfo == UTC

    asyncio.run(run())


def test_open_weather_map_requires_api_key():
    async def run():
        async with httpx.AsyncClient() as client:
            provider = OpenWeatherMapProvider(
                Settings(
                    weather_provider="openweathermap",
                    weather_api_key=None,
                ),
                client,
            )
            with pytest.raises(WeatherUnavailable, match="API key is not configured"):
                await provider.current()
            with pytest.raises(WeatherUnavailable, match="API key is not configured"):
                await provider.forecast()

    asyncio.run(run())


def test_build_weather_provider_factory():
    client = httpx.AsyncClient()
    open_meteo = build_weather_provider(
        Settings(weather_provider="open_meteo"), client
    )
    assert isinstance(open_meteo, OpenMeteoProvider)

    owm = build_weather_provider(
        Settings(weather_provider="openweathermap", weather_api_key="test"), client
    )
    assert isinstance(owm, OpenWeatherMapProvider)

