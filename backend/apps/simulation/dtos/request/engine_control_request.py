from __future__ import annotations

from typing import Any, Dict, List, Optional

from ninja import Schema
from pydantic import ConfigDict


class EngineControlRequest(Schema):
    model_config = ConfigDict(extra="allow")

    rainfall: Optional[float] = None
    rainfallRatio: Optional[float] = None
    rainfallPercent: Optional[float] = None
    maxRainfallMmPerHour: Optional[float] = None
    speedMultiplier: Optional[float] = None
    blockagesById: Optional[Dict[str, Any]] = None
    exceptions: Optional[List[Dict[str, Any]]] = None

    def to_control_payload(self) -> Dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)
