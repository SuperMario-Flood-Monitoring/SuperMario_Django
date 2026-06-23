from __future__ import annotations

from ninja import Schema
from pydantic import Field


class LoginRequest(Schema):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1)


class AccessTokenResponse(Schema):
    accessToken: str


class AuthErrorResponse(Schema):
    success: bool = False
    httpStatus: int
    status: str
    message: str
    data: object | None = None


class LogoutResponse(Schema):
    success: bool
    httpStatus: int
    status: str
    message: str
    data: object | None = None

