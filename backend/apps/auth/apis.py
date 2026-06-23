from __future__ import annotations

import hmac
from typing import Any

from django.contrib.auth.hashers import check_password
from django.http import HttpRequest
from ninja import NinjaAPI

from .cookies import REFRESH_COOKIE_NAME, clear_refresh_cookie, set_refresh_cookie
from .dtos import AccessTokenResponse, AuthErrorResponse, LoginRequest, LogoutResponse
from .models import User
from .tokens import TokenError, TokenExpired, decode_token, hash_refresh_token, issue_token


auth_api = NinjaAPI(
    title="Auth API",
    version="1.0.0",
    urls_namespace="custom_auth_api",
)


def error_payload(http_status: int, status: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "httpStatus": http_status,
        "status": status,
        "message": message,
        "data": None,
    }


def token_response(request: HttpRequest, user: User) -> Any:
    access_token = issue_token(user, "access")
    refresh_token = issue_token(user, "refresh")
    user.refresh_token = hash_refresh_token(refresh_token)
    user.save(update_fields=["refresh_token"])
    response = auth_api.create_response(request, {"accessToken": access_token}, status=200)
    set_refresh_cookie(response, refresh_token)
    return response


def clear_user_refresh_token(user_id: int | None) -> None:
    if user_id is None:
        return
    User.objects.filter(user_id=user_id).update(refresh_token=None)


@auth_api.post(
    "/login",
    response={200: AccessTokenResponse, 401: AuthErrorResponse},
)
def login(request: HttpRequest, payload: LoginRequest):
    username = payload.username.strip()
    try:
        user = User.objects.get(username=username)
    except User.DoesNotExist:
        return auth_api.create_response(
            request,
            error_payload(401, "UNAUTHORIZED", "아이디 또는 비밀번호가 일치하지 않습니다."),
            status=401,
        )

    if user.role != User.Role.ADMIN or not check_password(payload.password, user.password):
        return auth_api.create_response(
            request,
            error_payload(401, "UNAUTHORIZED", "아이디 또는 비밀번호가 일치하지 않습니다."),
            status=401,
        )

    return token_response(request, user)


@auth_api.post(
    "/refresh",
    response={200: AccessTokenResponse, 403: AuthErrorResponse},
)
def refresh(request: HttpRequest):
    refresh_token = request.COOKIES.get(REFRESH_COOKIE_NAME)
    user_id: int | None = None
    try:
        if not refresh_token:
            raise TokenError("Refresh token is required.")
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = int(payload.get("sub"))
        user = User.objects.get(user_id=user_id)
        stored_hash = user.refresh_token or ""
        incoming_hash = hash_refresh_token(refresh_token)
        if not stored_hash or not hmac.compare_digest(stored_hash, incoming_hash):
            clear_user_refresh_token(user.user_id)
            raise TokenError("Refresh token mismatch.")
    except (TokenError, TokenExpired, User.DoesNotExist, TypeError, ValueError):
        clear_user_refresh_token(user_id)
        response = auth_api.create_response(
            request,
            error_payload(403, "FORBIDDEN", "토큰을 재발급할 수 없습니다."),
            status=403,
        )
        clear_refresh_cookie(response)
        return response

    return token_response(request, user)


@auth_api.post(
    "/logout",
    response={200: LogoutResponse},
)
def logout(request: HttpRequest):
    user = getattr(request, "auth_user", None)
    if isinstance(user, User):
        user.refresh_token = None
        user.save(update_fields=["refresh_token"])

    response = auth_api.create_response(
        request,
        {
            "success": True,
            "httpStatus": 200,
            "status": "OK",
            "message": "Logged out.",
            "data": None,
        },
        status=200,
    )
    clear_refresh_cookie(response)
    return response

