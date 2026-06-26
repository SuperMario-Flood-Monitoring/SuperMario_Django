from __future__ import annotations

from collections import deque
from copy import deepcopy
from statistics import median
from typing import Any, Mapping

from django.conf import settings


FORECAST_MINUTES = settings.SUPERMARIO_FORECAST_MINUTES
FORECAST_WINDOW_SECONDS = settings.SUPERMARIO_FORECAST_WINDOW_SECONDS
FORECAST_BUFFER_SECONDS = settings.SUPERMARIO_FORECAST_BUFFER_SECONDS
FORECAST_MIN_OBSERVATION_SECONDS = settings.SUPERMARIO_FORECAST_MIN_OBSERVATION_SECONDS

WARNING_FILL_RATIO = 0.70
CRITICAL_FILL_RATIO = 0.90
SURCHARGE_RATIO = 1.00
CAPACITY_WARNING_RATIO = 1.00
CAPACITY_CRITICAL_RATIO = 1.25
DEPTH_WARNING_RATIO = 0.70
DEPTH_CRITICAL_RATIO = 0.90
FLOODING_CRITICAL_CMS = 0.000001
BLOCKAGE_WARNING_RATIO = 0.80
BLOCKAGE_CRITICAL_RATIO = 1.00
MIN_CURRENT_BY_RAIN = {
    "clear": {
        "fullness": settings.SUPERMARIO_FORECAST_CLEAR_MIN_CURRENT_FULLNESS,
        "depthRatio": settings.SUPERMARIO_FORECAST_CLEAR_MIN_CURRENT_DEPTH_RATIO,
        "capacityRatio": settings.SUPERMARIO_FORECAST_CLEAR_MIN_CURRENT_CAPACITY_RATIO,
    },
    "weak": {
        "fullness": settings.SUPERMARIO_FORECAST_MIN_CURRENT_FULLNESS,
        "depthRatio": settings.SUPERMARIO_FORECAST_MIN_CURRENT_DEPTH_RATIO,
        "capacityRatio": settings.SUPERMARIO_FORECAST_MIN_CURRENT_CAPACITY_RATIO,
    },
    "heavy": {
        "fullness": settings.SUPERMARIO_FORECAST_HEAVY_RAIN_MIN_CURRENT_FULLNESS,
        "depthRatio": settings.SUPERMARIO_FORECAST_HEAVY_RAIN_MIN_CURRENT_DEPTH_RATIO,
        "capacityRatio": settings.SUPERMARIO_FORECAST_HEAVY_RAIN_MIN_CURRENT_CAPACITY_RATIO,
    },
}
PREDICTED_MAX_BY_METRIC = {
    "fullness": settings.SUPERMARIO_FORECAST_MAX_FULLNESS,
    "depthRatio": settings.SUPERMARIO_FORECAST_MAX_DEPTH_RATIO,
    "capacityRatio": settings.SUPERMARIO_FORECAST_MAX_CAPACITY_RATIO,
}

_samples: deque[dict[str, Any]] = deque()
_current_run_id: str | None = None


def reset() -> None:
    _samples.clear()
    global _current_run_id
    _current_run_id = None


def record_snapshot(snapshot: Mapping[str, Any]) -> None:
    if not isinstance(snapshot.get("nodes"), Mapping) or not isinstance(snapshot.get("links"), Mapping):
        return

    run_id = str(snapshot.get("runId") or "")
    if not run_id:
        return

    global _current_run_id
    if _current_run_id and _current_run_id != run_id:
        _samples.clear()
    _current_run_id = run_id

    step_index = int(_number(snapshot.get("stepIndex")))
    step_seconds = max(_number(snapshot.get("stepSeconds"), 1.0), 1.0)
    sample = {
        "runId": run_id,
        "stepIndex": step_index,
        "stepSeconds": step_seconds,
        "modelSecond": step_index * step_seconds,
        "modelTime": snapshot.get("modelTime"),
        "control": deepcopy(snapshot.get("control") or {}),
        "nodes": {
            str(node_id): {
                "depthRatio": _number(state.get("depthRatio")),
                "floodingCms": _number(state.get("floodingCms")),
            }
            for node_id, state in snapshot.get("nodes", {}).items()
            if isinstance(state, Mapping)
        },
        "links": {
            str(link_id): {
                "fullness": _number(state.get("fullness")),
                "capacityRatio": _number(state.get("capacityRatio")),
                "flowCms": _number(state.get("flowCms")),
                "blockageRatio": _number(state.get("blockageRatio")),
            }
            for link_id, state in snapshot.get("links", {}).items()
            if isinstance(state, Mapping)
        },
    }
    _samples.append(sample)
    cutoff = sample["modelSecond"] - FORECAST_BUFFER_SECONDS
    while _samples and _samples[0]["modelSecond"] < cutoff:
        _samples.popleft()


