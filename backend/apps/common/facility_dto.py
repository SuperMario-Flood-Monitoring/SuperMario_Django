from dataclasses import dataclass, field
from typing import Any

from .base_dto import BaseResponseDTO


@dataclass
class FacilityRequestDTO:
    name: str
    facility_type: str
    location: str = ""
    normal_value: float = 0.0
    unit: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FacilityResponseDTO(BaseResponseDTO):
    pass
