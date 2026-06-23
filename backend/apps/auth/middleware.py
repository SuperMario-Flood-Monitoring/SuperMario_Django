from __future__ import annotations

from django.http import JsonResponse

from .models import User
from .tokens import TokenError, TokenExpired, decode_token


EXEMPT_API_PREFIXES = (
    "/api/auth/login",
    "/api/auth/refresh",
    "/api/engine/health",
)


class ApiJwtAuthenticationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if not path.startswith("/api/") or self.is_exempt(path):
            return self.get_response(request)

        token = self.extract_access_token(request)
        if not token:
            return self.unauthorized("Access token is required.")

        try:
            payload = decode_token(token, expected_type="access")
            user = User.objects.get(user_id=int(payload.get("sub")), role=User.Role.ADMIN)
        except (TokenError, TokenExpired, User.DoesNotExist, TypeError, ValueError):
            return self.unauthorized("Access token is invalid or expired.")

        request.auth_user = user
        request.auth_payload = payload
        return self.get_response(request)

    @staticmethod
    def is_exempt(path: str) -> bool:
        return any(path.startswith(prefix) for prefix in EXEMPT_API_PREFIXES)

    @staticmethod
    def extract_access_token(request) -> str | None:
        header = request.headers.get("Authorization", "").strip()
        if not header:
            return None
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return header

    @staticmethod
    def unauthorized(message: str) -> JsonResponse:
        return JsonResponse(
            {
                "success": False,
                "httpStatus": 401,
                "status": "UNAUTHORIZED",
                "message": message,
                "data": None,
            },
            status=401,
        )