def forecast(minutes: int | None = None) -> dict[str, Any]:
    forecast_minutes = int(minutes or FORECAST_MINUTES)
    if forecast_minutes < 1:
        forecast_minutes = FORECAST_MINUTES

    if len(_samples) < 2:
        if _samples:
            latest = _samples[-1]
            predictions = _blockage_predictions(latest, forecast_minutes)
            events = [_event_from_prediction(prediction) for prediction in predictions]
            if events:
                return _forecast_payload(
                    forecast_minutes=forecast_minutes,
                    window_seconds=0,
                    latest=latest,
                    events=events,
                    predictions=predictions,
                    message="Not enough runtime samples.",
                )
        return _empty_forecast(forecast_minutes, "Not enough runtime samples.")

    latest = _samples[-1]
    window_start = latest["modelSecond"] - FORECAST_WINDOW_SECONDS
    window_samples = [sample for sample in _samples if sample["modelSecond"] >= window_start]
    baseline = window_samples[0] if window_samples else _samples[0]
    delta_seconds = max(latest["modelSecond"] - baseline["modelSecond"], 1.0)
    rainfall_level = _rainfall_level(latest.get("control"))
    events: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    for prediction in _blockage_predictions(latest, forecast_minutes):
        predictions.append(prediction)
        if prediction["severity"] in {"WARNING", "CRITICAL"}:
            events.append(_event_from_prediction(prediction))

    if delta_seconds < FORECAST_MIN_OBSERVATION_SECONDS:
        return _forecast_payload(
            forecast_minutes=forecast_minutes,
            window_seconds=int(delta_seconds),
            latest=latest,
            events=events,
            predictions=predictions,
            message=f"Need at least {FORECAST_MIN_OBSERVATION_SECONDS} seconds of runtime samples.",
        )

    horizon_seconds = forecast_minutes * 60.0
    for link_id, current in latest["links"].items():
        previous = baseline["links"].get(link_id)
        if not isinstance(previous, Mapping):
            continue
        for metric, hazard_type in (
            ("fullness", "PREDICTED_FULL_PIPE"),
            ("capacityRatio", "PREDICTED_CAPACITY_EXCEEDED"),
        ):
            prediction = _predict_metric(
                source="link",
                target_id=link_id,
                metric=metric,
                hazard_type=hazard_type,
                current_value=current.get(metric),
                previous_value=previous.get(metric),
                delta_seconds=delta_seconds,
                horizon_seconds=horizon_seconds,
                forecast_minutes=forecast_minutes,
                rainfall_level=rainfall_level,
                slope_per_second=_median_slope(window_samples, "links", link_id, metric),
            )
            if prediction:
                predictions.append(prediction)
                if prediction["severity"] in {"WARNING", "CRITICAL"}:
                    events.append(_event_from_prediction(prediction))

    for node_id, current in latest["nodes"].items():
        previous = baseline["nodes"].get(node_id)
        if not isinstance(previous, Mapping):
            continue
        prediction = _predict_metric(
            source="node",
            target_id=node_id,
            metric="depthRatio",
            hazard_type="PREDICTED_NODE_DEPTH",
            current_value=current.get("depthRatio"),
            previous_value=previous.get("depthRatio"),
            delta_seconds=delta_seconds,
            horizon_seconds=horizon_seconds,
            forecast_minutes=forecast_minutes,
            rainfall_level=rainfall_level,
            slope_per_second=_median_slope(window_samples, "nodes", node_id, "depthRatio"),
        )
        if prediction:
            predictions.append(prediction)
            if prediction["severity"] in {"WARNING", "CRITICAL"}:
                events.append(_event_from_prediction(prediction))

        flooding_prediction = {
            "source": "node",
            "targetId": node_id,
            "metric": "floodingCms",
            "hazardType": "PREDICTED_FLOODING",
            "currentValue": current.get("floodingCms", 0.0),
            "predictedValue": current.get("floodingCms", 0.0),
            "slopePerSecond": 0.0,
            "severity": "CRITICAL" if current.get("floodingCms", 0.0) > FLOODING_CRITICAL_CMS else "NORMAL",
            "forecastMinutes": forecast_minutes,
        }
        predictions.append(flooding_prediction)
        if flooding_prediction["severity"] == "CRITICAL":
            events.append(_event_from_prediction(flooding_prediction))

    return _forecast_payload(
        forecast_minutes=forecast_minutes,
        window_seconds=int(delta_seconds),
        latest=latest,
        events=events,
        predictions=predictions,
    )


