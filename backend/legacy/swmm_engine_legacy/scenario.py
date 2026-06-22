from __future__ import annotations

from copy import deepcopy
from typing import Any


def apply_facility_conditions(
    model: dict[str, Any],
    control: dict[str, Any],
    facilities: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    model = deepcopy(model)
    control = deepcopy(control)
    nodes = model.get("nodes", [])
    links = model.get("links", [])
    blockages = control.setdefault("blockagesById", {})
    obstructions = control.setdefault("obstructionsById", {})

    node_lookup = {}
    for node in nodes:
        for key in (node.get("id"), node.get("swmmId"), node.get("name")):
            if key:
                node_lookup[str(key)] = node

    link_lookup = {}
    for link in links:
        for key in (link.get("id"), link.get("swmmId"), link.get("name")):
            if key:
                link_lookup[str(key)] = link

    for facility in facilities:
        metadata = facility.get("metadata") or {}
        reference = str(
            metadata.get("swmm_id")
            or metadata.get("model_id")
            or facility.get("name")
            or ""
        )
        initial_water = _percentage(metadata.get("initial_water_percent"))
        blockage = _percentage(metadata.get("blockage_percent"))
        obstruction = str(metadata.get("obstruction_type", "")).strip().upper()

        node = node_lookup.get(reference)
        link = link_lookup.get(reference)
        if node is not None and initial_water is not None:
            node.setdefault("props", {})["initialWaterPercent"] = initial_water
        if link is not None and initial_water is not None:
            link.setdefault("props", {})["initialWaterPercent"] = initial_water

        affected_links = []
        if link is not None:
            affected_links.append(link)
        if node is not None and blockage is not None:
            affected_links.extend(
                item
                for item in links
                if item.get("from", {}).get("nodeId") == node.get("id")
            )

        if blockage is not None:
            for affected in affected_links:
                link_id = str(affected.get("id") or affected.get("swmmId"))
                blockages[link_id] = blockage
                if obstruction:
                    obstructions[link_id] = obstruction

    return model, control


def _percentage(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return min(100.0, max(0.0, float(value)))
