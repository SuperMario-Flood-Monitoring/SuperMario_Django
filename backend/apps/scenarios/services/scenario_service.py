from __future__ import annotations

from typing import Any, Dict, List

from django.shortcuts import get_object_or_404

from ..dtos import ScenarioCreateRequest, ScenarioUpdateRequest
from ..models import Scenario


def serialize_scenario(scenario: Scenario) -> Dict[str, Any]:
    return {
        "id": scenario.id,
        "title": scenario.title,
        "description": scenario.description,
        "layoutJson": scenario.layout_json,
        "version": scenario.version,
        "isActive": scenario.is_active,
        "createdAt": scenario.created_at,
        "updatedAt": scenario.updated_at,
    }


def list_scenarios(include_inactive: bool = False) -> List[Scenario]:
    queryset = Scenario.objects.all()
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return list(queryset)


def get_scenario(scenario_id: int) -> Scenario:
    return get_object_or_404(Scenario, id=scenario_id)


def create_scenario(payload: ScenarioCreateRequest) -> Scenario:
    return Scenario.objects.create(
        title=payload.title,
        description=payload.description,
        layout_json=payload.layoutJson,
    )


def update_scenario(scenario_id: int, payload: ScenarioUpdateRequest) -> Scenario:
    scenario = get_scenario(scenario_id)
    changed_layout = False

    if payload.title is not None:
        scenario.title = payload.title
    if payload.description is not None:
        scenario.description = payload.description
    if payload.layoutJson is not None:
        scenario.layout_json = payload.layoutJson
        changed_layout = True
    if payload.isActive is not None:
        scenario.is_active = payload.isActive
    if changed_layout:
        scenario.version += 1

    scenario.save()
    return scenario


def delete_scenario(scenario_id: int) -> None:
    scenario = get_scenario(scenario_id)
    scenario.is_active = False
    scenario.save(update_fields=["is_active", "updated_at"])
