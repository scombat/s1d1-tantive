"""The app factory: engine, catalogue, service routes, the adapters named by S1D1_FORMATS, auth, limits, errors."""

from __future__ import annotations

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tantive import service
from tantive.adapters import ADAPTERS
from tantive.capture import Capture
from tantive.send import Sender
from tantive.config import ConfigError, Settings, cpu_count_from_cgroup
from tantive.engine import Engine
from tantive.errors import ApiError, error_response
from tantive.longprompt import LongPromptPolicy
from tantive.model import Model, ModelInfo
from tantive.questions import Catalogue
from tantive.telemetry import configure_otel, log_event
from tantive.tiers import TierSet, native_set

MAX_BODY_BYTES = 2 * 1024 * 1024


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)  # the router's own 404/405 are Starlette's, FastAPI's subclass it
    async def on_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:  # ApiError included
        return error_response(exc.status_code, str(exc.detail), exc.headers)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        return error_response(400, f"invalid request body: {where}: {first.get('msg', 'invalid')}")


def require_api_key(settings: Settings) -> Depends:
    """`Authorization: Bearer <key>` or `x-api-key: <key>`; open when no key is configured."""

    def check(request: Request) -> None:
        if settings.api_key is None:
            return
        auth = request.headers.get("authorization", "")
        presented = auth[7:] if auth[:7].lower() == "bearer " else request.headers.get("x-api-key", "")
        if not secrets.compare_digest(presented.encode(), settings.api_key.encode()):
            raise ApiError(401, "invalid API key")

    return Depends(check)


class _BodySizeLimit:
    """Pure ASGI middleware: 413 when the body exceeds the limit, from Content-Length before any route runs, or from
    the bytes actually received (chunked bodies) as soon as the count passes the limit. The streamed case raises an
    ApiError from `receive`: FastAPI re-raises HTTPExceptions it meets while reading the body, so the error handler
    answers 413 (an exception of another class would become FastAPI's own 400 "error parsing the body")."""

    def __init__(self, app, limit: int) -> None:
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        if length is not None and length.isdigit() and int(length) > self.limit:
            await error_response(413, self._message)(scope, receive, send)
            return
        received = 0

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise ApiError(413, self._message)
            return message

        await self.app(scope, counting_receive, send)

    @property
    def _message(self) -> str:
        return f"request body exceeds {self.limit} bytes"


MEDIAN_TOKENS = 64  # p50 of the research test set (docs/21 § 5.1): the "typical" prompt for the throughput estimate


def log_ready(settings: Settings, info: ModelInfo, engine: Engine) -> None:
    """The `ready` line: what was loaded, the fitted cost model and what it means for capacity; a
    `capacity_warning` when a full-window prompt is predicted to exceed the deadline (it would be shed)."""
    cost = engine.cost
    predicted_1024 = round(cost.predict(info.max_length)) if cost else None
    typical = cost.predict(MEDIAN_TOKENS) if cost else None
    log_event(
        "ready",
        variant=info.variant,
        revision=info.revision,
        sigma=info.sigma,
        sigma_source=info.sigma_source,
        formats=sorted(settings.formats),
        cpus=cpu_count_from_cgroup(),
        threads=settings.threads,
        deadline_ms=settings.deadline_ms,
        max_queue=settings.max_queue,
        fixed_ms=round(cost.fixed_ms, 3) if cost else None,
        per_token_ms=round(cost.per_token_ms, 4) if cost else None,
        predicted_ms_1024=predicted_1024,
        sustainable_rps=round(1000 / typical, 1) if typical else None,
    )
    if predicted_1024 is not None and predicted_1024 > settings.deadline_ms:
        log_event(
            "capacity_warning",
            level=logging.WARNING,
            message=f"a full-window prompt ({info.max_length} tokens) is predicted at {predicted_1024} ms, beyond "
            f"S1D1_DEADLINE_MS={settings.deadline_ms}: it will be shed with 503 even on an idle server",
            predicted_ms_1024=predicted_1024,
            deadline_ms=settings.deadline_ms,
        )


def create_app(settings: Settings, model: Model) -> FastAPI:
    try:
        native: TierSet = native_set(settings.tiers)
    except ValueError as e:
        raise ConfigError(f"S1D1_TIERS: {e}") from None
    engine = Engine(
        model,
        deadline_ms=settings.deadline_ms,
        max_queue=settings.max_queue,
        policy=LongPromptPolicy.from_settings(settings, window=model.info.max_length),
    )
    # sigma comes with the weights; S1D1_DEFAULT_SIGMA applies only when variant.json carries none
    info = (
        model.info if model.info.sigma_source == "variant.json" else replace(model.info, sigma=settings.default_sigma)
    )
    catalogue = Catalogue(native, info.sigma)
    capture = (
        Capture(
            settings.capture_dir,
            info=info,
            native=native,
            sample=settings.capture_sample,
            min_margin=settings.capture_min_margin,
            max_bytes=settings.capture_max_mb * 1024 * 1024,
            retention_days=settings.capture_retention_days,
            queue_size=settings.capture_queue,
            terms_version=settings.terms_version,
            send=settings.capture_send,
        )
        if settings.capture_dir
        else None
    )
    sender = (
        Sender(
            settings.capture_dir,
            url=settings.capture_url,
            token=settings.capture_token,
            batch=settings.capture_batch,
            interval=settings.capture_send_seconds,
            start=False,
        )
        if settings.capture_send and settings.capture_dir
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await engine.start()
        log_ready(settings, info, engine)
        if sender is not None:
            sender.start()
        yield
        if sender is not None:
            await asyncio.to_thread(sender.close)
        await engine.close()
        if capture is not None:
            await asyncio.to_thread(capture.close)

    app = FastAPI(title="s1d1-tantive", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.engine, app.state.catalogue, app.state.info, app.state.native = (
        settings,
        engine,
        catalogue,
        info,
        native,
    )
    app.state.capture = capture
    app.state.sender = sender
    install_error_handlers(app)
    app.add_middleware(_BodySizeLimit, limit=MAX_BODY_BYTES)
    app.include_router(service.router(engine, settings, info, native, sender))
    auth = require_api_key(settings)
    for name in sorted(settings.formats):
        factory = ADAPTERS.get(name)
        if factory is not None:
            app.include_router(factory(app), dependencies=[auth])
    configure_otel(app, settings.otlp_endpoint)
    return app
