from __future__ import annotations

import json
from dataclasses import asdict

from django.db import transaction
from django.http import JsonResponse
from django.views import View

from apps.common import FacilityRequestDTO, FacilityResponseDTO

from .models import Facility


def _response(code: int, message: str, status: str, data=None) -> JsonResponse:
    body = FacilityResponseDTO(
        code=code,
        message=message,
        status=status,
        data=data,
    )
    return JsonResponse(asdict(body), status=code)


def _serialize(facility: Facility) -> dict:
    return {
        "id": facility.id,
        "name": facility.name,
        "facility_type": facility.facility_type,
        "location": facility.location,
        "normal_value": facility.normal_value,
        "unit": facility.unit,
        "metadata": facility.metadata,
        "is_active": facility.is_active,
        "created_at": facility.created_at.isoformat(),
        "updated_at": facility.updated_at.isoformat(),
    }


def _parse_body(request) -> dict | list:
    if not request.body:
        raise ValueError("Request body is required.")
    try:
        return json.loads(request.body)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Request body must be valid JSON.") from exc


def _validate(payload: dict) -> FacilityRequestDTO:
    if not isinstance(payload, dict):
        raise ValueError("Each facility must be a JSON object.")

    name = str(payload.get("name", "")).strip()
    facility_type = str(payload.get("facility_type", Facility.Type.OTHER)).upper()
    if not name:
        raise ValueError("'name' is required.")
    if facility_type not in Facility.Type.values:
        raise ValueError(
            f"'facility_type' must be one of: {', '.join(Facility.Type.values)}."
        )

    try:
        normal_value = float(payload.get("normal_value", 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("'normal_value' must be a number.") from exc

    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("'metadata' must be a JSON object.")

    return FacilityRequestDTO(
        name=name,
        facility_type=facility_type,
        location=str(payload.get("location", "")).strip(),
        normal_value=normal_value,
        unit=str(payload.get("unit", "")).strip(),
        metadata=metadata,
    )


class FacilitiesView(View):
    def get(self, request):
        facilities = Facility.objects.all()
        return _response(
            200,
            "Facilities found.",
            "OK",
            [_serialize(facility) for facility in facilities],
        )

    def post(self, request):
        try:
            body = _parse_body(request)
            is_bulk = isinstance(body, list) or (
                isinstance(body, dict) and "facilities" in body
            )
            payloads = (
                body.get("facilities", [])
                if isinstance(body, dict) and "facilities" in body
                else body
            )
            payloads = payloads if isinstance(payloads, list) else [payloads]
            if not payloads:
                raise ValueError("At least one facility is required.")
            requests = [_validate(payload) for payload in payloads]
        except ValueError as exc:
            return _response(400, str(exc), "BAD_REQUEST")

        facilities = []
        with transaction.atomic():
            for item in requests:
                facility, _ = Facility.objects.update_or_create(
                    name=item.name,
                    defaults={
                        "facility_type": item.facility_type,
                        "location": item.location,
                        "normal_value": item.normal_value,
                        "unit": item.unit,
                        "metadata": item.metadata,
                        "is_active": True,
                    },
                )
                facilities.append(facility)

        data = [_serialize(facility) for facility in facilities]
        return _response(
            200,
            "Facility initial state saved.",
            "OK",
            data if is_bulk else data[0],
        )


class FacilityDetailView(View):
    def get(self, request, facility_id: int):
        try:
            facility = Facility.objects.get(id=facility_id)
        except Facility.DoesNotExist:
            return _response(404, "Facility not found.", "NOT_FOUND")
        return _response(200, "Facility found.", "OK", _serialize(facility))

    def put(self, request, facility_id: int):
        try:
            facility = Facility.objects.get(id=facility_id)
            item = _validate(_parse_body(request))
        except Facility.DoesNotExist:
            return _response(404, "Facility not found.", "NOT_FOUND")
        except ValueError as exc:
            return _response(400, str(exc), "BAD_REQUEST")

        facility.name = item.name
        facility.facility_type = item.facility_type
        facility.location = item.location
        facility.normal_value = item.normal_value
        facility.unit = item.unit
        facility.metadata = item.metadata
        facility.save()
        return _response(200, "Facility updated.", "OK", _serialize(facility))

    def delete(self, request, facility_id: int):
        try:
            facility = Facility.objects.get(id=facility_id)
        except Facility.DoesNotExist:
            return _response(404, "Facility not found.", "NOT_FOUND")
        facility.delete()
        return _response(200, "Facility deleted.", "OK")