def build_forecast_llm_payload(snapshot: Mapping[str, Any], forecast_result: Mapping[str, Any]) -> dict[str, Any] | None:
    events = [event for event in forecast_result.get("events") or [] if event.get("severity") == "CRITICAL"]
    if not events:
        return None
    context = {
        "schemaVersion": 1,
        "contextLevel": "forecast",
        "simulation": {
            "runId": forecast_result.get("runId"),
            "stepIndex": forecast_result.get("stepIndex"),
            "modelTime": forecast_result.get("modelTime"),
            "forecastMinutes": forecast_result.get("forecastMinutes"),
            "windowSeconds": forecast_result.get("windowSeconds"),
            "control": forecast_result.get("control", {}),
        },
        "highestSeverity": forecast_result.get("highestSeverity"),
        "riskEvents": events,
        "forecastPredictions": forecast_result.get("predictions", []),
        "systemMeta": {
            "sourceService": "SuperMario_Django",
            "targetService": "SuperMario_LLM",
            "dispatchStatus": "not_called",
            "triggerBasis": "forecast",
        },
    }
    copied = dict(snapshot)
    copied["llmTrigger"] = {
        "shouldTrigger": True,
        "reason": "predicted_10_min_risk",
        "contextLevel": "forecast",
        "context": context,
        "triggeredIssues": [
            {
                "issueId": event["eventId"],
                "eventType": event["eventType"],
                "severity": event["severity"],
                "sourceId": event["sourceId"],
                "lastTriggeredStepIndex": forecast_result.get("stepIndex"),
            }
            for event in events
        ],
    }
    return copied


def _predict_metric(
    *,
    source: str,
    target_id: str,
    metric: str,
    hazard_type: str,
    current_value: Any,
    previous_value: Any,
    delta_seconds: float,
    horizon_seconds: float,
    forecast_minutes: int,
    rainfall_level: str,
    slope_per_second: float | None = None,
) -> dict[str, Any] | None:
    current = _number(current_value)
    previous = _number(previous_value)
    slope = (current - previous) / delta_seconds if slope_per_second is None else slope_per_second
    predicted = min(PREDICTED_MAX_BY_METRIC.get(metric, float("inf")), max(0.0, current + slope * horizon_seconds))
    severity = _severity_for_metric(metric, current, predicted, rainfall_level)
    return {
        "source": source,
        "targetId": target_id,
        "metric": metric,
        "hazardType": hazard_type,
        "currentValue": current,
        "predictedValue": predicted,
        "slopePerSecond": slope,
        "minCurrentValue": _min_current_for_metric(metric, rainfall_level),
        "rainfallLevel": rainfall_level,
        "severity": severity,
        "forecastMinutes": forecast_minutes,
    }


def _severity_for_metric(metric: str, current: float, predicted: float, rainfall_level: str) -> str:
    if current < _min_current_for_metric(metric, rainfall_level):
        return "NORMAL"
    if metric == "fullness":
        if predicted >= CRITICAL_FILL_RATIO:
            return "CRITICAL"
        if predicted >= WARNING_FILL_RATIO:
            return "WARNING"
        return "NORMAL"
    if metric == "depthRatio":
        if predicted >= DEPTH_CRITICAL_RATIO:
            return "CRITICAL"
        if predicted >= DEPTH_WARNING_RATIO:
            return "WARNING"
        return "NORMAL"
    if metric == "capacityRatio":
        if predicted >= CAPACITY_CRITICAL_RATIO:
            return "CRITICAL"
        if predicted >= CAPACITY_WARNING_RATIO:
            return "WARNING"
    return "NORMAL"


def _blockage_predictions(latest: Mapping[str, Any], forecast_minutes: int) -> list[dict[str, Any]]:
    predictions: list[dict[str, Any]] = []
    links = latest.get("links")
    if not isinstance(links, Mapping):
        return predictions
    for link_id, state in links.items():
        if not isinstance(state, Mapping):
            continue
        blockage_ratio = _number(state.get("blockageRatio"))
        severity = _severity_for_blockage(blockage_ratio)
        if severity == "NORMAL":
            continue
        hazard_type = "PREDICTED_BLOCKAGE_CLOSED" if severity == "CRITICAL" else "PREDICTED_BLOCKAGE_HIGH"
        predictions.append(
            {
                "source": "link",
                "targetId": str(link_id),
                "metric": "blockageRatio",
                "hazardType": hazard_type,
                "currentValue": blockage_ratio,
                "predictedValue": blockage_ratio,
                "slopePerSecond": 0.0,
                "minCurrentValue": BLOCKAGE_WARNING_RATIO,
                "rainfallLevel": _rainfall_level(latest.get("control")),
                "severity": severity,
                "forecastMinutes": forecast_minutes,
            }
        )
    return predictions


