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

from django.conf import settings


logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
LLM_DISPATCH_LOG_PATH = PACKAGE_DIR / "logs" / "llm-dispatch.jsonl"
MAX_REMEMBERED_DISPATCH_KEYS = 1000
LLM_DISPATCH_COOLDOWN_SECONDS = settings.SUPERMARIO_LLM_DISPATCH_COOLDOWN_SECONDS
LLM_DISPATCH_AGGREGATION_SECONDS = settings.SUPERMARIO_LLM_AGGREGATION_SECONDS
LLM_DISPATCH_EMERGENCY_AGGREGATION_SECONDS = settings.SUPERMARIO_LLM_EMERGENCY_AGGREGATION_SECONDS
LLM_DISPATCH_RESPONSE_TIMEOUT_SECONDS = 30
DEFAULT_LANGCHAIN_SITUATION_ID = "약한비"
CRITICAL_SEVERITY = "CRITICAL"
EMERGENCY_RUNTIME_EVENT_TYPES = {"BLOCKAGE_CLOSED", "REVERSE_FLOW"}
LANGCHAIN_SITUATION_LABEL_BY_VALUE = {
    "0": "맑음",
    "0.0": "맑음",
    "100": "약한비",
    "100.0": "약한비",
    "300": "폭우",
    "300.0": "폭우",
    "맑음": "맑음",
    "비옴": "약한비",
    "약한비": "약한비",
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
    "rainfall",
    "rainfallRatio",
    "rainfallPercent",
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

    first = candidates[0]
    payload = dict(first.payload)
    trigger = dict(first.trigger)
    context = dict(first.context)
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

    try:
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
    """LangChain 서버가 Telegram 알림에 사용할 bot token과 chat ID 목록을 조회한다."""

    from apps.notification.models import BotToken, NotificationRecipient

    bot_token = BotToken.objects.order_by("id").first()
    return {
        "TELEGRAM_BOT_TOKEN": bot_token.bot_token if bot_token else None,
        "TELEGRAM_CHAT_ID": list(NotificationRecipient.objects.order_by("id").values_list("chat_id", flat=True)),
    }


def extract_situation_id(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    """React 강수 preset과 context 값을 LangChain 상황 ID 세 값으로 정규화한다."""

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
    """값을 `맑음`, `약한비`, `폭우` 중 하나로 변환한다."""

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
    return LANGCHAIN_SITUATION_LABEL_BY_VALUE.get(normalized)


def normalize_rainfall_preset_id(value: Any, key: str) -> str | None:
    """강수 제어값을 React preset 기준 상황 ID로 변환한다."""

    label = normalize_langchain_situation_id(value)
    if label:
        return label

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if key == "rainfallRatio":
        ratio_label_by_value = {
            0.0: "맑음",
            1.0: "약한비",
            3.0: "폭우",
        }
        return ratio_label_by_value.get(number)

    return None


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
