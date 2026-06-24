from __future__ import annotations

from typing import Any, Dict, Optional

from ninja import Schema
from pydantic import ConfigDict, model_validator


DEFAULT_EDITOR_CONVERT_TITLE = "React editor layout에서 생성한 SWMM model"


class EditorConvertRequest(Schema):
    model_config = ConfigDict(extra="allow")

    layout: Optional[Dict[str, Any]] = None
    title: str = DEFAULT_EDITOR_CONVERT_TITLE
    filename: Optional[str] = None

    @model_validator(mode="after")
    def ensure_layout(self) -> "EditorConvertRequest":
        if self.layout is None:
            extras = dict(self.model_extra or {})
            extras.pop("title", None)
            extras.pop("filename", None)
            if not extras:
                raise ValueError("layout은 JSON object여야 합니다.")
            self.layout = extras
        return self

    @property
    def layout_payload(self) -> Dict[str, Any]:
        return self.layout or {}

    @property
    def title_value(self) -> str:
        return self.title or DEFAULT_EDITOR_CONVERT_TITLE

    @property
    def filename_value(self) -> Optional[str]:
        return self.filename or None
