from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from django.http.response import Http404
from ninja import NinjaAPI

from ..dtos import (
    ErrorResponse,
    ScenarioCreateRequest,
    ScenarioDetailResponse,
    ScenarioListResponse,
    ScenarioUpdateRequest,
)
from ..services import (
    create_scenario,
    delete_scenario,
    get_scenario,
    list_scenarios,
    serialize_scenario,
    update_scenario,
)


scenario_api = NinjaAPI(
    title="Scenario API",
    version="1.0.0",
    urls_namespace="scenario_api",
)


def error_payload(message: str, detail: Any = None) -> dict[str, Any]:
    return {"ok": False, "message": message, "detail": detail or message}


@scenario_api.exception_handler(Http404)
def not_found(request: HttpRequest, exc: Http404):
    return scenario_api.create_response(request, error_payload("Scenario not found."), status=404)


@scenario_api.exception_handler(ValueError)
def value_error(request: HttpRequest, exc: ValueError):
    return scenario_api.create_response(request, error_payload(str(exc)), status=400)


@scenario_api.get("/scenarios", response={200: ScenarioListResponse, 400: ErrorResponse, 500: ErrorResponse})
def scenario_list(request: HttpRequest, includeInactive: bool = False) -> dict[str, Any]:
    scenarios = list_scenarios(include_inactive=includeInactive)
    return {
        "ok": True,
        "scenarios": [serialize_scenario(scenario) for scenario in scenarios],
    }


@scenario_api.post("/scenarios", response={200: ScenarioDetailResponse, 400: ErrorResponse, 422: ErrorResponse, 500: ErrorResponse})
def scenario_create(request: HttpRequest, payload: ScenarioCreateRequest) -> dict[str, Any]:
    scenario = create_scenario(payload)
    return {
        "ok": True,
        "message": "Scenario created.",
        "scenario": serialize_scenario(scenario),
    }


@scenario_api.get("/scenarios/{scenario_id}", response={200: ScenarioDetailResponse, 404: ErrorResponse, 500: ErrorResponse})
def scenario_detail(request: HttpRequest, scenario_id: int) -> dict[str, Any]:
    scenario = get_scenario(scenario_id)
    return {
        "ok": True,
        "scenario": serialize_scenario(scenario),
    }


@scenario_api.put("/scenarios/{scenario_id}", response={200: ScenarioDetailResponse, 400: ErrorResponse, 404: ErrorResponse, 422: ErrorResponse, 500: ErrorResponse})
def scenario_update(request: HttpRequest, scenario_id: int, payload: ScenarioUpdateRequest) -> dict[str, Any]:
    scenario = update_scenario(scenario_id, payload)
    return {
        "ok": True,
        "message": "Scenario updated.",
        "scenario": serialize_scenario(scenario),
    }


@scenario_api.delete("/scenarios/{scenario_id}", response={200: ScenarioDetailResponse, 404: ErrorResponse, 500: ErrorResponse})
def scenario_delete(request: HttpRequest, scenario_id: int) -> dict[str, Any]:
    scenario = get_scenario(scenario_id)
    delete_scenario(scenario_id)
    scenario.refresh_from_db()
    return {
        "ok": True,
        "message": "Scenario deleted.",
        "scenario": serialize_scenario(scenario),
    }
