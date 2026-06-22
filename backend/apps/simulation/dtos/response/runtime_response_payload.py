from __future__ import annotations

from typing import Any, Dict, Optional

from ninja import Schema
from pydantic import ConfigDict


class FlexibleResponse(Schema):
    model_config = ConfigDict(extra="allow")


class ErrorResponse(FlexibleResponse):
    ok: bool = False
    message: str
    detail: Any = None


class HealthResponse(FlexibleResponse):
    ok: bool
    engine: str


class EngineStatusResponse(FlexibleResponse):
    ok: bool
    running: Optional[bool] = None
    paused: Optional[bool] = None
    hasSession: Optional[bool] = None
    stepIndex: Optional[int] = None
    stepSeconds: Optional[int] = None
    websocketClients: Optional[int] = None


class EngineStartResponse(FlexibleResponse):
    ok: bool
    running: bool
    status: Dict[str, Any]
    report: Dict[str, Any]
    mapping: Dict[str, Any]
    snapshot: Dict[str, Any]


class EngineControlResponse(FlexibleResponse):
    ok: bool
    control: Dict[str, Any]
    snapshot: Dict[str, Any]


class EditorConvertResponse(FlexibleResponse):
    ok: bool
    inpText: str
    report: Dict[str, Any]
    mapping: Dict[str, Any]
