from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.http.response import Http404
from ninja import NinjaAPI, Schema

from ..models import HazardEvent
from ..services.forecast_state import forecast
from ..services.hazard_service import (
    complete_hazard_action,
    list_hazard_events,
    serialize_hazard_detail,
    serialize_hazard_row,
    start_hazard_action,
)


class HazardActionStartRequest(Schema):
    action_detail: str
    action_type: str = ""


class HazardActionCompleteRequest(Schema):
    result_detail: str = ""
    result_status: str = ""
    recurrence_note: str = ""
    action_detail: str | None = None
    action_type: str | None = None


hazard_api = NinjaAPI(
    title="Hazard API",
    version="1.0.0",
    urls_namespace="hazard_api",
    openapi_url=None,
    docs_url=None,
)


def error_payload(status_code: int, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "httpStatus": status_code,
        "status": "BAD_REQUEST" if status_code == 400 else "NOT_FOUND",
        "message": message,
        "data": None,
    }


@hazard_api.get("/hazards", response={200: list[dict[str, Any]]})
def hazard_list(
    request: HttpRequest,
    status: str = "ALL",
    includeDeleted: bool = True,
) -> list[dict[str, Any]]:
    events = list_hazard_events(status=status, include_deleted=includeDeleted)
    return [serialize_hazard_row(event) for event in events]


@hazard_api.get("/hazards/forecast", response={200: dict[str, Any], 400: dict[str, Any]})
def hazard_forecast(request: HttpRequest, minutes: str | None = None):
    try:
        parsed_minutes = int(minutes or 0) or None
    except ValueError:
        return 400, error_payload(400, "minutes must be numeric.")
    return forecast(parsed_minutes)


@hazard_api.get("/hazards/{hazard_id}", response={200: dict[str, Any], 404: dict[str, Any]})
def hazard_detail(request: HttpRequest, hazard_id: int):
    try:
        event = HazardEvent.objects.get(id=hazard_id)
    except HazardEvent.DoesNotExist:
        return 404, error_payload(404, "Hazard event not found.")
    return serialize_hazard_detail(event)


@hazard_api.post("/hazards/{hazard_id}/actions", response={201: dict[str, Any], 400: dict[str, Any], 404: dict[str, Any]})
def hazard_action_start(request: HttpRequest, hazard_id: int, payload: HazardActionStartRequest):
    try:
        action = start_hazard_action(hazard_id, payload.dict())
    except Http404:
        return 404, error_payload(404, "Hazard event not found.")
    except ValueError as exc:
        return 400, error_payload(400, str(exc))

    return 201, serialize_action_response(action)


@hazard_api.patch(
    "/hazards/{hazard_id}/actions/{action_id}",
    response={200: dict[str, Any], 400: dict[str, Any], 404: dict[str, Any]},
)
def hazard_action_complete(
    request: HttpRequest,
    hazard_id: int,
    action_id: int,
    payload: HazardActionCompleteRequest,
):
    try:
        action = complete_hazard_action(hazard_id, action_id, payload.dict(exclude_none=True))
    except Http404:
        return 404, error_payload(404, "Hazard action not found.")
    except ValueError as exc:
        return 400, error_payload(400, str(exc))

    return serialize_action_response(action)


def serialize_action_response(action) -> dict[str, Any]:
    return {
        "id": action.id,
        "event_id": action.event_id,
        "action_detail": action.action_detail,
        "action_type": action.action_type,
        "result_detail": action.result_detail,
        "result_status": action.result_status,
        "recurrence_note": action.recurrence_note,
        "fastapi_sync": {
            "status": action.fastapi_sync_status,
            "vector_id": action.fastapi_vector_id,
            "error_message": action.fastapi_error_message,
        },
        "event": serialize_hazard_detail(action.event),
    }
