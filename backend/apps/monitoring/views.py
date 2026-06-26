from __future__ import annotations

import json
from typing import Any

from django.http import JsonResponse
from django.http.response import Http404
from django.views import View

from .models import HazardEvent
from .services.forecast_state import forecast
from .services.hazard_service import (
    complete_hazard_action,
    list_hazard_events,
    serialize_hazard_detail,
    serialize_hazard_row,
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


def error_response(status_code: int, message: str) -> JsonResponse:
    return JsonResponse(
        {
            "success": False,
            "httpStatus": status_code,
            "status": "BAD_REQUEST" if status_code == 400 else "NOT_FOUND",
            "message": message,
            "data": None,
        },
        status=status_code,
    )


class HazardListView(View):
    def get(self, request):
        status = str(request.GET.get("status") or HazardEvent.Status.OPEN).upper()
        include_deleted = str(request.GET.get("includeDeleted") or "false").lower() == "true"
        events = list_hazard_events(status=status, include_deleted=include_deleted)
        return JsonResponse([serialize_hazard_row(event) for event in events], safe=False)


class HazardForecastView(View):
    def get(self, request):
        try:
            minutes = int(request.GET.get("minutes") or 0) or None
        except ValueError:
            return error_response(400, "minutes는 숫자여야 합니다.")
        return JsonResponse(forecast(minutes))


class HazardDetailView(View):
    def get(self, request, hazard_id: int):
        try:
            event = HazardEvent.objects.get(id=hazard_id)
        except HazardEvent.DoesNotExist:
            return error_response(404, "Hazard event not found.")
        return JsonResponse(serialize_hazard_detail(event))


class HazardActionView(View):
    def post(self, request, hazard_id: int):
        try:
            action = complete_hazard_action(hazard_id, parse_body(request))
        except Http404:
            return error_response(404, "Hazard event not found.")
        except ValueError as exc:
            return error_response(400, str(exc))

        return JsonResponse(
            {
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
            },
            status=201,
        )
