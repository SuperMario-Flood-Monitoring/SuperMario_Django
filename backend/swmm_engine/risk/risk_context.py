"""Validate SWMM runtime snapshots and build risk-focused context packets.

This module intentionally has no FastAPI/PySWMM dependency so it can be reused
from FastAPI, Django, Channels, a background worker, or tests.

이 Django-package copy는 `swmm.interface.validate_snapshot()`,
`detect_risks()`, `build_llm_context()`의 내부 구현이다. 장고 view,
Channels consumer, Celery worker는 가능하면 이 파일을 직접 import하지 않고
공개 인터페이스인 `swmm.interface`를 통해 호출한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal


ContextLevel = Literal["optimal", "medium", "full"]
Severity = Literal["NORMAL", "WATCH", "WARNING", "CRITICAL"]

REVERSE_FLOW_EPSILON_CMS = -0.0005
FLOODING_EPSILON_CMS = 0.000001
WATCH_FILL_RATIO = 0.50
WARNING_FILL_RATIO = 0.70
CRITICAL_FILL_RATIO = 0.90
SURCHARGE_RATIO = 1.00
CAPACITY_WARNING_RATIO = 1.00
CAPACITY_CRITICAL_RATIO = 1.25
BLOCKAGE_WATCH_RATIO = 0.50
BLOCKAGE_WARNING_RATIO = 0.80
BLOCKAGE_CRITICAL_RATIO = 1.00

REQUIRED_SNAPSHOT_KEYS = ("nodes", "links", "editorObjects", "summary")
NODE_NUMERIC_FIELDS = ("depthM", "headM", "invertElevationM", "depthRatio", "totalInflowCms", "floodingCms")
LINK_NUMERIC_FIELDS = (
    "flowCms",
    "velocityMps",
    "depthM",
    "fullness",
    "capacityCms",
    "capacityRatio",
    "targetSetting",
    "currentSetting",
    "blockageRatio",
)
EDITOR_NUMERIC_FIELDS = (
    "maxDepthRatio",
    "maxFullness",
    "maxCapacityRatio",
    "maxBlockageRatio",
    "maxFloodingCms",
    "flowCms",
    "maxVelocityMps",
    "totalInflowCms",
)

SEVERITY_RANK: dict[str, int] = {
    "NORMAL": 0,
    "WATCH": 1,
    "WARNING": 2,
    "CRITICAL": 3,
}


def validate_swmm_snapshot(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the runtime snapshot shape before downstream risk analysis."""

    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(payload, Mapping):
        return {
            "ok": False,
            "errors": ["snapshot must be a mapping"],
            "warnings": [],
            "counts": {"nodes": 0, "links": 0, "editorObjects": 0},
        }

    for key in REQUIRED_SNAPSHOT_KEYS:
        if key not in payload:
            errors.append(f"missing required key: {key}")

    nodes = _mapping_or_empty(payload.get("nodes"))
    links = _mapping_or_empty(payload.get("links"))
    editor_objects = _mapping_or_empty(payload.get("editorObjects"))

    if "nodes" in payload and not isinstance(payload.get("nodes"), Mapping):
        errors.append("nodes must be a mapping")
    if "links" in payload and not isinstance(payload.get("links"), Mapping):
        errors.append("links must be a mapping")
    if "editorObjects" in payload and not isinstance(payload.get("editorObjects"), Mapping):
        errors.append("editorObjects must be a mapping")
    if "summary" in payload and not isinstance(payload.get("summary"), Mapping):
        errors.append("summary must be a mapping")

    _validate_numeric_records("nodes", nodes, NODE_NUMERIC_FIELDS, errors, warnings)
    _validate_numeric_records("links", links, LINK_NUMERIC_FIELDS, errors, warnings)
    _validate_numeric_records("editorObjects", editor_objects, EDITOR_NUMERIC_FIELDS, errors, warnings)

    for link_id, state in links.items():
        direction = state.get("direction") if isinstance(state, Mapping) else None
        if direction not in (None, "forward", "reverse"):
            warnings.append(f"links.{link_id}.direction should be forward or reverse")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "nodes": len(nodes),
            "links": len(links),
            "editorObjects": len(editor_objects),
        },
    }


