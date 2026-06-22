from __future__ import annotations

from typing import Any, Dict, Optional

from ninja import Field, Schema
from pydantic import ConfigDict


class EngineStartRequest(Schema):
    model_config = ConfigDict(extra="allow")

    layout: Optional[Dict[str, Any]] = None
    stepSeconds: int = Field(1, ge=1)
    maxRainfallMmPerHour: float = 100.0
    control: Optional[Dict[str, Any]] = None

    def to_engine_payload(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)
