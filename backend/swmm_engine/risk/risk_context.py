"""SWMM runtime snapshot을 검증하고 위험 중심 context packet을 만든다.

이 모듈은 FastAPI/PySWMM에 의존하지 않도록 구성되어 FastAPI, Django,
Channels, background worker, test에서 재사용할 수 있다.

이 Django-package copy는 `swmm.interface.validate_snapshot()`,
`detect_risks()`, `build_llm_context()`의 내부 구현이다. 장고 view,
Channels consumer, Celery worker는 가능하면 이 파일을 직접 import하지 않고
공개 인터페이스인 `swmm.interface`를 통해 호출한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Literal

from .priority import enrich_risk_event_priority


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

DEFAULT_RISK_POLICY_LEVEL = "balanced"
RISK_POLICY_LEVELS: dict[str, dict[str, Any]] = {
    "sensitive": {
        "startupGraceTicks": 0,
        "reverseFlowMinAbsCms": 0.0005,
        "reverseWarningTicks": 3,
        "reverseCriticalTicks": 10,
        "fillWarningTicks": 1,
        "fillCriticalTicks": 3,
        "capacityWarningTicks": 1,
        "capacityCriticalTicks": 3,
        "blockageWarningTicks": 1,
        "blockageCriticalTicks": 1,
        "floodingCriticalTicks": 1,
        "llmAlertSeverities": ("WARNING", "CRITICAL"),
        "resolutionGraceTicks": 3,
    },
    "balanced": {
        "startupGraceTicks": 30,
        "reverseFlowMinAbsCms": 0.005,
        "reverseWarningTicks": 10,
        "reverseCriticalTicks": 30,
        "fillWarningTicks": 5,
        "fillCriticalTicks": 15,
        "capacityWarningTicks": 5,
        "capacityCriticalTicks": 15,
        "blockageWarningTicks": 3,
        "blockageCriticalTicks": 3,
        "floodingCriticalTicks": 1,
        "llmAlertSeverities": ("CRITICAL",),
        "resolutionGraceTicks": 5,
    },
    "strict": {
        "startupGraceTicks": 60,
        "reverseFlowMinAbsCms": 0.01,
        "reverseWarningTicks": 30,
        "reverseCriticalTicks": 60,
        "fillWarningTicks": 10,
        "fillCriticalTicks": 30,
        "capacityWarningTicks": 10,
        "capacityCriticalTicks": 30,
        "blockageWarningTicks": 5,
        "blockageCriticalTicks": 5,
        "floodingCriticalTicks": 1,
        "llmAlertSeverities": ("CRITICAL",),
        "resolutionGraceTicks": 10,
    },
}


def normalize_risk_policy_level(policy_level: str | None = None) -> str:
    """Return a known risk policy level, falling back to the operational default."""

    level = str(policy_level or DEFAULT_RISK_POLICY_LEVEL).strip().lower()
    if level not in RISK_POLICY_LEVELS:
        return DEFAULT_RISK_POLICY_LEVEL
    return level


def get_risk_policy(policy_level: str | None = None) -> dict[str, Any]:
    """Return a copy of the named risk policy with JSON-friendly metadata."""

    level = normalize_risk_policy_level(policy_level)
    policy = dict(RISK_POLICY_LEVELS[level])
    policy["level"] = level
    policy["llmAlertSeverities"] = list(policy.get("llmAlertSeverities") or ())
    return policy


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
    *,
    policy_level: str | None = None,
) -> dict[str, Any]:
    """SWMM runtime snapshot에서 deterministic 위험 event를 생성한다."""

    policy = get_risk_policy(policy_level)
    validation = validate_swmm_snapshot(snapshot)
    nodes = _mapping_or_empty(snapshot.get("nodes"))
    links = _mapping_or_empty(snapshot.get("links"))
    editor_objects = _mapping_or_empty(snapshot.get("editorObjects"))

    events: list[dict[str, Any]] = []
    counters = _copy_previous_counters(previous_state)
    step_index = int(_number(snapshot.get("stepIndex")))
    startup_grace_ticks = int(_number(policy.get("startupGraceTicks")))
    fill_warning_ticks = int(_number(policy.get("fillWarningTicks"), 1.0))
    fill_critical_ticks = int(_number(policy.get("fillCriticalTicks"), fill_warning_ticks))
    capacity_warning_ticks = int(_number(policy.get("capacityWarningTicks"), 1.0))
    capacity_critical_ticks = int(_number(policy.get("capacityCriticalTicks"), capacity_warning_ticks))
    blockage_warning_ticks = int(_number(policy.get("blockageWarningTicks"), 1.0))
    blockage_critical_ticks = int(_number(policy.get("blockageCriticalTicks"), blockage_warning_ticks))
    flooding_critical_ticks = int(_number(policy.get("floodingCriticalTicks"), 1.0))
    reverse_min_abs_flow = _number(policy.get("reverseFlowMinAbsCms"), abs(REVERSE_FLOW_EPSILON_CMS))
    reverse_warning_ticks = int(_number(policy.get("reverseWarningTicks"), 3.0))
    reverse_critical_ticks = int(_number(policy.get("reverseCriticalTicks"), reverse_warning_ticks))

    for node_id, state in nodes.items():
        flooding = _number(state.get("floodingCms"))
        depth_ratio = _number(state.get("depthRatio"))
        inflow = _number(state.get("totalInflowCms"))
        flooding_key = f"duration:FLOODING:node:{node_id}"
        fill_key = f"duration:FILL_LEVEL:node:{node_id}"

        if flooding > FLOODING_EPSILON_CMS:
            flooding_ticks = _condition_ticks(counters, flooding_key, True)
            events.append(_event(
                "FLOODING_DETECTED",
                _duration_severity(flooding_ticks, flooding_critical_ticks, flooding_critical_ticks),
                "node",
                node_id,
                _duration_metrics(
                    {"floodingCms": flooding, "depthRatio": depth_ratio, "totalInflowCms": inflow},
                    flooding_ticks,
                    flooding_critical_ticks,
                    flooding_critical_ticks,
                ),
                "SWMM이 이 node의 외부 침수를 보고했습니다.",
            ))
        elif depth_ratio >= SURCHARGE_RATIO:
            _condition_ticks(counters, flooding_key, False)
            fill_ticks = _condition_ticks(counters, fill_key, True)
            events.append(_event(
                "NODE_SURCHARGE",
                _duration_severity(fill_ticks, fill_warning_ticks, fill_critical_ticks),
                "node",
                node_id,
                _duration_metrics(
                    {"depthRatio": depth_ratio, "totalInflowCms": inflow},
                    fill_ticks,
                    fill_warning_ticks,
                    fill_critical_ticks,
                ),
                "Node depth is at or above its modeled max depth.",
            ))
        elif depth_ratio >= CRITICAL_FILL_RATIO:
            _condition_ticks(counters, flooding_key, False)
            fill_ticks = _condition_ticks(counters, fill_key, True)
            events.append(_event(
                "NODE_HIGH_WATER",
                _duration_severity(fill_ticks, fill_warning_ticks, fill_critical_ticks),
                "node",
                node_id,
                _duration_metrics(
                    {"depthRatio": depth_ratio, "totalInflowCms": inflow},
                    fill_ticks,
                    fill_warning_ticks,
                    fill_critical_ticks,
                ),
                "Node water level is near the critical range.",
            ))
        elif depth_ratio >= WARNING_FILL_RATIO:
            _condition_ticks(counters, flooding_key, False)
            _condition_ticks(counters, fill_key, False)
            events.append(_event(
                "NODE_RISING_WATER",
                "WATCH",
                "node",
                node_id,
                {"depthRatio": depth_ratio, "totalInflowCms": inflow},
                "Node water level is elevated.",
            ))
        else:
            _condition_ticks(counters, flooding_key, False)
            _condition_ticks(counters, fill_key, False)

    for link_id, state in links.items():
        flow = _number(state.get("flowCms"))
        fullness = _number(state.get("fullness"))
        capacity_ratio = _number(state.get("capacityRatio"))
        blockage_ratio = _number(state.get("blockageRatio"))
        direction = state.get("direction")
        is_reverse = direction == "reverse" or flow < REVERSE_FLOW_EPSILON_CMS
        is_meaningful_reverse = is_reverse and abs(flow) >= reverse_min_abs_flow
        reverse_key = f"reverse:{link_id}"
        reverse_ticks = _condition_ticks(counters, reverse_key, is_meaningful_reverse)
        if is_meaningful_reverse and step_index > startup_grace_ticks:
            events.append(_event(
                "REVERSE_FLOW",
                _duration_severity(reverse_ticks, reverse_warning_ticks, reverse_critical_ticks),
                "link",
                link_id,
                {
                    "flowCms": flow,
                    "fullness": fullness,
                    "capacityRatio": capacity_ratio,
                    "reverseTicks": reverse_ticks,
                    "minAbsFlowCms": reverse_min_abs_flow,
                    "startupGraceTicks": startup_grace_ticks,
                },
                "Link flow is negative relative to the modeled link direction.",
            ))

        fill_key = f"duration:FILL_LEVEL:link:{link_id}"
        if fullness >= SURCHARGE_RATIO:
            fill_ticks = _condition_ticks(counters, fill_key, True)
            events.append(_event(
                "LINK_SURCHARGE",
                _duration_severity(fill_ticks, fill_warning_ticks, fill_critical_ticks),
                "link",
                link_id,
                _duration_metrics(
                    {"fullness": fullness, "flowCms": flow, "capacityRatio": capacity_ratio},
                    fill_ticks,
                    fill_warning_ticks,
                    fill_critical_ticks,
                ),
                "Link depth is at or above full pipe depth.",
            ))
        elif fullness >= CRITICAL_FILL_RATIO:
            fill_ticks = _condition_ticks(counters, fill_key, True)
            events.append(_event(
                "LINK_HIGH_FILL",
                _duration_severity(fill_ticks, fill_warning_ticks, fill_critical_ticks),
                "link",
                link_id,
                _duration_metrics(
                    {"fullness": fullness, "flowCms": flow, "capacityRatio": capacity_ratio},
                    fill_ticks,
                    fill_warning_ticks,
                    fill_critical_ticks,
                ),
                "Link fill ratio is near the critical range.",
            ))
        elif fullness >= WARNING_FILL_RATIO:
            _condition_ticks(counters, fill_key, False)
            events.append(_event(
                "LINK_ELEVATED_FILL",
                "WATCH",
                "link",
                link_id,
                {"fullness": fullness, "flowCms": flow, "capacityRatio": capacity_ratio},
                "Link fill ratio is elevated.",
            ))
        else:
            _condition_ticks(counters, fill_key, False)

        capacity_key = f"duration:CAPACITY:link:{link_id}"
        if capacity_ratio >= CAPACITY_CRITICAL_RATIO:
            capacity_ticks = _condition_ticks(counters, capacity_key, True)
            events.append(_event(
                "CAPACITY_EXCEEDED",
                _duration_severity(capacity_ticks, capacity_warning_ticks, capacity_critical_ticks),
                "link",
                link_id,
                _duration_metrics(
                    {"capacityRatio": capacity_ratio, "flowCms": flow, "capacityCms": _number(state.get("capacityCms"))},
                    capacity_ticks,
                    capacity_warning_ticks,
                    capacity_critical_ticks,
                ),
                "Link flow exceeds modeled capacity by a large margin.",
            ))
        elif capacity_ratio >= CAPACITY_WARNING_RATIO:
            capacity_ticks = _condition_ticks(counters, capacity_key, True)
            events.append(_event(
                "CAPACITY_EXCEEDED",
                "WARNING" if capacity_ticks >= capacity_warning_ticks else "WATCH",
                "link",
                link_id,
                _duration_metrics(
                    {"capacityRatio": capacity_ratio, "flowCms": flow, "capacityCms": _number(state.get("capacityCms"))},
                    capacity_ticks,
                    capacity_warning_ticks,
                    capacity_critical_ticks,
                ),
                "Link flow is at or above modeled capacity.",
            ))
        else:
            _condition_ticks(counters, capacity_key, False)

        blockage_key = f"duration:BLOCKAGE:link:{link_id}"
        if blockage_ratio >= BLOCKAGE_CRITICAL_RATIO:
            blockage_ticks = _condition_ticks(counters, blockage_key, True)
            events.append(_event(
                "BLOCKAGE_CLOSED",
                _duration_severity(blockage_ticks, blockage_warning_ticks, blockage_critical_ticks),
                "link",
                link_id,
                _duration_metrics(
                    {"blockageRatio": blockage_ratio, "targetSetting": _number(state.get("targetSetting"))},
                    blockage_ticks,
                    blockage_warning_ticks,
                    blockage_critical_ticks,
                ),
                "Control input indicates this link is fully blocked.",
            ))
        elif blockage_ratio >= BLOCKAGE_WARNING_RATIO:
            blockage_ticks = _condition_ticks(counters, blockage_key, True)
            events.append(_event(
                "BLOCKAGE_HIGH",
                "WARNING" if blockage_ticks >= blockage_warning_ticks else "WATCH",
                "link",
                link_id,
                _duration_metrics(
                    {"blockageRatio": blockage_ratio, "targetSetting": _number(state.get("targetSetting"))},
                    blockage_ticks,
                    blockage_warning_ticks,
                    blockage_critical_ticks,
                ),
                "Control input indicates severe blockage.",
            ))
        elif blockage_ratio >= BLOCKAGE_WATCH_RATIO:
            _condition_ticks(counters, blockage_key, False)
            events.append(_event(
                "BLOCKAGE_ACTIVE",
                "WATCH",
                "link",
                link_id,
                {"blockageRatio": blockage_ratio, "targetSetting": _number(state.get("targetSetting"))},
                "Control input indicates partial blockage.",
            ))
        else:
            _condition_ticks(counters, blockage_key, False)

    for editor_id, state in editor_objects.items():
        flooding = _number(state.get("maxFloodingCms"))
        max_depth = _number(state.get("maxDepthRatio"))
        max_fullness = _number(state.get("maxFullness"))
        max_capacity = _number(state.get("maxCapacityRatio"))
        flooding_key = f"duration:FLOODING:editorObject:{editor_id}"
        fill_key = f"duration:FILL_LEVEL:editorObject:{editor_id}"
        if flooding > FLOODING_EPSILON_CMS:
            flooding_ticks = _condition_ticks(counters, flooding_key, True)
            events.append(_event(
                "OBJECT_FLOODING",
                _duration_severity(flooding_ticks, flooding_critical_ticks, flooding_critical_ticks),
                "editorObject",
                editor_id,
                _duration_metrics(
                    {"maxFloodingCms": flooding, "maxDepthRatio": max_depth, "maxFullness": max_fullness},
                    flooding_ticks,
                    flooding_critical_ticks,
                    flooding_critical_ticks,
                ),
                "editor object가 외부 침수가 발생한 SWMM node에 매핑되어 있습니다.",
            ))
        elif max(max_depth, max_fullness, max_capacity) >= SURCHARGE_RATIO:
            _condition_ticks(counters, flooding_key, False)
            fill_ticks = _condition_ticks(counters, fill_key, True)
            events.append(_event(
                "OBJECT_SURCHARGE",
                _duration_severity(fill_ticks, fill_warning_ticks, fill_critical_ticks),
                "editorObject",
                editor_id,
                _duration_metrics(
                    {"maxDepthRatio": max_depth, "maxFullness": max_fullness, "maxCapacityRatio": max_capacity},
                    fill_ticks,
                    fill_warning_ticks,
                    fill_critical_ticks,
                ),
                "editor object가 만관 또는 용량 제한 상태의 SWMM 요소에 매핑되어 있습니다.",
            ))
        else:
            _condition_ticks(counters, flooding_key, False)
            _condition_ticks(counters, fill_key, False)

    events = [enrich_risk_event_priority(event) for event in events]
    events = sorted(
        events,
        key=lambda item: (
            -float(item.get("priorityScore") or 0.0),
            -SEVERITY_RANK[item["severity"]],
            item["eventType"],
            item["sourceId"],
        ),
    )
    highest = _highest_severity(events)

    return {
        "ok": validation["ok"],
        "highestSeverity": highest,
        "events": events,
        "summary": _risk_summary(snapshot, events),
        "validation": validation,
        "counters": counters,
        "policy": _public_risk_policy(policy),
    }


def build_swmm_context_packet(
    snapshot: Mapping[str, Any],
    risk_result: Mapping[str, Any] | None = None,
    *,
    context_level: ContextLevel = "optimal",
    policy_level: str | None = None,
    system_meta: Mapping[str, Any] | None = None,
    raw_snapshot_ref: str | None = None,
    include_debug_refs: bool = False,
) -> dict[str, Any]:
    """Build a size-tiered packet for an LLM or external analysis service."""

    if context_level not in {"optimal", "medium", "full"}:
        raise ValueError("context_level must be one of: optimal, medium, full")

    validation = validate_swmm_snapshot(snapshot)
    risk = dict(risk_result) if risk_result is not None else evaluate_swmm_risk(snapshot, policy_level=policy_level)
    events = list(risk.get("events") or [])
    policy = dict(risk.get("policy") or get_risk_policy(policy_level))

    packet: dict[str, Any] = {
        "schemaVersion": 1,
        "contextLevel": context_level,
        "simulation": _simulation_meta(snapshot, raw_snapshot_ref, include_debug_refs=include_debug_refs),
        "systemMeta": dict(system_meta or {}),
        "riskPolicy": _public_risk_policy(policy),
        "riskSummary": risk.get("summary", {}),
        "highestSeverity": risk.get("highestSeverity", "NORMAL"),
        "riskEvents": events,
        "affectedObjects": _affected_objects(snapshot, events),
        "globalStateSummary": _global_state_summary(snapshot, policy),
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

    return _drop_empty_values(packet)


def _mapping_or_empty(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _drop_empty_values(value: Any) -> Any:
    if isinstance(value, Mapping):
        compact: dict[str, Any] = {}
        for key, entry_value in value.items():
            cleaned = _drop_empty_values(entry_value)
            if cleaned in ({}, [], None):
                continue
            compact[str(key)] = cleaned
        return compact
    if isinstance(value, list):
        return [
            cleaned
            for entry in value
            if (cleaned := _drop_empty_values(entry)) not in ({}, [], None)
        ]
    return value


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default


def _condition_ticks(counters: dict[str, int], key: str, active: bool) -> int:
    if active:
        counters[key] = int(counters.get(key, 0)) + 1
        return counters[key]
    counters.pop(key, None)
    return 0


def _duration_severity(duration_ticks: int, warning_ticks: int, critical_ticks: int) -> Severity:
    warning_ticks = max(1, int(warning_ticks))
    critical_ticks = max(warning_ticks, int(critical_ticks))
    if duration_ticks >= critical_ticks:
        return "CRITICAL"
    if duration_ticks >= warning_ticks:
        return "WARNING"
    return "WATCH"


def _duration_metrics(
    metrics: Mapping[str, Any],
    duration_ticks: int,
    warning_ticks: int,
    critical_ticks: int,
) -> dict[str, Any]:
    enriched = dict(metrics)
    enriched["durationTicks"] = duration_ticks
    enriched["warningAfterTicks"] = warning_ticks
    enriched["criticalAfterTicks"] = critical_ticks
    return enriched


def _public_risk_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "level": policy.get("level", DEFAULT_RISK_POLICY_LEVEL),
        "startupGraceTicks": int(_number(policy.get("startupGraceTicks"))),
        "reverseFlowMinAbsCms": _number(policy.get("reverseFlowMinAbsCms"), abs(REVERSE_FLOW_EPSILON_CMS)),
        "reverseWarningTicks": int(_number(policy.get("reverseWarningTicks"))),
        "reverseCriticalTicks": int(_number(policy.get("reverseCriticalTicks"))),
        "fillWarningTicks": int(_number(policy.get("fillWarningTicks"))),
        "fillCriticalTicks": int(_number(policy.get("fillCriticalTicks"))),
        "capacityWarningTicks": int(_number(policy.get("capacityWarningTicks"))),
        "capacityCriticalTicks": int(_number(policy.get("capacityCriticalTicks"))),
        "blockageWarningTicks": int(_number(policy.get("blockageWarningTicks"))),
        "blockageCriticalTicks": int(_number(policy.get("blockageCriticalTicks"))),
        "floodingCriticalTicks": int(_number(policy.get("floodingCriticalTicks"), 1.0)),
        "llmAlertSeverities": list(policy.get("llmAlertSeverities") or ()),
        "resolutionGraceTicks": int(_number(policy.get("resolutionGraceTicks"), 3.0)),
    }


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


def _simulation_meta(
    snapshot: Mapping[str, Any],
    raw_snapshot_ref: str | None,
    *,
    include_debug_refs: bool,
) -> dict[str, Any]:
    control = _mapping_or_empty(snapshot.get("control"))
    meta = {
        "runId": snapshot.get("runId"),
        "stepIndex": snapshot.get("stepIndex"),
        "modelTime": snapshot.get("modelTime"),
        "stepSeconds": snapshot.get("stepSeconds"),
        "sourceOfTruth": snapshot.get("sourceOfTruth"),
        "source": snapshot.get("source"),
        "control": {
            "rainfallRatio": control.get("rainfallRatio"),
            "rainfallPercent": control.get("rainfallPercent"),
            "maxRainfallMmPerHour": control.get("maxRainfallMmPerHour"),
            "speedMultiplier": control.get("speedMultiplier"),
            "activeBlockageCount": len(_mapping_or_empty(control.get("blockagesById"))),
        },
    }
    if include_debug_refs:
        meta.update({
            "modelPath": snapshot.get("modelPath"),
            "runtimeModelPath": snapshot.get("runtimeModelPath"),
            "tickLogPath": snapshot.get("tickLogPath"),
            "rawSnapshotRef": raw_snapshot_ref or snapshot.get("tickLogPath"),
        })
    return meta


def _global_state_summary(snapshot: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    nodes = _mapping_or_empty(snapshot.get("nodes"))
    links = _mapping_or_empty(snapshot.get("links"))
    editor_objects = _mapping_or_empty(snapshot.get("editorObjects"))
    reverse_min_abs_flow = _number(policy.get("reverseFlowMinAbsCms"), abs(REVERSE_FLOW_EPSILON_CMS))
    return {
        "summary": dict(_mapping_or_empty(snapshot.get("summary"))),
        "maxNodeDepthRatio": max((_number(state.get("depthRatio")) for state in nodes.values() if isinstance(state, Mapping)), default=0.0),
        "maxNodeFloodingCms": max((_number(state.get("floodingCms")) for state in nodes.values() if isinstance(state, Mapping)), default=0.0),
        "maxLinkFullness": max((_number(state.get("fullness")) for state in links.values() if isinstance(state, Mapping)), default=0.0),
        "maxLinkCapacityRatio": max((_number(state.get("capacityRatio")) for state in links.values() if isinstance(state, Mapping)), default=0.0),
        "reverseFlowLinkCount": sum(
            1
            for state in links.values()
            if (
                isinstance(state, Mapping)
                and (state.get("direction") == "reverse" or _number(state.get("flowCms")) < REVERSE_FLOW_EPSILON_CMS)
                and abs(_number(state.get("flowCms"))) >= reverse_min_abs_flow
            )
        ),
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