def _severity_for_blockage(blockage_ratio: float) -> str:
    if blockage_ratio >= BLOCKAGE_CRITICAL_RATIO:
        return "CRITICAL"
    if blockage_ratio >= BLOCKAGE_WARNING_RATIO:
        return "WARNING"
    return "NORMAL"


def _event_from_prediction(prediction: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eventId": f"{prediction['hazardType']}:{prediction['source']}:{prediction['targetId']}",
        "eventType": prediction["hazardType"],
        "severity": prediction["severity"],
        "source": prediction["source"],
        "sourceId": prediction["targetId"],
        "metrics": {
            "metric": prediction["metric"],
            "currentValue": prediction["currentValue"],
            "predictedValue": prediction["predictedValue"],
            "slopePerSecond": prediction["slopePerSecond"],
            "minCurrentValue": prediction.get("minCurrentValue"),
            "rainfallLevel": prediction.get("rainfallLevel"),
            "forecastMinutes": prediction["forecastMinutes"],
        },
        "reason": _prediction_reason(prediction),
    }


def _prediction_reason(prediction: Mapping[str, Any]) -> str:
    if prediction.get("metric") == "blockageRatio":
        return "실시간 관로 막힘 위험이 감지되었습니다."
    return f"{prediction['forecastMinutes']}분 뒤 {prediction['metric']} 위험이 예측되었습니다."


def _forecast_payload(
    *,
    forecast_minutes: int,
    window_seconds: int,
    latest: Mapping[str, Any],
    events: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    message: str | None = None,
) -> dict[str, Any]:
    events.sort(key=lambda event: (-_severity_rank(event["severity"]), event["eventType"], event["sourceId"]))
    predictions.sort(key=lambda item: (-_severity_rank(item["severity"]), -float(item["predictedValue"]), item["targetId"]))
    payload = {
        "ok": True,
        "forecastMinutes": forecast_minutes,
        "windowSeconds": window_seconds,
        "sampleCount": len(_samples),
        "runId": latest["runId"],
        "stepIndex": latest["stepIndex"],
        "modelTime": latest["modelTime"],
        "control": latest["control"],
        "highestSeverity": _highest_severity(events),
        "events": events,
        "predictions": [item for item in predictions if item["severity"] != "NORMAL"][:50],
    }
    if message:
        payload["message"] = message
    return payload


def _empty_forecast(
    minutes: int,
    reason: str,
    latest: Mapping[str, Any] | None = None,
    window_seconds: int = 0,
) -> dict[str, Any]:
    payload = {
        "ok": True,
        "forecastMinutes": minutes,
        "windowSeconds": window_seconds,
        "sampleCount": len(_samples),
        "highestSeverity": "NORMAL",
        "events": [],
        "predictions": [],
        "message": reason,
    }
    if latest:
        payload.update(
            {
                "runId": latest.get("runId"),
                "stepIndex": latest.get("stepIndex"),
                "modelTime": latest.get("modelTime"),
                "control": latest.get("control", {}),
            }
        )
    return payload


def _median_slope(
    samples: list[Mapping[str, Any]],
    collection_key: str,
    target_id: str,
    metric: str,
) -> float | None:
    slopes: list[float] = []
    previous_sample: Mapping[str, Any] | None = None
    previous_value: float | None = None
    for sample in samples:
        collection = sample.get(collection_key)
        if not isinstance(collection, Mapping):
            continue
        state = collection.get(target_id)
        if not isinstance(state, Mapping):
            continue
        value = _number(state.get(metric))
        if previous_sample is not None and previous_value is not None:
            delta_seconds = _number(sample.get("modelSecond")) - _number(previous_sample.get("modelSecond"))
            if delta_seconds > 0:
                slopes.append((value - previous_value) / delta_seconds)
        previous_sample = sample
        previous_value = value
    if not slopes:
        return None
    return median(slopes)


def _rainfall_level(control: Any) -> str:
    if not isinstance(control, Mapping):
        return "weak"
    rainfall_ratio = _number(control.get("rainfallRatio", control.get("rainfall")))
    if rainfall_ratio <= 0:
        return "clear"
    if rainfall_ratio >= 3:
        return "heavy"
    return "weak"


def _min_current_for_metric(metric: str, rainfall_level: str) -> float:
    return MIN_CURRENT_BY_RAIN.get(rainfall_level, MIN_CURRENT_BY_RAIN["weak"]).get(metric, 0.0)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _severity_rank(severity: Any) -> int:
    return {"NORMAL": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}.get(str(severity), 0)


def _highest_severity(events: list[Mapping[str, Any]]) -> str:
    highest = "NORMAL"
    for event in events:
        if _severity_rank(event.get("severity")) > _severity_rank(highest):
            highest = str(event.get("severity"))
    return highest
