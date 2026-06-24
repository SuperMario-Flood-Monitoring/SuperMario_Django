from __future__ import annotations

import json
from typing import Any

from django.http import JsonResponse
from django.views import View

from .models import NotificationRecipient


def response(status_code: int, status: str, message: str, data: Any = None) -> JsonResponse:
    return JsonResponse(
        {
            "success": 200 <= status_code < 300,
            "httpStatus": status_code,
            "status": status,
            "message": message,
            "data": data,
        },
        status=status_code,
    )


def parse_body(request) -> dict[str, Any]:
    if not request.body:
        raise ValueError("요청 body가 필요합니다.")
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError as exc:
        raise ValueError("요청 body는 JSON이어야 합니다.") from exc
    if not isinstance(payload, dict):
        raise ValueError("요청 body는 JSON object여야 합니다.")
    return payload


def serialize_recipient(recipient: NotificationRecipient) -> dict[str, Any]:
    return {
        "id": recipient.id,
        "employee_name": recipient.employee_name,
        "chat_id": recipient.chat_id,
    }


def validate_recipient_payload(payload: dict[str, Any]) -> dict[str, str]:
    employee_name = str(payload.get("employee_name") or "").strip()
    chat_id = str(payload.get("chat_id") or "").strip()
    if not employee_name:
        raise ValueError("employee_name은 필수입니다.")
    if not chat_id:
        raise ValueError("chat_id는 필수입니다.")
    return {
        "employee_name": employee_name,
        "chat_id": chat_id,
    }


class NotificationRecipientListView(View):
    def get(self, request):
        recipients = NotificationRecipient.objects.all()
        return response(
            200,
            "OK",
            "Notification recipients found.",
            [serialize_recipient(recipient) for recipient in recipients],
        )

    def post(self, request):
        try:
            data = validate_recipient_payload(parse_body(request))
        except ValueError as exc:
            return response(400, "BAD_REQUEST", str(exc))

        recipient = NotificationRecipient.objects.create(**data)
        return response(
            201,
            "CREATED",
            "Notification recipient created.",
            serialize_recipient(recipient),
        )


class NotificationRecipientDetailView(View):
    def delete(self, request, recipient_id: int):
        try:
            recipient = NotificationRecipient.objects.get(id=recipient_id)
        except NotificationRecipient.DoesNotExist:
            return response(404, "NOT_FOUND", "Notification recipient not found.")

        recipient.delete()
        return response(200, "OK", "Notification recipient deleted.")
