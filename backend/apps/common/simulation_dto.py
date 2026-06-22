from dataclasses import dataclass, field
from typing import Any

from .base_dto import BaseResponseDTO


@dataclass
class SimulationRequestDTO:
    rainfall_status: str
    rainfall_amount: float = 0.0
    duration_minutes: int = 0
    parameters: dict[str, Any] = field(default_factory=dict)
    model: dict[str, Any] = field(default_factory=dict)
    control: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimulationResponseDTO(BaseResponseDTO):
    pass
