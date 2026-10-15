import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from home_controller.api.v1.router import router
from home_controller.config import Settings, get_settings
from home_controller.services.calendar_service import CalendarService, WasteCalendarSource
from home_controller.services.waste_service import CachedWasteService, build_waste_provider
from home_controller.services.weather_service import (
    CachedWeatherService,
    build_weather_provider,
)


class RequestSizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                await self._send_error(
                    send, 400, "invalid_content_length", "Invalid Content-Length"
                )
                return
            if declared_length > self.max_bytes:
                await self._send_error(send, 413, "request_too_large", "Request body is too large")
                return

        messages: list[Message] = []
        body_size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            body_size += len(message.get("body", b""))
            if body_size > self.max_bytes:
                await self._send_error(send, 413, "request_too_large", "Request body is too large")
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _send_error(send: Send, status_code: int, code: str, message: str) -> None:
        body = json.dumps({"error": {"code": code, "message": message}}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(settings: Settings | None = None, *, database_url: str | None = None) -> FastAPI:
    config = settings or get_settings()
    db_url = database_url or config.database_url
    engine = create_async_engine(
        db_url,
        connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
    )
    if db_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        timeout = httpx.Timeout(config.weather_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            app.state.http_client = client
            app.state.weather_service = CachedWeatherService(
                build_weather_provider(config, client), config.weather_cache_ttl_seconds
            )
            app.state.waste_service = CachedWasteService(
                build_waste_provider(config, client), config
            )
            app.state.calendar_service = CalendarService(
                [WasteCalendarSource(app.state.waste_service, config)], config.location_timezone
            )
            yield
        await engine.dispose()

    app = FastAPI(
        title="Home IoT Controller",
        version="1.0.0",
        description=(
            "A local-first API for provisioned home IoT devices. Devices are provisioned by an "
            "administrator using the CLI; there is no public registration endpoint. Device "
            "credentials use HTTP Bearer authentication and should only cross untrusted networks "
            "over HTTPS. Telemetry timestamps must include an offset and cannot be over five "
            "minutes in the future. Exact retries are idempotent; conflicting message ID reuse "
            "returns HTTP 409. SQLite values are normalised as UTC; collection dates are local "
            "calendar dates in the configured location timezone."
        ),
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.settings = config
    app.state.logger = logging.getLogger("home_controller")
    app.add_middleware(RequestSizeLimitMiddleware, max_bytes=config.request_body_limit_bytes)
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        del request
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, exc: HTTPException):
        del request
        code = {
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            413: "request_too_large",
            422: "validation_error",
            503: "service_unavailable",
        }.get(exc.status_code, "request_error")
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content={"error": {"code": code, "message": exc.detail}},
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception):
        app.state.logger.error(
            json.dumps({"event": "unhandled_request_error", "exception_type": type(exc).__name__}),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "An internal error occurred"}},
        )

    return app


app = create_app()
