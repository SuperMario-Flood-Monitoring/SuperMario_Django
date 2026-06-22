from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_SIMULATION_SECONDS = 30


class SwmmContractError(ValueError):
    pass


@dataclass(frozen=True)
class SwmmNode:
    id: str
    swmm_id: str
    name: str
    node_type: str
    x: float
    y: float
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SwmmLink:
    id: str
    swmm_id: str
    name: str
    from_node_id: str
    to_node_id: str
    size: str
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SwmmModel:
    version: int
    ground_surface_y: float
    nodes: tuple[SwmmNode, ...]
    links: tuple[SwmmLink, ...]


@dataclass(frozen=True)
class SwmmControl:
    version: str
    step_seconds: int
    duration_seconds: int | None
    realtime: bool
    broadcast_interval_seconds: float
    rainfall: float
    rainfall_ratio: float
    blockages_by_id: dict[str, float]
    obstructions_by_id: dict[str, str]
    exceptions: tuple[str, ...] = ()

    @property
    def effective_rainfall(self) -> float:
        return self.rainfall * self.rainfall_ratio


def _required_text(payload: dict, field_name: str) -> str:
    value = str(payload.get(field_name, "")).strip()
    if not value:
        raise SwmmContractError(f"'{field_name}' is required.")
    return value


def parse_model(payload: dict) -> SwmmModel:
    if not isinstance(payload, dict):
        raise SwmmContractError("'model' must be a JSON object.")

    raw_nodes = payload.get("nodes")
    raw_links = payload.get("links")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise SwmmContractError("'model.nodes' must contain at least one node.")
    if not isinstance(raw_links, list) or not raw_links:
        raise SwmmContractError("'model.links' must contain at least one link.")

    nodes = []
    node_ids = set()
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise SwmmContractError("Each model node must be a JSON object.")
        node_id = _required_text(raw, "id")
        if node_id in node_ids:
            raise SwmmContractError(f"Duplicate model node id: {node_id}.")
        node_ids.add(node_id)
        try:
            x = float(raw.get("x", 0))
            y = float(raw.get("y", 0))
        except (TypeError, ValueError) as exc:
            raise SwmmContractError("Node coordinates must be numbers.") from exc
        props = raw.get("props", {})
        if not isinstance(props, dict):
            raise SwmmContractError("Node 'props' must be a JSON object.")
        nodes.append(
            SwmmNode(
                id=node_id,
                swmm_id=str(raw.get("swmmId") or node_id).strip(),
                name=str(raw.get("name") or node_id).strip(),
                node_type=str(raw.get("type") or "junction").strip(),
                x=x,
                y=y,
                props=props,
            )
        )

    links = []
    link_ids = set()
    for raw in raw_links:
        if not isinstance(raw, dict):
            raise SwmmContractError("Each model link must be a JSON object.")
        link_id = _required_text(raw, "id")
        if link_id in link_ids:
            raise SwmmContractError(f"Duplicate model link id: {link_id}.")
        link_ids.add(link_id)
        from_data = raw.get("from", {})
        to_data = raw.get("to", {})
        from_node_id = str(from_data.get("nodeId", "")).strip()
        to_node_id = str(to_data.get("nodeId", "")).strip()
        if from_node_id not in node_ids or to_node_id not in node_ids:
            raise SwmmContractError(
                f"Link '{link_id}' references an unknown node."
            )
        if from_node_id == to_node_id:
            raise SwmmContractError(f"Link '{link_id}' cannot connect itself.")
        props = raw.get("props", {})
        if not isinstance(props, dict):
            raise SwmmContractError("Link 'props' must be a JSON object.")
        links.append(
            SwmmLink(
                id=link_id,
                swmm_id=str(raw.get("swmmId") or link_id).strip(),
                name=str(raw.get("name") or link_id).strip(),
                from_node_id=from_node_id,
                to_node_id=to_node_id,
                size=str(raw.get("size") or "medium").lower(),
                props=props,
            )
        )

    try:
        version = int(payload.get("version", 1))
        ground_surface_y = float(payload.get("groundSurfaceY", 330))
    except (TypeError, ValueError) as exc:
        raise SwmmContractError(
            "Model version and groundSurfaceY must be numbers."
        ) from exc
    return SwmmModel(
        version=version,
        ground_surface_y=ground_surface_y,
        nodes=tuple(nodes),
        links=tuple(links),
    )


def parse_control(payload: dict, fallback: dict | None = None) -> SwmmControl:
    if not isinstance(payload, dict):
        raise SwmmContractError("'control' must be a JSON object.")
    fallback = fallback or {}
    realtime = payload.get("realtime", False)
    if not isinstance(realtime, bool):
        raise SwmmContractError("'control.realtime' must be a boolean.")
    try:
        step_seconds = int(
            payload.get("stepSeconds", fallback.get("step_seconds", 60))
        )
        rainfall = float(payload.get("rainfall", fallback.get("rainfall", 0)))
        rainfall_ratio = float(payload.get("rainfallRatio", 1))
        duration_value = payload.get("durationSeconds")
        duration_seconds = (
            int(duration_value) if duration_value is not None else None
        )
        broadcast_interval_seconds = float(
            payload.get("broadcastIntervalSeconds", 1)
        )
    except (TypeError, ValueError) as exc:
        raise SwmmContractError(
            "Control stepSeconds, rainfall and rainfallRatio must be numbers."
        ) from exc
    if step_seconds < 1:
        raise SwmmContractError("'control.stepSeconds' must be at least 1.")
    if duration_seconds is not None and duration_seconds < 1:
        raise SwmmContractError(
            "'control.durationSeconds' must be at least 1."
        )
    if (
        duration_seconds is not None
        and duration_seconds > MAX_SIMULATION_SECONDS
    ):
        raise SwmmContractError(
            f"'control.durationSeconds' cannot exceed "
            f"{MAX_SIMULATION_SECONDS}."
        )
    if broadcast_interval_seconds < 0:
        raise SwmmContractError(
            "'control.broadcastIntervalSeconds' cannot be negative."
        )
    if rainfall < 0 or rainfall_ratio < 0:
        raise SwmmContractError(
            "Control rainfall and rainfallRatio cannot be negative."
        )

    raw_blockages = payload.get("blockagesById", {})
    if not isinstance(raw_blockages, dict):
        raise SwmmContractError("'control.blockagesById' must be an object.")
    blockages = {}
    for link_id, value in raw_blockages.items():
        try:
            blockage = float(value)
        except (TypeError, ValueError) as exc:
            raise SwmmContractError("Blockage values must be numbers.") from exc
        blockages[str(link_id)] = min(100.0, max(0.0, blockage))

    raw_obstructions = payload.get("obstructionsById", {})
    if not isinstance(raw_obstructions, dict):
        raise SwmmContractError("'control.obstructionsById' must be an object.")
    obstructions = {
        str(link_id): str(value).strip().upper()
        for link_id, value in raw_obstructions.items()
        if str(value).strip()
    }

    exceptions = payload.get("exceptions", [])
    if not isinstance(exceptions, list):
        raise SwmmContractError("'control.exceptions' must be an array.")
    return SwmmControl(
        version=str(payload.get("version", "swmm-control-v1")),
        step_seconds=step_seconds,
        duration_seconds=duration_seconds,
        realtime=realtime,
        broadcast_interval_seconds=broadcast_interval_seconds,
        rainfall=rainfall,
        rainfall_ratio=rainfall_ratio,
        blockages_by_id=blockages,
        obstructions_by_id=obstructions,
        exceptions=tuple(str(item) for item in exceptions),
    )
