from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from django.conf import settings
from django.utils import timezone

from ..models import HazardAction


def build_maintenance_log_payload(action: HazardAction) -> dict[str, str]:
    return {
        "sourceId": action.event.target_id,
        "action_details": action.action_detail,
    }


def dispatch_maintenance_log(action: HazardAction) -> dict[str, Any]:
    action.fastapi_requested_at = timezone.now()
    action.fastapi_sync_status = HazardAction.FastApiSyncStatus.PENDING
    action.fastapi_error_message = ""
    action.save(
        update_fields=[
            "fastapi_requested_at",
            "fastapi_sync_status",
            "fastapi_error_message",
        ]
    )

    try:
        response = post_maintenance_log(build_maintenance_log_payload(action))
    except (TimeoutError, socket.timeout, urllib.error.URLError, OSError) as exc:
        error_message = str(exc)
        action.fastapi_sync_status = HazardAction.FastApiSyncStatus.FAILED
        action.fastapi_error_message = error_message
        action.fastapi_completed_at = timezone.now()
        action.save(
            update_fields=[
                "fastapi_sync_status",
                "fastapi_error_message",
                "fastapi_completed_at",
            ]
        )
        return {
            "ok": False,
            "status": "FAILED",
            "error_message": error_message,
        }

    action.fastapi_sync_status = HazardAction.FastApiSyncStatus.SENT
    action.fastapi_vector_id = str(response.get("vector_id") or response.get("id") or "")
    action.fastapi_error_message = ""
    action.fastapi_completed_at = timezone.now()
    action.save(
        update_fields=[
            "fastapi_sync_status",
            "fastapi_vector_id",
            "fastapi_error_message",
            "fastapi_completed_at",
        ]
    )
    return {
        "ok": True,
        "status": "SENT",
        "vector_id": action.fastapi_vector_id,
        "response": response,
    }


def post_maintenance_log(payload: dict[str, str]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        settings.SUPERMARIO_LLM_MAINTENANCE_LOG_URL,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(
        request,
        timeout=settings.SUPERMARIO_LLM_MAINTENANCE_LOG_TIMEOUT_SECONDS,
    ) as response:
        response_body = response.read(65536).decode("utf-8", errors="replace")

    try:
        parsed = json.loads(response_body) if response_body else {}
    except json.JSONDecodeError:
        parsed = {"raw_response": response_body}
    if not isinstance(parsed, dict):
        return {"raw_response": parsed}
    return parsed
