"""SWMM 위험 snapshot을 SuperMario_LLM/LangChain 서버로 전달하는 모듈."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from channels.layers import get_channel_layer
from django.conf import settings

from apps.simulation.realtime_alerts import build_realtime_alert


logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
LLM_DISPATCH_LOG_PATH = PACKAGE_DIR / "logs" / "llm-dispatch.jsonl"
MAX_REMEMBERED_DISPATCH_KEYS = 1000
LLM_DISPATCH_COOLDOWN_SECONDS = settings.SUPERMARIO_LLM_DISPATCH_COOLDOWN_SECONDS
LLM_DISPATCH_AGGREGATION_SECONDS = settings.SUPERMARIO_LLM_AGGREGATION_SECONDS
LLM_DISPATCH_EMERGENCY_AGGREGATION_SECONDS = settings.SUPERMARIO_LLM_EMERGENCY_AGGREGATION_SECONDS
LLM_DISPATCH_RESPONSE_TIMEOUT_SECONDS = 30
DEFAULT_LANGCHAIN_SITUATION_ID = "우천"
CRITICAL_SEVERITY = "CRITICAL"
EMERGENCY_RUNTIME_EVENT_TYPES = {"BLOCKAGE_CLOSED", "REVERSE_FLOW"}
SIMULATION_GROUP_NAME = "simulation"
LANGCHAIN_SITUATION_LABEL_BY_VALUE = {
    "0": "맑음",
    "0.0": "맑음",
    "10": "우천",
    "10.0": "우천",
    "100": "호우",
    "100.0": "호우",
    "300": "폭우",
    "300.0": "폭우",
    "500": "폭우",
    "500.0": "폭우",
    "맑음": "맑음",
    "비옴": "우천",
    "약한비": "우천",
    "우천": "우천",
    "호우": "호우",
    "폭우": "폭우",
}
LANGCHAIN_SITUATION_EXPLICIT_KEYS = (
    "id",
    "situationId",
    "scenarioId",
    "rainfallPreset",
    "rainfallPresetValue",
    "rainfallLabel",
    "reason",
)
LANGCHAIN_SITUATION_RAINFALL_KEYS = (
    "rainfallPercent",
    "rainfall",
    "rainfallRatio",
)
LLM_CONTEXT_OMIT_KEYS = {
    "bytes",
    "contextExports",
    "directory",
    "enabledBy",
    "exportContextLevel",
    "exportedAt",
    "exportKey",
    "exportPurpose",
    "files",
    "manifestBytes",
    "manifestPath",
    "modelPath",
    "path",
    "rawSnapshotRef",
    "runtimeModelPath",
    "tickLogPath",
    "weather",
}

_scheduled_dispatch_keys: set[str] = set()
_scheduled_dispatch_key_order: deque[str] = deque()
_last_llm_dispatch_scheduled_at: float | None = None
_cooldown_until: float | None = None
_cooldown_task: asyncio.Task[None] | None = None
_normal_batch: list["DispatchCandidate"] = []
_normal_batch_task: asyncio.Task[None] | None = None
_emergency_batch: list["DispatchCandidate"] = []
_emergency_batch_task: asyncio.Task[None] | None = None
_pending_queue: list["DispatchCandidate"] = []
_pending_signatures: set[str] = set()
_last_pending_queue_signatures: set[str] = set()


@dataclass
class DispatchCandidate:
    payload: Mapping[str, Any]
    trigger: Mapping[str, Any]
    context: Mapping[str, Any]
    sanitized_context: Mapping[str, Any]
    dispatch_key: str
    signatures: set[str]


def schedule_llm_analysis_dispatch(payload: Mapping[str, Any]) -> bool:
    """LEVEL 23 문자 발송 정책에 따라 LLM 분석 요청을 예약하거나 queue에 넣는다."""

    trigger = payload.get("llmTrigger")
    if not isinstance(trigger, Mapping) or not trigger.get("shouldTrigger"):
        return False

    context = trigger.get("context")
    if not isinstance(context, Mapping):
        logger.warning("LLM trigger가 설정되었지만 context payload가 없습니다.")
        return False
    sanitized_context = sanitize_llm_context(context)

    dispatch_key = build_llm_dispatch_key(payload, trigger)
    signatures = critical_issue_signatures(trigger, context)
    if not signatures:
        append_llm_dispatch_log(
            payload,
            trigger,
            sanitized_context,
            dispatch_key,
            status="severity_skipped",
            detail={"reason": "no_critical_issue"},
        )
        return False

    if not remember_dispatch_key(dispatch_key):
        return False

    candidate = DispatchCandidate(
        payload=payload,
        trigger=trigger,
        context=context,
        sanitized_context=sanitized_context,
        dispatch_key=dispatch_key,
        signatures=signatures,
    )

    if is_emergency_runtime_candidate(trigger, context):
        return queue_emergency_candidate(candidate)

    now = time.monotonic()
    expire_cooldown_if_needed(now)
    if llm_dispatch_cooldown_remaining_seconds(now) > 0:
        return queue_pending_candidate(candidate, now)

    return queue_normal_candidate(candidate)


def queue_normal_candidate(candidate: DispatchCandidate) -> bool:
    """일반 CRITICAL 위험을 aggregation window에 누적한다."""

    global _normal_batch_task

    if not add_unique_candidate(_normal_batch, candidate):
        append_llm_dispatch_log(
            candidate.payload,
            candidate.trigger,
            candidate.sanitized_context,
            candidate.dispatch_key,
            status="aggregation_duplicate_skipped",
            detail={"signatures": sorted(candidate.signatures)},
        )
        return False

    append_llm_dispatch_log(
        candidate.payload,
        candidate.trigger,
        candidate.sanitized_context,
        candidate.dispatch_key,
        status="aggregation_queued",
        detail={
            "aggregationSeconds": LLM_DISPATCH_AGGREGATION_SECONDS,
            "signatures": sorted(candidate.signatures),
        },
    )
    if _normal_batch_task is None or _normal_batch_task.done():
        _normal_batch_task = asyncio.create_task(flush_normal_batch_after_delay())
    return True


def queue_emergency_candidate(candidate: DispatchCandidate) -> bool:
    """runtime 막힘/역류 위험을 emergency aggregation window에 누적한다."""

    global _emergency_batch_task

    if not add_unique_candidate(_emergency_batch, candidate):
        append_llm_dispatch_log(
            candidate.payload,
            candidate.trigger,
            candidate.sanitized_context,
            candidate.dispatch_key,
            status="emergency_duplicate_skipped",
            detail={"signatures": sorted(candidate.signatures)},
        )
        return False

    append_llm_dispatch_log(
        candidate.payload,
        candidate.trigger,
        candidate.sanitized_context,
        candidate.dispatch_key,
        status="emergency_queued",
        detail={
            "aggregationSeconds": LLM_DISPATCH_EMERGENCY_AGGREGATION_SECONDS,
            "signatures": sorted(candidate.signatures),
        },
    )
    if _emergency_batch_task is None or _emergency_batch_task.done():
        _emergency_batch_task = asyncio.create_task(flush_emergency_batch_after_delay())
    return True


def queue_pending_candidate(candidate: DispatchCandidate, now: float) -> bool:
    """cooldown 중 일반 위험을 pending queue에 누적한다."""

    new_signatures = candidate.signatures - _pending_signatures - _last_pending_queue_signatures
    remaining_seconds = llm_dispatch_cooldown_remaining_seconds(now)
    if not new_signatures:
        append_llm_dispatch_log(
            candidate.payload,
            candidate.trigger,
            candidate.sanitized_context,
            candidate.dispatch_key,
            status="pending_duplicate_skipped",
            detail={
                "remainingSeconds": round(remaining_seconds, 3),
                "signatures": sorted(candidate.signatures),
            },
        )
        return False

    _pending_queue.append(candidate)
    _pending_signatures.update(new_signatures)
    append_llm_dispatch_log(
        candidate.payload,
        candidate.trigger,
        candidate.sanitized_context,
        candidate.dispatch_key,
        status="pending_queued",
        detail={
            "remainingSeconds": round(remaining_seconds, 3),
            "signatures": sorted(new_signatures),
        },
    )
    return True


async def flush_normal_batch_after_delay() -> None:
    await asyncio.sleep(max(0.0, LLM_DISPATCH_AGGREGATION_SECONDS))
    dispatch_candidate_batch("aggregation_window")


async def flush_emergency_batch_after_delay() -> None:
    await asyncio.sleep(max(0.0, LLM_DISPATCH_EMERGENCY_AGGREGATION_SECONDS))
    dispatch_candidate_batch("emergency_aggregation", emergency=True)


async def flush_pending_after_cooldown(delay_seconds: float) -> None:
    await asyncio.sleep(max(0.0, delay_seconds))
    expire_cooldown_if_needed(time.monotonic(), force=True)


def dispatch_candidate_batch(reason: str, *, emergency: bool = False) -> bool:
    """현재 batch를 하나의 LLM 요청으로 묶어 예약한다."""

    global _normal_batch, _normal_batch_task, _emergency_batch, _emergency_batch_task

    if emergency:
        candidates = list(_emergency_batch)
        _emergency_batch = []
        _emergency_batch_task = None
        apply_cooldown = False
    else:
        candidates = list(_normal_batch)
        _normal_batch = []
        _normal_batch_task = None
        apply_cooldown = True

    if not candidates:
        return False

    payload, trigger, context, dispatch_key = merge_dispatch_candidates(candidates, reason)
    schedule_dispatch_now(payload, trigger, context, dispatch_key, apply_cooldown=apply_cooldown)
    return True


def dispatch_pending_queue(reason: str = "cooldown_window_elapsed") -> bool:
    """cooldown 종료 시 pending queue를 하나의 LLM 요청으로 묶어 예약한다."""

    global _pending_queue, _pending_signatures, _last_pending_queue_signatures

    candidates = list(_pending_queue)
    _pending_queue = []
    sent_signatures = set(_pending_signatures)
    _pending_signatures = set()
    if not candidates:
        _last_pending_queue_signatures = set()
        return False

    payload, trigger, context, dispatch_key = merge_dispatch_candidates(candidates, reason)
    _last_pending_queue_signatures = sent_signatures
    schedule_dispatch_now(payload, trigger, context, dispatch_key, apply_cooldown=True)
    return True


def schedule_dispatch_now(
    payload: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
    dispatch_key: str,
    *,
    apply_cooldown: bool,
) -> None:
    """묶음이 확정된 LLM 요청을 즉시 background task로 예약한다."""

    global _last_llm_dispatch_scheduled_at

    sanitized_context = sanitize_llm_context(context)
    _last_llm_dispatch_scheduled_at = time.monotonic()
    append_llm_dispatch_log(payload, trigger, sanitized_context, dispatch_key, status="scheduled")
    logger.warning(
        "LLM dispatch scheduled. dispatchKey=%s runId=%s stepIndex=%s reason=%s issues=%s",
        dispatch_key,
        payload.get("runId"),
        payload.get("stepIndex"),
        trigger.get("reason"),
        summarize_triggered_issues(trigger),
    )
    asyncio.create_task(dispatch_llm_analysis(payload, trigger, sanitized_context, dispatch_key))
    if apply_cooldown:
        start_dispatch_cooldown()


def start_dispatch_cooldown() -> None:
    """일반 문자 발송 후 cooldown window를 시작한다."""

    global _cooldown_until, _cooldown_task

    now = time.monotonic()
    _cooldown_until = now + max(0.0, LLM_DISPATCH_COOLDOWN_SECONDS)
    if _cooldown_task is None or _cooldown_task.done():
        _cooldown_task = asyncio.create_task(flush_pending_after_cooldown(LLM_DISPATCH_COOLDOWN_SECONDS))


def expire_cooldown_if_needed(now: float, *, force: bool = False) -> None:
    """cooldown이 끝났으면 pending queue를 flush한다."""

    global _cooldown_until, _cooldown_task

    if _cooldown_until is None:
        return
    if not force and now < _cooldown_until:
        return
    _cooldown_until = None
    _cooldown_task = None
    dispatch_pending_queue()


def llm_dispatch_cooldown_remaining_seconds(now: float | None = None) -> float:
    """다음 일반 LLM 발송까지 남은 cooldown 시간을 초 단위로 반환한다."""

    if _cooldown_until is None:
        return 0.0

    current_time = time.monotonic() if now is None else now
    return max(0.0, _cooldown_until - current_time)


def add_unique_candidate(batch: list[DispatchCandidate], candidate: DispatchCandidate) -> bool:
    existing_signatures = set().union(*(item.signatures for item in batch)) if batch else set()
    if candidate.signatures <= existing_signatures:
        return False
    batch.append(candidate)
    return True


def merge_dispatch_candidates(
    candidates: list[DispatchCandidate],
    reason: str,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], str]:
    """여러 후보의 위험 이벤트를 하나의 LLM trigger/context로 병합한다."""

    latest = candidates[-1]
    payload = dict(latest.payload)
    trigger = dict(latest.trigger)
    context = dict(latest.context)
    trigger["reason"] = reason

    triggered_issues = merge_records(
        [issue for candidate in candidates for issue in candidate.trigger.get("triggeredIssues") or []],
        key_for_issue,
    )
    risk_events = merge_records(
        [event for candidate in candidates for event in candidate.context.get("riskEvents") or []],
        key_for_issue,
    )
    forecast_predictions = merge_records(
        [prediction for candidate in candidates for prediction in candidate.context.get("forecastPredictions") or []],
        key_for_prediction,
    )
    trigger["triggeredIssues"] = triggered_issues
    context["riskEvents"] = risk_events
    if forecast_predictions:
        context["forecastPredictions"] = forecast_predictions
    context["highestSeverity"] = CRITICAL_SEVERITY
    payload["llmTrigger"] = trigger
    dispatch_key = build_llm_dispatch_key(payload, trigger)
    return payload, trigger, context, dispatch_key


def merge_records(records: list[Any], key_builder: Callable[[Mapping[str, Any]], str]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        key = key_builder(record)
        if key not in merged:
            merged[key] = dict(record)
    return list(merged.values())


def critical_issue_signatures(trigger: Mapping[str, Any], context: Mapping[str, Any]) -> set[str]:
    records = list(trigger.get("triggeredIssues") or []) + list(context.get("riskEvents") or [])
    return {
        key_for_issue(record)
        for record in records
        if isinstance(record, Mapping) and str(record.get("severity") or "").upper() == CRITICAL_SEVERITY
    }


def is_emergency_runtime_candidate(trigger: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    if str(trigger.get("contextLevel") or context.get("contextLevel") or "").lower() == "forecast":
        return False
    records = list(trigger.get("triggeredIssues") or []) + list(context.get("riskEvents") or [])
    return any(
        isinstance(record, Mapping)
        and str(record.get("severity") or "").upper() == CRITICAL_SEVERITY
        and str(record.get("eventType") or "") in EMERGENCY_RUNTIME_EVENT_TYPES
        for record in records
    )


def key_for_issue(issue: Mapping[str, Any]) -> str:
    event_type = str(issue.get("eventType") or "")
    source = str(issue.get("source") or "")
    source_id = str(issue.get("sourceId") or "")
    if event_type or source or source_id:
        return ":".join([event_type or "UNKNOWN_EVENT", source or "unknown_source", source_id or "unknown_source_id"])
    return str(issue.get("issueId") or issue.get("eventId") or "unknown_issue")


def key_for_prediction(prediction: Mapping[str, Any]) -> str:
    return ":".join(
        [
            str(prediction.get("hazardType") or prediction.get("eventType") or "UNKNOWN_EVENT"),
            str(prediction.get("source") or "unknown_source"),
            str(prediction.get("targetId") or prediction.get("sourceId") or "unknown_source_id"),
        ]
    )


def reset_dispatch_policy_state() -> None:
    """테스트와 개발 서버 재초기화에 사용하는 in-memory dispatch 정책 상태 초기화."""

    global _last_llm_dispatch_scheduled_at, _cooldown_until, _cooldown_task
    global _normal_batch, _normal_batch_task, _emergency_batch, _emergency_batch_task
    global _pending_queue, _pending_signatures, _last_pending_queue_signatures

    _scheduled_dispatch_keys.clear()
    _scheduled_dispatch_key_order.clear()
    _last_llm_dispatch_scheduled_at = None
    _cooldown_until = None
    _cooldown_task = None
    _normal_batch = []
    _normal_batch_task = None
    _emergency_batch = []
    _emergency_batch_task = None
    _pending_queue = []
    _pending_signatures = set()
    _last_pending_queue_signatures = set()


async def dispatch_llm_analysis(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
    dispatch_key: str,
) -> dict[str, Any]:
    """SuperMario_LLM/LangChain 분석 endpoint로 위험 context를 POST한다."""

    if skipped := skip_llm_dispatch_for_engine_state(snapshot, trigger, dispatch_key):
        return skipped

    request_payload = await build_langchain_request_payload_async(snapshot, trigger, context)

    logger.debug(
        "LLM dispatch started. dispatchKey=%s runId=%s stepIndex=%s reason=%s id=%s contextKeys=%s",
        dispatch_key,
        snapshot.get("runId"),
        snapshot.get("stepIndex"),
        trigger.get("reason"),
        request_payload.get("id"),
        sorted(context.keys()),
    )

    auto_pause_result = await pause_engine_for_llm_dispatch(snapshot, trigger, dispatch_key)
    if auto_pause_result and auto_pause_result.get("status") == "engine_state_skipped":
        return auto_pause_result

    try:
        await broadcast_llm_request_alert(snapshot, trigger, dispatch_key)
        response = await post_langchain_analysis(request_payload)
    except (TimeoutError, socket.timeout) as exc:
        detail = {
            "error": str(exc),
            "timeoutSeconds": LLM_DISPATCH_RESPONSE_TIMEOUT_SECONDS,
        }
        logger.info(
            "LLM dispatch response timeout. dispatchKey=%s timeoutSeconds=%s",
            dispatch_key,
            LLM_DISPATCH_RESPONSE_TIMEOUT_SECONDS,
        )
        append_llm_dispatch_result_log(
            snapshot,
            trigger,
            dispatch_key,
            status="response_timeout",
            detail=detail,
        )
        return {
            "ok": False,
            "status": "response_timeout",
            "dispatchKey": dispatch_key,
            "targetService": "SuperMario_LLM",
            **detail,
        }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        detail = {
            "statusCode": exc.code,
            "responseBody": body,
        }
        logger.warning(
            "LLM dispatch failed with HTTP status. dispatchKey=%s statusCode=%s body=%s",
            dispatch_key,
            exc.code,
            body[:500],
        )
        append_llm_dispatch_result_log(
            snapshot,
            trigger,
            dispatch_key,
            status="http_error",
            detail=detail,
        )
        return {
            "ok": False,
            "status": "http_error",
            "dispatchKey": dispatch_key,
            "targetService": "SuperMario_LLM",
            **detail,
        }
    except Exception as exc:  # pragma: no cover - 외부 서버 장애는 시뮬레이션을 막지 않는다.
        detail = {
            "error": str(exc),
        }
        logger.warning("LLM dispatch failed. dispatchKey=%s error=%s", dispatch_key, exc)
        append_llm_dispatch_result_log(
            snapshot,
            trigger,
            dispatch_key,
            status="dispatch_failed",
            detail=detail,
        )
        return {
            "ok": False,
            "status": "dispatch_failed",
            "dispatchKey": dispatch_key,
            "targetService": "SuperMario_LLM",
            **detail,
        }

    logger.info(
        "LLM dispatch completed. dispatchKey=%s statusCode=%s",
        dispatch_key,
        response.get("statusCode"),
    )
    append_llm_dispatch_result_log(
        snapshot,
        trigger,
        dispatch_key,
        status="sent",
        detail=response,
    )
    return {
        "ok": True,
        "status": "sent",
        "dispatchKey": dispatch_key,
        "targetService": "SuperMario_LLM",
        **response,
    }


def skip_llm_dispatch_for_engine_state(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    dispatch_key: str,
) -> dict[str, Any] | None:
    """현재 엔진이 멈춘 상태면 LLM/Telegram 요청 직전에 dispatch를 취소한다."""

    detail = current_engine_dispatch_skip_detail(snapshot)
    if detail is None:
        return None

    logger.info(
        "LLM dispatch skipped because engine is inactive. dispatchKey=%s reason=%s engineStatus=%s",
        dispatch_key,
        detail.get("reason"),
        detail.get("engineStatus"),
    )
    append_llm_dispatch_result_log(
        snapshot,
        trigger,
        dispatch_key,
        status="engine_state_skipped",
        detail=detail,
    )
    return {
        "ok": False,
        "status": "engine_state_skipped",
        "dispatchKey": dispatch_key,
        "targetService": "SuperMario_LLM",
        **detail,
    }


async def pause_engine_for_llm_dispatch(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    dispatch_key: str,
) -> dict[str, Any] | None:
    """실제 LLM 요청 직전에 실행 중인 SWMM 엔진을 일시정지하고 UI에 알린다."""

    if skipped := skip_llm_dispatch_for_engine_state(snapshot, trigger, dispatch_key):
        return skipped

    try:
        simulation_state = simulation_state_for_dispatch()
        raw_status = await simulation_state.engine.pause()
        pause_payload = dict(raw_status) if isinstance(raw_status, Mapping) else {}

        try:
            current_status = simulation_state.status_payload()
            if isinstance(current_status, Mapping):
                pause_payload.update(current_status)
        except Exception as exc:  # pragma: no cover - pause 자체가 성공했으면 UI 전파는 계속 시도한다.
            logger.warning("LLM dispatch paused engine but status refresh failed: %s", exc)

        pause_payload.update(
            {
                "type": "paused",
                "pauseReason": "llm_dispatch",
                "llmDispatchKey": dispatch_key,
                "llmTriggerReason": trigger.get("reason"),
            }
        )
        await broadcast_engine_pause_status(pause_payload)
    except Exception as exc:  # pragma: no cover - 예기치 못한 pause 실패가 알림 경로를 숨기지 않게 한다.
        detail = {"error": str(exc)}
        logger.warning("LLM dispatch engine auto-pause failed. dispatchKey=%s error=%s", dispatch_key, exc)
        append_llm_dispatch_result_log(
            snapshot,
            trigger,
            dispatch_key,
            status="engine_auto_pause_failed",
            detail=detail,
        )
        return {
            "ok": False,
            "status": "engine_auto_pause_failed",
            "dispatchKey": dispatch_key,
            "targetService": "SuperMario_LLM",
            **detail,
        }

    detail = {
        "reason": "llm_dispatch",
        "engineStatus": summarize_engine_status_for_dispatch(pause_payload),
    }
    logger.info(
        "LLM dispatch auto-paused engine. dispatchKey=%s runId=%s stepIndex=%s",
        dispatch_key,
        pause_payload.get("runId"),
        pause_payload.get("stepIndex"),
    )
    append_llm_dispatch_result_log(
        snapshot,
        trigger,
        dispatch_key,
        status="engine_auto_paused",
        detail=detail,
    )
    return {
        "ok": True,
        "status": "engine_auto_paused",
        "dispatchKey": dispatch_key,
        "targetService": "SuperMario_LLM",
        **detail,
    }


async def broadcast_engine_pause_status(payload: Mapping[str, Any]) -> None:
    """React가 기존 status WebSocket 처리로 PAUSED UI를 표시하도록 상태를 전송한다."""

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    await channel_layer.group_send(
        SIMULATION_GROUP_NAME,
        {
            "type": "swmm.message",
            "payload": dict(payload),
        },
    )


def current_engine_dispatch_skip_detail(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """현재 SWMM 엔진 상태를 보고 dispatch를 중단해야 하면 detail을 반환한다."""

    status = current_engine_status_payload()
    if status is None:
        return None

    engine_status = summarize_engine_status_for_dispatch(status)
    snapshot_run_id = snapshot.get("runId")
    current_run_id = status.get("runId")

    if not status.get("hasSession"):
        reason = "engine_stopped"
    elif status.get("paused"):
        reason = "engine_paused"
    elif not status.get("running"):
        reason = "engine_not_running"
    elif snapshot_run_id and current_run_id and str(snapshot_run_id) != str(current_run_id):
        reason = "run_id_mismatch"
    else:
        return None

    return {
        "reason": reason,
        "snapshotRunId": snapshot_run_id,
        "currentRunId": current_run_id,
        "engineStatus": engine_status,
    }


def current_engine_status_payload() -> Mapping[str, Any] | None:
    """순환 import를 피하기 위해 dispatch 시점에 simulation state를 lazy 조회한다."""

    try:
        status = simulation_state_for_dispatch().status_payload()
    except Exception as exc:  # pragma: no cover - 상태 조회 실패가 dispatch 장애로 번지지 않게 한다.
        logger.warning("LLM dispatch engine status check failed: %s", exc)
        return None

    return status if isinstance(status, Mapping) else None


def simulation_state_for_dispatch():
    """순환 import를 피하기 위해 필요한 시점에 simulation state module을 가져온다."""

    from apps.simulation import state as simulation_state

    return simulation_state


def summarize_engine_status_for_dispatch(status: Mapping[str, Any]) -> dict[str, Any]:
    """dispatch log에 남길 엔진 상태만 추려 로컬 경로 노출을 피한다."""

    return {
        "running": bool(status.get("running")),
        "paused": bool(status.get("paused")),
        "hasSession": bool(status.get("hasSession")),
        "runId": status.get("runId"),
        "stepIndex": status.get("stepIndex"),
        "modelTime": status.get("modelTime"),
        "lastError": status.get("lastError"),
    }


async def broadcast_llm_request_alert(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    dispatch_key: str,
) -> None:
    """실제 LLM HTTP 요청 직전에 React WebSocket 소비자에게 알린다."""

    realtime_alert = build_realtime_alert(trigger, source="llm_request")
    if realtime_alert is None:
        return
    realtime_alert["dispatchKey"] = dispatch_key

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    await channel_layer.group_send(
        SIMULATION_GROUP_NAME,
        {
            "type": "swmm.message",
            "payload": {
                "type": "llm_alert",
                "ok": True,
                "runId": snapshot.get("runId"),
                "stepIndex": snapshot.get("stepIndex"),
                "modelTime": snapshot.get("modelTime"),
                "dispatchKey": dispatch_key,
                "realtimeAlert": realtime_alert,
            },
        },
    )


def build_langchain_request_payload(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """LangChain 서버가 요구하는 `{id, swmm_raw_data}` payload를 만든다."""

    return {
        "id": extract_situation_id(snapshot, trigger, context),
        "swmm_raw_data": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        **build_notification_payload(),
    }


async def build_langchain_request_payload_async(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """async dispatch 안에서 안전하게 LangChain 요청 payload를 만든다."""

    return {
        "id": extract_situation_id(snapshot, trigger, context),
        "swmm_raw_data": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
        **await asyncio.to_thread(build_notification_payload),
    }


def build_notification_payload() -> dict[str, Any]:
    """LangChain 서버가 Telegram 알림에 사용할 token과 DB 수신자 목록을 만든다."""

    return {
        "TELEGRAM_BOT_TOKEN": notification_bot_token_from_settings(),
        "TELEGRAM_CHAT_ID": notification_chat_ids_from_db(),
    }


def notification_bot_token_from_settings() -> str | None:
    token = str(getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    return token or None


def notification_chat_ids_from_db() -> list[str]:
    from apps.notification.models import NotificationRecipient

    return [
        str(chat_id).strip()
        for chat_id in NotificationRecipient.objects.order_by("id").values_list("chat_id", flat=True)
        if str(chat_id).strip()
    ]


def extract_situation_id(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    """React 강수 preset과 context 값을 LangChain 상황 ID 네 단계로 정규화한다."""

    control_candidates = [
        snapshot.get("control"),
        context.get("control"),
    ]
    simulation = context.get("simulation")
    if isinstance(simulation, Mapping):
        control_candidates.append(simulation.get("control"))

    for control in control_candidates:
        label = extract_control_situation_id(control)
        if label:
            return label

    for value in (trigger.get("reason"), context.get("highestSeverity")):
        label = normalize_langchain_situation_id(value)
        if label:
            return label

    return DEFAULT_LANGCHAIN_SITUATION_ID


def extract_control_situation_id(control: Any) -> str | None:
    """control payload에서 명시 상황값 또는 강수 preset 값을 찾아 정규화한다."""

    if not isinstance(control, Mapping):
        return None

    for key in LANGCHAIN_SITUATION_EXPLICIT_KEYS:
        label = normalize_langchain_situation_id(control.get(key))
        if label:
            return label

    for key in LANGCHAIN_SITUATION_RAINFALL_KEYS:
        label = normalize_rainfall_preset_id(control.get(key), key)
        if label:
            return label

    return None


def normalize_langchain_situation_id(value: Any) -> str | None:
    """값을 `맑음`, `우천`, `호우`, `폭우` 중 하나로 변환한다."""

    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text in LANGCHAIN_SITUATION_LABEL_BY_VALUE:
            return LANGCHAIN_SITUATION_LABEL_BY_VALUE[text]
        value = text

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    normalized = str(int(number)) if number.is_integer() else str(number)
    return LANGCHAIN_SITUATION_LABEL_BY_VALUE.get(normalized) or rainfall_percent_to_situation_label(number)


def rainfall_percent_to_situation_label(percent: float) -> str | None:
    """React 강수 percent 값을 LLM 상황 ID로 변환한다."""

    if percent < 0:
        return None
    if percent < 10:
        return "맑음"
    if percent < 100:
        return "우천"
    if percent == 100:
        return "호우"
    return "폭우"


def rainfall_ratio_to_percent(ratio: float) -> float:
    """runtime ratio와 React raw percent가 섞인 rainfallRatio 값을 percent로 변환한다."""

    if ratio <= 3.0:
        return ratio * 100.0
    return ratio


def normalize_rainfall_preset_id(value: Any, key: str) -> str | None:
    """강수 제어값을 React preset 기준 상황 ID로 변환한다."""

    if isinstance(value, str):
        label = normalize_langchain_situation_id(value)
        if label:
            return label

    if key not in {"rainfall", "rainfallRatio", "rainfallPercent"}:
        label = normalize_langchain_situation_id(value)
        if label:
            return label

    if key == "rainfallPercent":
        label = normalize_langchain_situation_id(value)
        if label:
            return label

    if key == "rainfall":
        label = normalize_langchain_situation_id(value)
        if label:
            return label

    if key != "rainfallRatio":
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return rainfall_percent_to_situation_label(rainfall_ratio_to_percent(number))


async def post_langchain_analysis(payload: Mapping[str, Any]) -> dict[str, Any]:
    """표준 라이브러리 HTTP client로 LangChain 분석 endpoint에 POST한다."""

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        settings.SUPERMARIO_LLM_ANALYZE_URL,
        data=body,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
        method="POST",
    )

    def send() -> dict[str, Any]:
        with urllib.request.urlopen(request, timeout=LLM_DISPATCH_RESPONSE_TIMEOUT_SECONDS) as response:
            response_body = response.read(65536).decode("utf-8", errors="replace")
            return {
                "statusCode": response.status,
                "responseBody": response_body,
            }

    return await asyncio.to_thread(send)


def sanitize_llm_context(value: Any) -> Any:
    """로컬 경로와 디버그 export metadata를 제거한 LLM context 복사본을 반환한다."""

    if isinstance(value, Mapping):
        sanitized = {}
        for key, entry_value in value.items():
            if str(key) in LLM_CONTEXT_OMIT_KEYS:
                continue
            cleaned = sanitize_llm_context(entry_value)
            if cleaned in ({}, [], None):
                continue
            sanitized[str(key)] = cleaned
        return sanitized
    if isinstance(value, list):
        return [
            cleaned
            for entry in value
            if (cleaned := sanitize_llm_context(entry)) not in ({}, [], None)
        ]
    return value


def append_llm_dispatch_log(
    payload: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
    dispatch_key: str,
    *,
    status: str,
    detail: Mapping[str, Any] | None = None,
) -> None:
    """LLM 전송 후보 trigger마다 로컬 JSONL 기록을 남긴다."""

    try:
        LLM_DISPATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "loggedAt": datetime.now().isoformat(timespec="milliseconds"),
            "dispatchKey": dispatch_key,
            "status": status,
            "runId": payload.get("runId"),
            "stepIndex": payload.get("stepIndex"),
            "modelTime": payload.get("modelTime"),
            "reason": trigger.get("reason"),
            "contextLevel": trigger.get("contextLevel"),
            "highestSeverity": context.get("highestSeverity"),
            "riskEventCount": len(context.get("riskEvents") or []),
            "contextSanitized": True,
            "triggeredIssues": summarize_triggered_issues(trigger),
        }
        if detail:
            record["detail"] = dict(detail)
        with LLM_DISPATCH_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:  # pragma: no cover - local logging must not stop simulation
        logger.warning("LLM dispatch 예약 log 기록 실패: %s", exc)


def append_llm_dispatch_result_log(
    payload: Mapping[str, Any],
    trigger: Mapping[str, Any],
    dispatch_key: str,
    *,
    status: str,
    detail: Mapping[str, Any],
) -> None:
    """실제 dispatch 결과 JSONL record 하나를 기록한다."""

    try:
        LLM_DISPATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "loggedAt": datetime.now().isoformat(timespec="milliseconds"),
            "dispatchKey": dispatch_key,
            "status": status,
            "runId": payload.get("runId"),
            "stepIndex": payload.get("stepIndex"),
            "modelTime": payload.get("modelTime"),
            "reason": trigger.get("reason"),
            "targetUrl": settings.SUPERMARIO_LLM_ANALYZE_URL,
            "detail": dict(detail),
        }
        with LLM_DISPATCH_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:  # pragma: no cover - local logging must not stop simulation
        logger.warning("LLM dispatch 결과 log 기록 실패: %s", exc)


def summarize_triggered_issues(trigger: Mapping[str, Any]) -> list[dict[str, Any]]:
    """콘솔과 파일 로그에 남길 위험 이슈 요약을 만든다."""

    issues: list[dict[str, Any]] = []
    for issue in trigger.get("triggeredIssues") or []:
        if not isinstance(issue, Mapping):
            continue
        issues.append({
            "issueId": issue.get("issueId"),
            "eventType": issue.get("eventType"),
            "severity": issue.get("severity"),
            "sourceId": issue.get("sourceId"),
            "displayName": issue.get("displayName"),
            "sourceEditorName": issue.get("sourceEditorName"),
            "fromNode": issue.get("fromNode"),
            "fromNodeName": issue.get("fromNodeName"),
            "toNode": issue.get("toNode"),
            "toNodeName": issue.get("toNodeName"),
        })
    return issues


def build_llm_dispatch_key(payload: Mapping[str, Any], trigger: Mapping[str, Any]) -> str:
    """동일한 LLM trigger 중복 전송을 막는 key를 만든다."""

    issue_parts: list[str] = []
    for issue in trigger.get("triggeredIssues") or []:
        if not isinstance(issue, Mapping):
            continue
        issue_parts.append(
            ":".join(
                [
                    str(issue.get("issueId") or "unknown_issue"),
                    str(issue.get("severity") or "NORMAL"),
                    str(issue.get("lastTriggeredStepIndex") or payload.get("stepIndex") or 0),
                ]
            )
        )
    issue_key = "|".join(sorted(issue_parts)) or str(trigger.get("reason") or "unknown_trigger")
    return ":".join(
        [
            str(payload.get("runId") or "unknown_run"),
            str(payload.get("stepIndex") or 0),
            issue_key,
        ]
    )


def remember_dispatch_key(dispatch_key: str) -> bool:
    """dispatch key를 기억하고 이미 본 key이면 False를 반환한다."""

    if dispatch_key in _scheduled_dispatch_keys:
        return False

    _scheduled_dispatch_keys.add(dispatch_key)
    _scheduled_dispatch_key_order.append(dispatch_key)
    while len(_scheduled_dispatch_key_order) > MAX_REMEMBERED_DISPATCH_KEYS:
        old_key = _scheduled_dispatch_key_order.popleft()
        _scheduled_dispatch_keys.discard(old_key)
    return True
