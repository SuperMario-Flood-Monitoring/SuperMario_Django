from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from ninja import NinjaAPI, Schema

from ..models import NotificationRecipient


class NotificationRecipientRequest(Schema):
    employee_name: str
    chat_id: str


notification_api = NinjaAPI(
    title="Notification API",
    version="1.0.0",
    urls_namespace="notification_api",
)


def response_payload(status_code: int, status: str, message: str, data: Any = None) -> dict[str, Any]:
    return {
        "success": 200 <= status_code < 300,
        "httpStatus": status_code,
        "status": status,
        "message": message,
        "data": data,
    }


def serialize_recipient(recipient: NotificationRecipient) -> dict[str, Any]:
    return {
        "id": recipient.id,
        "employee_name": recipient.employee_name,
        "chat_id": recipient.chat_id,
    }


def validate_recipient_payload(payload: NotificationRecipientRequest) -> dict[str, str]:
    employee_name = payload.employee_name.strip()
    chat_id = payload.chat_id.strip()
    if not employee_name:
        raise ValueError("employee_name은 필수입니다.")
    if not chat_id:
        raise ValueError("chat_id는 필수입니다.")
    return {
        "employee_name": employee_name,
        "chat_id": chat_id,
    }


@notification_api.exception_handler(ValueError)
def value_error(request: HttpRequest, exc: ValueError):
    return notification_api.create_response(
        request,
        response_payload(400, "BAD_REQUEST", str(exc)),
        status=400,
    )


@notification_api.get("/list", response={200: dict[str, Any]})
def list_notification_recipients(request: HttpRequest) -> dict[str, Any]:
    recipients = NotificationRecipient.objects.all()
    return response_payload(
        200,
        "OK",
        "Notification recipients found.",
        [serialize_recipient(recipient) for recipient in recipients],
    )


@notification_api.post("/", response={201: dict[str, Any], 400: dict[str, Any]})
def create_notification_recipient(
    request: HttpRequest,
    payload: NotificationRecipientRequest,
):
    data = validate_recipient_payload(payload)
    recipient = NotificationRecipient.objects.create(**data)
    return 201, response_payload(
        201,
        "CREATED",
        "Notification recipient created.",
        serialize_recipient(recipient),
    )


@notification_api.delete("/{recipient_id}", response={200: dict[str, Any], 404: dict[str, Any]})
def delete_notification_recipient(request: HttpRequest, recipient_id: int):
    try:
        recipient = NotificationRecipient.objects.get(id=recipient_id)
    except NotificationRecipient.DoesNotExist:
        return 404, response_payload(404, "NOT_FOUND", "Notification recipient not found.")

    recipient.delete()
    return response_payload(200, "OK", "Notification recipient deleted.")
