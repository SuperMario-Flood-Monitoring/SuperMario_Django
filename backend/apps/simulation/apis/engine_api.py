from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from ninja import NinjaAPI

from swmm_engine.engine.runtime_engine import SwmmRuntimeError
from swmm_engine.interface import (
    apply_controls,
    pause_engine,
    resume_engine,
    start_engine,
    stop_engine,
)

from .. import state
from ..dtos import (
    EngineControlRequest,
    EngineControlResponse,
    EngineResetRequest,
    EngineStartRequest,
    EngineStartResponse,
    EngineStatusResponse,
    ErrorResponse,
    HealthResponse,
)


engine_api = NinjaAPI(
    title="SWMM Engine API",
    version="1.0.0",
    urls_namespace="swmm_engine_api",
)


def error_payload(message: str, detail: Any = None) -> dict[str, Any]:
    return {"ok": False, "message": message, "detail": detail or message}


@engine_api.exception_handler(SwmmRuntimeError)
def swmm_runtime_error(request: HttpRequest, exc: SwmmRuntimeError):
    return engine_api.create_response(request, error_payload(str(exc), exc.detail), status=exc.status_code)


@engine_api.exception_handler(ValueError)
def value_error(request: HttpRequest, exc: ValueError):
    return engine_api.create_response(request, error_payload(str(exc)), status=400)


@engine_api.get("/health", response=HealthResponse)
def health(request: HttpRequest) -> dict[str, Any]:
    return {"ok": True, "engine": "django-swmm-engine"}


@engine_api.get("/status", response=EngineStatusResponse)
def engine_status(request: HttpRequest) -> dict[str, Any]:
    return state.status_payload()


@engine_api.post("/start", response={200: EngineStartResponse, 400: ErrorResponse, 422: ErrorResponse, 500: ErrorResponse})
async def engine_start(request: HttpRequest, payload: EngineStartRequest) -> dict[str, Any]:
    data = await start_engine(state.engine, payload.to_engine_payload())
    data["status"]["websocketClients"] = state.websocket_clients
    await state.ensure_broadcast_loop()
    await state.broadcast(data["snapshot"])
    return data


@engine_api.post("/reset", response={200: EngineStartResponse, 400: ErrorResponse, 422: ErrorResponse, 500: ErrorResponse})
async def engine_reset(request: HttpRequest, payload: EngineResetRequest) -> dict[str, Any]:
    data = await state.engine.reset(payload.to_engine_payload())
    data["status"]["websocketClients"] = state.websocket_clients
    await state.ensure_broadcast_loop()
    await state.broadcast(data["snapshot"])
    return data


@engine_api.post("/control", response={200: EngineControlResponse, 400: ErrorResponse, 409: ErrorResponse, 500: ErrorResponse})
async def engine_control(request: HttpRequest, payload: EngineControlRequest) -> dict[str, Any]:
    data = await apply_controls(state.engine, payload.to_control_payload())
    await state.broadcast(data["snapshot"])
    return data


@engine_api.post("/stop", response={200: EngineStatusResponse, 400: ErrorResponse, 500: ErrorResponse})
async def engine_stop(request: HttpRequest) -> dict[str, Any]:
    data = await stop_engine(state.engine)
    data["websocketClients"] = state.websocket_clients
    await state.broadcast(data)
    return data


@engine_api.post("/pause", response={200: EngineStatusResponse, 400: ErrorResponse, 409: ErrorResponse, 500: ErrorResponse})
async def engine_pause(request: HttpRequest) -> dict[str, Any]:
    data = await pause_engine(state.engine)
    data["websocketClients"] = state.websocket_clients
    await state.broadcast(data)
    return data


@engine_api.post("/resume", response={200: EngineStatusResponse, 400: ErrorResponse, 409: ErrorResponse, 500: ErrorResponse})
async def engine_resume(request: HttpRequest) -> dict[str, Any]:
    data = await resume_engine(state.engine)
    data["websocketClients"] = state.websocket_clients
    await state.ensure_broadcast_loop()
    await state.broadcast(data)
    return data
