import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class BaseResponseDTO:
    code: int
    message: str
    status: str
    data: Any = None

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