def evaluate_swmm_risk(
    snapshot: Mapping[str, Any],
    previous_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create deterministic risk events from a SWMM runtime snapshot."""

    validation = validate_swmm_snapshot(snapshot)
    nodes = _mapping_or_empty(snapshot.get("nodes"))
    links = _mapping_or_empty(snapshot.get("links"))
    editor_objects = _mapping_or_empty(snapshot.get("editorObjects"))

    events: list[dict[str, Any]] = []
    counters = _copy_previous_counters(previous_state)

    for node_id, state in nodes.items():
        flooding = _number(state.get("floodingCms"))
        depth_ratio = _number(state.get("depthRatio"))
        inflow = _number(state.get("totalInflowCms"))

        if flooding > FLOODING_EPSILON_CMS:
            events.append(_event(
                "FLOODING_DETECTED",
                "CRITICAL",
                "node",
                node_id,
                {"floodingCms": flooding, "depthRatio": depth_ratio, "totalInflowCms": inflow},
                "SWMM reported external flooding at this node.",
            ))
        elif depth_ratio >= SURCHARGE_RATIO:
            events.append(_event(
                "NODE_SURCHARGE",
                "WARNING",
                "node",
                node_id,
                {"depthRatio": depth_ratio, "totalInflowCms": inflow},
                "Node depth is at or above its modeled max depth.",
            ))
        elif depth_ratio >= CRITICAL_FILL_RATIO:
            events.append(_event(
                "NODE_HIGH_WATER",
                "WARNING",
                "node",
                node_id,
                {"depthRatio": depth_ratio, "totalInflowCms": inflow},
                "Node water level is near the critical range.",
            ))
        elif depth_ratio >= WARNING_FILL_RATIO:
            events.append(_event(
                "NODE_RISING_WATER",
                "WATCH",
                "node",
                node_id,
                {"depthRatio": depth_ratio, "totalInflowCms": inflow},
                "Node water level is elevated.",
            ))

    for link_id, state in links.items():
        flow = _number(state.get("flowCms"))
        fullness = _number(state.get("fullness"))
        capacity_ratio = _number(state.get("capacityRatio"))
        blockage_ratio = _number(state.get("blockageRatio"))
        direction = state.get("direction")
        is_reverse = direction == "reverse" or flow < REVERSE_FLOW_EPSILON_CMS
        if is_reverse:
            reverse_key = f"reverse:{link_id}"
            counters[reverse_key] = int(counters.get(reverse_key, 0)) + 1
            events.append(_event(
                "REVERSE_FLOW",
                "WARNING" if counters[reverse_key] < 3 else "CRITICAL",
                "link",
                link_id,
                {"flowCms": flow, "fullness": fullness, "capacityRatio": capacity_ratio, "reverseTicks": counters[reverse_key]},
                "Link flow is negative relative to the modeled link direction.",
            ))
        else:
            counters[f"reverse:{link_id}"] = 0

        if fullness >= SURCHARGE_RATIO:
            events.append(_event(
                "LINK_SURCHARGE",
                "WARNING",
                "link",
                link_id,
                {"fullness": fullness, "flowCms": flow, "capacityRatio": capacity_ratio},
                "Link depth is at or above full pipe depth.",
            ))
        elif fullness >= CRITICAL_FILL_RATIO:
            events.append(_event(
                "LINK_HIGH_FILL",
                "WARNING",
                "link",
                link_id,
                {"fullness": fullness, "flowCms": flow, "capacityRatio": capacity_ratio},
                "Link fill ratio is near the critical range.",
            ))
        elif fullness >= WARNING_FILL_RATIO:
            events.append(_event(
                "LINK_ELEVATED_FILL",
                "WATCH",
                "link",
                link_id,
                {"fullness": fullness, "flowCms": flow, "capacityRatio": capacity_ratio},
                "Link fill ratio is elevated.",
            ))

        if capacity_ratio >= CAPACITY_CRITICAL_RATIO:
            events.append(_event(
                "CAPACITY_EXCEEDED",
                "CRITICAL",
                "link",
                link_id,
                {"capacityRatio": capacity_ratio, "flowCms": flow, "capacityCms": _number(state.get("capacityCms"))},
                "Link flow exceeds modeled capacity by a large margin.",
            ))
        elif capacity_ratio >= CAPACITY_WARNING_RATIO:
            events.append(_event(
                "CAPACITY_EXCEEDED",
                "WARNING",
                "link",
                link_id,
                {"capacityRatio": capacity_ratio, "flowCms": flow, "capacityCms": _number(state.get("capacityCms"))},
                "Link flow is at or above modeled capacity.",
            ))

        if blockage_ratio >= BLOCKAGE_CRITICAL_RATIO:
            events.append(_event(
                "BLOCKAGE_CLOSED",
                "CRITICAL",
                "link",
                link_id,
                {"blockageRatio": blockage_ratio, "targetSetting": _number(state.get("targetSetting"))},
                "Control input indicates this link is fully blocked.",
            ))
        elif blockage_ratio >= BLOCKAGE_WARNING_RATIO:
            events.append(_event(
                "BLOCKAGE_HIGH",
                "WARNING",
                "link",
                link_id,
                {"blockageRatio": blockage_ratio, "targetSetting": _number(state.get("targetSetting"))},
                "Control input indicates severe blockage.",
            ))
        elif blockage_ratio >= BLOCKAGE_WATCH_RATIO:
            events.append(_event(
                "BLOCKAGE_ACTIVE",
                "WATCH",
                "link",
                link_id,
                {"blockageRatio": blockage_ratio, "targetSetting": _number(state.get("targetSetting"))},
                "Control input indicates partial blockage.",
            ))

    for editor_id, state in editor_objects.items():
        flooding = _number(state.get("maxFloodingCms"))
        max_depth = _number(state.get("maxDepthRatio"))
        max_fullness = _number(state.get("maxFullness"))
        max_capacity = _number(state.get("maxCapacityRatio"))
        if flooding > FLOODING_EPSILON_CMS:
            events.append(_event(
                "OBJECT_FLOODING",
                "CRITICAL",
                "editorObject",
                editor_id,
                {"maxFloodingCms": flooding, "maxDepthRatio": max_depth, "maxFullness": max_fullness},
                "An editor object maps to a SWMM node with external flooding.",
            ))
        elif max(max_depth, max_fullness, max_capacity) >= SURCHARGE_RATIO:
            events.append(_event(
                "OBJECT_SURCHARGE",
                "WARNING",
                "editorObject",
                editor_id,
                {"maxDepthRatio": max_depth, "maxFullness": max_fullness, "maxCapacityRatio": max_capacity},
                "An editor object maps to at least one full or capacity-limited SWMM element.",
            ))

    events = sorted(events, key=lambda item: (-SEVERITY_RANK[item["severity"]], item["eventType"], item["sourceId"]))
    highest = _highest_severity(events)

    return {
        "ok": validation["ok"],
        "highestSeverity": highest,
        "events": events,
        "summary": _risk_summary(snapshot, events),
        "validation": validation,
        "counters": counters,
    }


def build_swmm_context_packet(
    snapshot: Mapping[str, Any],
    risk_result: Mapping[str, Any] | None = None,
    *,
    context_level: ContextLevel = "optimal",
    weather: Mapping[str, Any] | None = None,
    system_meta: Mapping[str, Any] | None = None,
    raw_snapshot_ref: str | None = None,
) -> dict[str, Any]:
    """Build a size-tiered packet for an LLM or external analysis service."""

    if context_level not in {"optimal", "medium", "full"}:
        raise ValueError("context_level must be one of: optimal, medium, full")

    validation = validate_swmm_snapshot(snapshot)
    risk = dict(risk_result) if risk_result is not None else evaluate_swmm_risk(snapshot)
    events = list(risk.get("events") or [])

    packet: dict[str, Any] = {
        "schemaVersion": 1,
        "contextLevel": context_level,
        "simulation": _simulation_meta(snapshot, raw_snapshot_ref),
        "weather": dict(weather or {}),
        "systemMeta": dict(system_meta or {}),
        "riskSummary": risk.get("summary", {}),
        "highestSeverity": risk.get("highestSeverity", "NORMAL"),
        "riskEvents": events,
        "affectedObjects": _affected_objects(snapshot, events),
        "globalStateSummary": _global_state_summary(snapshot),
        "validation": validation,
    }

    if context_level in {"medium", "full"}:
        packet["topAbnormalObjects"] = _top_abnormal_objects(snapshot)
        packet["relatedState"] = _related_state(snapshot, events)

    if context_level == "full":
        packet["rawSnapshot"] = {
            "nodes": deepcopy(dict(_mapping_or_empty(snapshot.get("nodes")))),
            "links": deepcopy(dict(_mapping_or_empty(snapshot.get("links")))),
            "editorObjects": deepcopy(dict(_mapping_or_empty(snapshot.get("editorObjects")))),
            "summary": deepcopy(dict(_mapping_or_empty(snapshot.get("summary")))),
            "control": deepcopy(snapshot.get("control")),
        }

    return packet


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default


def _validate_numeric_records(
    scope: str,
    records: Mapping[str, Any],
    field_names: tuple[str, ...],
    errors: list[str],
    warnings: list[str],
) -> None:
    for record_id, record in records.items():
        if not isinstance(record, Mapping):
            errors.append(f"{scope}.{record_id} must be a mapping")
            continue
        for field_name in field_names:
            if field_name not in record:
                continue
            value = record.get(field_name)
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                errors.append(f"{scope}.{record_id}.{field_name} must be numeric")
                continue
            if parsed != parsed:
                errors.append(f"{scope}.{record_id}.{field_name} must not be NaN")
            elif parsed < 0 and field_name not in {"flowCms"}:
                warnings.append(f"{scope}.{record_id}.{field_name} is negative")


def _event(
    event_type: str,
    severity: Severity,
    source: str,
    source_id: str,
    metrics: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "eventId": f"{event_type}:{source}:{source_id}",
        "eventType": event_type,
        "severity": severity,
        "source": source,
        "sourceId": source_id,
        "metrics": dict(metrics),
        "reason": reason,
    }


def _copy_previous_counters(previous_state: Mapping[str, Any] | None) -> dict[str, int]:
    raw_counters = _mapping_or_empty((previous_state or {}).get("counters") if previous_state else None)
    counters: dict[str, int] = {}
    for key, value in raw_counters.items():
        counters[str(key)] = int(_number(value))
    return counters


def _highest_severity(events: list[Mapping[str, Any]]) -> Severity:
    highest: Severity = "NORMAL"
    for event in events:
        severity = str(event.get("severity") or "NORMAL")
        if SEVERITY_RANK.get(severity, 0) > SEVERITY_RANK[highest]:
            highest = severity  # type: ignore[assignment]
    return highest


def _risk_summary(snapshot: Mapping[str, Any], events: list[Mapping[str, Any]]) -> dict[str, Any]:
    counts_by_type: dict[str, int] = {}
    counts_by_severity: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("eventType") or "UNKNOWN")
        severity = str(event.get("severity") or "NORMAL")
        counts_by_type[event_type] = counts_by_type.get(event_type, 0) + 1
        counts_by_severity[severity] = counts_by_severity.get(severity, 0) + 1
    return {
        "stepIndex": snapshot.get("stepIndex"),
        "modelTime": snapshot.get("modelTime"),
        "eventCount": len(events),
        "countsByType": counts_by_type,
        "countsBySeverity": counts_by_severity,
        "topEvents": list(events[:10]),
    }


def _simulation_meta(snapshot: Mapping[str, Any], raw_snapshot_ref: str | None) -> dict[str, Any]:
    control = _mapping_or_empty(snapshot.get("control"))
    return {
        "runId": snapshot.get("runId"),
        "stepIndex": snapshot.get("stepIndex"),
        "modelTime": snapshot.get("modelTime"),
        "stepSeconds": snapshot.get("stepSeconds"),
        "sourceOfTruth": snapshot.get("sourceOfTruth"),
        "source": snapshot.get("source"),
        "modelPath": snapshot.get("modelPath"),
        "runtimeModelPath": snapshot.get("runtimeModelPath"),
        "tickLogPath": snapshot.get("tickLogPath"),
        "rawSnapshotRef": raw_snapshot_ref or snapshot.get("tickLogPath"),
        "control": {
            "rainfallRatio": control.get("rainfallRatio"),
            "rainfallPercent": control.get("rainfallPercent"),
            "maxRainfallMmPerHour": control.get("maxRainfallMmPerHour"),
            "speedMultiplier": control.get("speedMultiplier"),
            "activeBlockageCount": len(_mapping_or_empty(control.get("blockagesById"))),
        },
    }


def _global_state_summary(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    nodes = _mapping_or_empty(snapshot.get("nodes"))
    links = _mapping_or_empty(snapshot.get("links"))
    editor_objects = _mapping_or_empty(snapshot.get("editorObjects"))
    return {
        "summary": dict(_mapping_or_empty(snapshot.get("summary"))),
        "maxNodeDepthRatio": max((_number(state.get("depthRatio")) for state in nodes.values() if isinstance(state, Mapping)), default=0.0),
        "maxNodeFloodingCms": max((_number(state.get("floodingCms")) for state in nodes.values() if isinstance(state, Mapping)), default=0.0),
        "maxLinkFullness": max((_number(state.get("fullness")) for state in links.values() if isinstance(state, Mapping)), default=0.0),
        "maxLinkCapacityRatio": max((_number(state.get("capacityRatio")) for state in links.values() if isinstance(state, Mapping)), default=0.0),
        "reverseFlowLinkCount": sum(1 for state in links.values() if isinstance(state, Mapping) and (state.get("direction") == "reverse" or _number(state.get("flowCms")) < REVERSE_FLOW_EPSILON_CMS)),
        "floodingNodeCount": sum(1 for state in nodes.values() if isinstance(state, Mapping) and _number(state.get("floodingCms")) > FLOODING_EPSILON_CMS),
        "blockedLinkCount": sum(1 for state in links.values() if isinstance(state, Mapping) and _number(state.get("blockageRatio")) > 0),
        "editorObjectCount": len(editor_objects),
    }


def _affected_objects(snapshot: Mapping[str, Any], events: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    editor_objects = _mapping_or_empty(snapshot.get("editorObjects"))
    affected: list[dict[str, Any]] = []
    for event in events:
        if event.get("source") != "editorObject":
            continue
        source_id = str(event.get("sourceId"))
        state = editor_objects.get(source_id) if isinstance(editor_objects.get(source_id), Mapping) else {}
        affected.append({
            "editorObjectId": source_id,
            "severity": event.get("severity"),
            "eventType": event.get("eventType"),
            "state": dict(state),
        })
    return affected


def _top_abnormal_objects(snapshot: Mapping[str, Any], limit: int = 12) -> dict[str, Any]:
    nodes = _mapping_or_empty(snapshot.get("nodes"))
    links = _mapping_or_empty(snapshot.get("links"))
    editor_objects = _mapping_or_empty(snapshot.get("editorObjects"))
    return {
        "nodes": _top_records(nodes, lambda state: max(_number(state.get("depthRatio")), _number(state.get("floodingCms")) * 100.0), limit),
        "links": _top_records(links, lambda state: max(_number(state.get("fullness")), _number(state.get("capacityRatio")), abs(_number(state.get("flowCms")))), limit),
        "editorObjects": _top_records(editor_objects, lambda state: max(
            _number(state.get("maxDepthRatio")),
            _number(state.get("maxFullness")),
            _number(state.get("maxCapacityRatio")),
            _number(state.get("maxFloodingCms")) * 100.0,
        ), limit),
    }


def _top_records(
    records: Mapping[str, Any],
    score_fn: Any,
    limit: int,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, Mapping[str, Any]]] = []
    for record_id, state in records.items():
        if not isinstance(state, Mapping):
            continue
        score = score_fn(state)
        if score <= 0:
            continue
        scored.append((score, record_id, state))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"id": record_id, "score": score, "state": dict(state)} for score, record_id, state in scored[:limit]]


def _related_state(snapshot: Mapping[str, Any], events: list[Mapping[str, Any]]) -> dict[str, Any]:
    nodes = _mapping_or_empty(snapshot.get("nodes"))
    links = _mapping_or_empty(snapshot.get("links"))
    source_node_ids = {str(event.get("sourceId")) for event in events if event.get("source") == "node"}
    source_link_ids = {str(event.get("sourceId")) for event in events if event.get("source") == "link"}
    blocked_link_ids = {
        link_id
        for link_id, state in links.items()
        if isinstance(state, Mapping) and _number(state.get("blockageRatio")) > 0
    }
    return {
        "eventNodes": {node_id: dict(nodes[node_id]) for node_id in source_node_ids if isinstance(nodes.get(node_id), Mapping)},
        "eventLinks": {link_id: dict(links[link_id]) for link_id in source_link_ids if isinstance(links.get(link_id), Mapping)},
        "nearbyBlockedLinks": {link_id: dict(links[link_id]) for link_id in blocked_link_ids if isinstance(links.get(link_id), Mapping)},
    }
