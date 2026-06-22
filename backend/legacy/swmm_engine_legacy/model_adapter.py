from __future__ import annotations

from copy import deepcopy
from typing import Any


def normalize_model_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert supported client model payloads into the engine graph contract."""
    if not isinstance(payload, dict):
        raise ValueError("'model' must be a JSON object.")

    if _looks_like_graph_model(payload):
        model = deepcopy(payload)
        model.setdefault("inputFormat", "ui-graph-v1")
        return model

    swmm_payload = payload.get("swmm") if isinstance(payload.get("swmm"), dict) else payload
    if _looks_like_swmm_sections(swmm_payload):
        return _swmm_sections_to_graph(swmm_payload, payload)

    raise ValueError(
        "'model' must be either the UI graph contract with nodes/links or "
        "the SWMM section contract with junctions/outfalls/conduits."
    )


def _looks_like_graph_model(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("nodes"), list) and isinstance(
        payload.get("links"), list
    )


def _looks_like_swmm_sections(payload: dict[str, Any]) -> bool:
    return (
        isinstance(payload.get("junctions"), list)
        or isinstance(payload.get("outfalls"), list)
    ) and isinstance(payload.get("conduits"), list)


def _swmm_sections_to_graph(
    swmm: dict[str, Any],
    original_payload: dict[str, Any],
) -> dict[str, Any]:
    nodes = []
    seen_node_ids = set()
    for raw in swmm.get("junctions", []):
        node = _node_from_section(raw, "junction")
        nodes.append(node)
        seen_node_ids.add(node["id"])
    for raw in swmm.get("outfalls", []):
        node = _node_from_section(raw, "outfall")
        nodes.append(node)
        seen_node_ids.add(node["id"])

    links = []
    for raw in swmm.get("conduits", []):
        if not isinstance(raw, dict):
            raise ValueError("Each SWMM conduit must be a JSON object.")
        link_id = _required_text(raw, "id", "SWMM conduit")
        from_node = _required_text(raw, "from_node", f"SWMM conduit '{link_id}'")
        to_node = _required_text(raw, "to_node", f"SWMM conduit '{link_id}'")
        if from_node not in seen_node_ids or to_node not in seen_node_ids:
            raise ValueError(f"SWMM conduit '{link_id}' references an unknown node.")

        xsection = raw.get("xsection", {})
        if xsection is None:
            xsection = {}
        if not isinstance(xsection, dict):
            raise ValueError(f"SWMM conduit '{link_id}' xsection must be an object.")
        diameter = _optional_float(
            xsection.get("diameter", xsection.get("geom1")),
            f"SWMM conduit '{link_id}' xsection diameter",
        )

        props = {
            "length": _optional_float(raw.get("length"), f"SWMM conduit '{link_id}' length"),
            "roughness": _optional_float(
                raw.get("roughness"),
                f"SWMM conduit '{link_id}' roughness",
            ),
            "slope": _optional_float(raw.get("slope"), f"SWMM conduit '{link_id}' slope"),
            "blockage": _optional_float(
                raw.get("blockage_percent", raw.get("blockage")),
                f"SWMM conduit '{link_id}' blockage",
            ),
            "initialWaterPercent": _optional_float(
                raw.get("initial_water_percent"),
                f"SWMM conduit '{link_id}' initial water percent",
            ),
        }
        if diameter is not None:
            props["diameter"] = diameter
        props = {key: value for key, value in props.items() if value is not None}

        links.append(
            {
                "id": link_id,
                "swmmId": str(raw.get("swmm_id") or raw.get("swmmId") or link_id),
                "name": str(raw.get("name") or link_id),
                "type": "conduit",
                "from": {"nodeId": from_node},
                "to": {"nodeId": to_node},
                "size": str(raw.get("size") or "medium").lower(),
                "props": props,
            }
        )

    return {
        "version": int(original_payload.get("version", swmm.get("version", 1))),
        "inputFormat": "swmm-section-v1",
        "groundSurfaceY": float(
            original_payload.get("groundSurfaceY", swmm.get("groundSurfaceY", 330))
        ),
        "nodes": nodes,
        "links": links,
    }


def _node_from_section(raw: dict[str, Any], node_type: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"Each SWMM {node_type} must be a JSON object.")
    node_id = _required_text(raw, "id", f"SWMM {node_type}")
    x = _optional_float(raw.get("x"), f"SWMM {node_type} '{node_id}' x") or 0.0
    y = _optional_float(raw.get("y"), f"SWMM {node_type} '{node_id}' y") or 0.0
    props = {
        "invertElevation": _optional_float(
            raw.get("elevation", raw.get("invert_elevation")),
            f"SWMM {node_type} '{node_id}' elevation",
        ),
        "maxDepth": _optional_float(
            raw.get("max_depth", raw.get("maxDepth")),
            f"SWMM {node_type} '{node_id}' max depth",
        ),
        "initialDepth": _optional_float(
            raw.get("initial_depth", raw.get("initialDepth")),
            f"SWMM {node_type} '{node_id}' initial depth",
        ),
        "initialWaterPercent": _optional_float(
            raw.get("initial_water_percent"),
            f"SWMM {node_type} '{node_id}' initial water percent",
        ),
    }
    catchment = raw.get("catchment", {})
    if catchment is None:
        catchment = {}
    if not isinstance(catchment, dict):
        raise ValueError(f"SWMM {node_type} '{node_id}' catchment must be an object.")
    catchment_map = {
        "area": "catchmentArea",
        "impervious": "impervious",
        "width": "catchmentWidth",
        "slope": "catchmentSlope",
    }
    for source, target in catchment_map.items():
        value = _optional_float(
            catchment.get(source),
            f"SWMM {node_type} '{node_id}' catchment {source}",
        )
        if value is not None:
            props[target] = value

    return {
        "id": node_id,
        "swmmId": str(raw.get("swmm_id") or raw.get("swmmId") or node_id),
        "name": str(raw.get("name") or node_id),
        "type": node_type,
        "x": x,
        "y": y,
        "props": {key: value for key, value in props.items() if value is not None},
    }


def _required_text(payload: dict[str, Any], field_name: str, owner: str) -> str:
    value = str(payload.get(field_name, "")).strip()
    if not value:
        raise ValueError(f"'{field_name}' is required for {owner}.")
    return value


def _optional_float(value: Any, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number.") from exc
