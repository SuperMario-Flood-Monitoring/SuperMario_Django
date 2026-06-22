from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ninja import Schema
from pydantic import ConfigDict


class FlexibleResponse(Schema):
    model_config = ConfigDict(extra="allow")


class ErrorResponse(FlexibleResponse):
    ok: bool = False
    message: str
    detail: Any = None


class ScenarioResponse(Schema):
    id: int
    title: str
    description: str
    layoutJson: Dict[str, Any]
    version: int
    isActive: bool
    createdAt: datetime
    updatedAt: datetime


class ScenarioListResponse(FlexibleResponse):
    ok: bool
    scenarios: List[ScenarioResponse]


class ScenarioDetailResponse(FlexibleResponse):
    ok: bool
    scenario: ScenarioResponse
    message: Optional[str] = None
