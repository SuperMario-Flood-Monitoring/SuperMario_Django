"""SWMM 위험 이벤트의 현장 조치 우선순위 점수 계산."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SEVERITY_SCORE = {
    "CRITICAL": 100.0,
    "WARNING": 50.0,
    "WATCH": 10.0,
}

EVENT_TYPE_SCORE = {
    "FLOODING_DETECTED": 60.0,
    "NODE_FLOODING": 60.0,
    "PREDICTED_FLOODING": 55.0,
    "OBJECT_FLOODING": 55.0,
    "REVERSE_FLOW": 50.0,
    "BLOCKAGE_CLOSED": 50.0,
    "PREDICTED_BLOCKAGE_CLOSED": 45.0,
    "NODE_SURCHARGE": 45.0,
    "OBJECT_SURCHARGE": 42.0,
    "LINK_SURCHARGE": 40.0,
    "PREDICTED_NODE_DEPTH": 40.0,
    "CAPACITY_EXCEEDED": 35.0,
    "PREDICTED_CAPACITY_EXCEEDED": 35.0,
    "PREDICTED_FULL_PIPE": 35.0,
    "LINK_HIGH_FILL": 30.0,
    "BLOCKAGE_HIGH": 30.0,
    "PREDICTED_BLOCKAGE_HIGH": 25.0,
    "NODE_HIGH_WATER": 30.0,
}


def enrich_risk_event_priority(event: Mapping[str, Any], metrics: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """위험 이벤트 dict에 priorityScore, priorityBand, priorityReasons를 추가한다."""

    enriched = dict(event)
    priority = calculate_priority(event, metrics)
    enriched.update(priority)
    return enriched


def calculate_priority(event: Mapping[str, Any], metrics: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """현장 대처 우선순위 점수를 계산한다.

    점수는 deterministic rule 기반이다. 현장 중요도, 인구, 도로 등 외부 데이터가
    없는 현재 프로젝트에서는 SWMM 위험도, 위험 유형, 대상 유형, 주요 수치 초과량,
    10분 예측 변화량만 사용한다.
    """

    event_type = str(event.get("eventType") or event.get("hazard_type") or "").upper()
    severity = str(event.get("severity") or event.get("hazard_level") or "").upper()
    source = str(event.get("source") or "").lower()
    source_id = str(event.get("sourceId") or event.get("target_id") or "")
    merged_metrics = _metrics_for(event, metrics)

    score = 0.0
    reasons: list[str] = []

    severity_score = SEVERITY_SCORE.get(severity, 0.0)
    if severity_score:
        score += severity_score
        reasons.append(f"{severity} 위험")

    event_score = EVENT_TYPE_SCORE.get(event_type, 20.0 if event_type else 0.0)
    if event_score:
        score += event_score
        reasons.append(_event_reason(event_type))

    if source == "node":
        score += 8.0
        reasons.append("node 위험은 현장 침수 접점에 가까움")
    elif source == "editorobject":
        score += 6.0
        reasons.append("editor object 매핑 위험")

    if "MAIN" in source_id.upper():
        score += 8.0
        reasons.append("주요 관로로 추정되는 대상")

    score += _metric_score(merged_metrics, reasons)
    score = round(score, 2)

    return {
        "priorityScore": score,
        "priorityBand": _priority_band(score),
        "priorityReasons": reasons,
    }


def _metrics_for(event: Mapping[str, Any], metrics: Mapping[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    raw_event_metrics = event.get("metrics")
    if isinstance(raw_event_metrics, Mapping):
        merged.update(raw_event_metrics)
    if isinstance(metrics, Mapping):
        merged.update(metrics)
    return merged


def _metric_score(metrics: Mapping[str, Any], reasons: list[str]) -> float:
    score = 0.0

    flooding = _number(metrics.get("floodingCms"))
    if flooding > 0:
        score += min(50.0, 35.0 + flooding * 1000.0)
        reasons.append(f"월류 발생 floodingCms={flooding:g}")

    flow = _number(metrics.get("flowCms"))
    direction = str(metrics.get("direction") or "").lower()
    if direction == "reverse" or flow < 0:
        score += min(35.0, 20.0 + abs(flow) * 30.0)
        reasons.append(f"역류 흐름 flowCms={flow:g}")

    blockage = _metric_value(metrics, "blockageRatio")
    if blockage is not None:
        if blockage >= 1.0:
            score += 40.0
            reasons.append("막힘 비율 100%")
        elif blockage >= 0.8:
            score += 28.0
            reasons.append(f"막힘 비율 {blockage * 100:.1f}%")
        elif blockage > 0:
            score += min(20.0, blockage * 20.0)
            reasons.append(f"막힘 비율 {blockage * 100:.1f}%")

    fullness = _metric_value(metrics, "fullness")
    if fullness is not None:
        score += _ratio_score(fullness, 0.9, 1.0, 28.0, "만관율", reasons)

    capacity = _metric_value(metrics, "capacityRatio")
    if capacity is not None:
        score += _ratio_score(capacity, 1.0, 1.25, 35.0, "용량 비율", reasons)

    depth = _metric_value(metrics, "depthRatio")
    if depth is not None:
        score += _ratio_score(depth, 0.9, 1.0, 35.0, "node 수위 비율", reasons)

    slope = _number(metrics.get("slopePerSecond"))
    current = _optional_number(metrics.get("currentValue"))
    predicted = _optional_number(metrics.get("predictedValue"))
    if predicted is not None and current is not None and predicted > current:
        score += min(20.0, (predicted - current) * 30.0)
        reasons.append(f"예측 증가량 {predicted - current:.3f}")
    if slope > 0:
        score += min(20.0, slope * 600.0 * 20.0)
        reasons.append(f"예측 상승 기울기 {slope:g}/s")

    return score


def _metric_value(metrics: Mapping[str, Any], metric_name: str) -> float | None:
    if metric_name in metrics:
        return _optional_number(metrics.get(metric_name))
    if metrics.get("metric") == metric_name:
        return _optional_number(metrics.get("predictedValue", metrics.get("currentValue")))
    return None


def _ratio_score(
    value: float,
    warning_threshold: float,
    critical_threshold: float,
    max_score: float,
    label: str,
    reasons: list[str],
) -> float:
    if value < warning_threshold:
        return 0.0
    over = max(0.0, value - warning_threshold)
    span = max(critical_threshold - warning_threshold, 0.000001)
    score = min(max_score, 12.0 + (over / span) * (max_score - 12.0))
    reasons.append(f"{label} {value * 100:.1f}%")
    return score


def _priority_band(score: float) -> str:
    if score >= 170:
        return "P1"
    if score >= 130:
        return "P2"
    if score >= 90:
        return "P3"
    return "P4"


def _event_reason(event_type: str) -> str:
    if "FLOODING" in event_type:
        return "침수/월류 위험"
    if event_type == "REVERSE_FLOW":
        return "역류 위험"
    if "BLOCKAGE" in event_type:
        return "막힘 위험"
    if "NODE" in event_type:
        return "node 수위 위험"
    if "CAPACITY" in event_type:
        return "용량 초과 위험"
    if "FULL_PIPE" in event_type or "SURCHARGE" in event_type or "FILL" in event_type:
        return "만관 위험"
    return f"{event_type} 위험"


def _number(value: Any, default: float = 0.0) -> float:
    parsed = _optional_number(value)
    return default if parsed is None else parsed


def _optional_number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None
