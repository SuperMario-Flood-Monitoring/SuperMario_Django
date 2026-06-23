from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse


REFRESH_COOKIE_NAME = "refresh_token"


def refresh_cookie_options() -> dict:
    return {
        "httponly": True,
        "secure": getattr(settings, "SUPERMARIO_REFRESH_COOKIE_SECURE", True),
        "samesite": getattr(settings, "SUPERMARIO_REFRESH_COOKIE_SAMESITE", "Lax"),
        "path": "/api/auth",
        "max_age": 7 * 24 * 60 * 60,
    }


def set_refresh_cookie(response: HttpResponse, token: str) -> None:
    response.set_cookie(REFRESH_COOKIE_NAME, token, **refresh_cookie_options())


def clear_refresh_cookie(response: HttpResponse) -> None:
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path="/api/auth",
        samesite=getattr(settings, "SUPERMARIO_REFRESH_COOKIE_SAMESITE", "Lax"),
    )

