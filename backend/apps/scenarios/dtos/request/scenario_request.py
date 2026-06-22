from __future__ import annotations

from typing import Any, Dict, Optional

from ninja import Schema
from pydantic import Field, field_validator


class ScenarioCreateRequest(Schema):
    title: str = Field(..., min_length=1, max_length=100)
    description: str = ""
    layoutJson: Dict[str, Any]

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        title = value.strip()
        if not title:
            raise ValueError("title is required.")
        return title

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        return value.strip()


class ScenarioUpdateRequest(Schema):
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    layoutJson: Optional[Dict[str, Any]] = None
    isActive: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        title = value.strip()
        if not title:
            raise ValueError("title is required.")
        return title

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value is not None else None
