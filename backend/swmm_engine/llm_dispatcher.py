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
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from django.conf import settings


logger = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).resolve().parent
LLM_DISPATCH_LOG_PATH = PACKAGE_DIR / "logs" / "llm-dispatch.jsonl"
MAX_REMEMBERED_DISPATCH_KEYS = 1000
LLM_DISPATCH_COOLDOWN_SECONDS = 300
LLM_DISPATCH_RESPONSE_TIMEOUT_SECONDS = 30
DEFAULT_LANGCHAIN_SITUATION_ID = "약한비"
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


def schedule_llm_analysis_dispatch(payload: Mapping[str, Any]) -> bool:
    """snapshot이 요청한 LLM 분석 호출을 background task로 예약한다.

    WebSocket broadcast 직전에 호출되며, 같은 위험 trigger 중복과 짧은 시간 안의
    반복 발송을 막는다.

    반환:
    - True: 이번 snapshot은 LLM dispatch 대상이라 background task로 예약됨.
    - False: trigger가 없거나, 중복 또는 쿨다운으로 건너뜀.
    """

    global _last_llm_dispatch_scheduled_at

    trigger = payload.get("llmTrigger")
    if not isinstance(trigger, Mapping) or not trigger.get("shouldTrigger"):
        return False

    context = trigger.get("context")
    if not isinstance(context, Mapping):
        logger.warning("LLM trigger가 설정되었지만 context payload가 없습니다.")
        return False
    sanitized_context = sanitize_llm_context(context)

    dispatch_key = build_llm_dispatch_key(payload, trigger)
    now = time.monotonic()
    remaining_seconds = llm_dispatch_cooldown_remaining_seconds(now)
    if remaining_seconds > 0:
        logger.info(
            "LLM dispatch skipped by cooldown. dispatchKey=%s remainingSeconds=%.3f",
            dispatch_key,
            remaining_seconds,
        )
        return False

    if not remember_dispatch_key(dispatch_key):
        return False

    _last_llm_dispatch_scheduled_at = now
    append_llm_dispatch_log(payload, trigger, sanitized_context, dispatch_key)
    logger.warning(
        "LLM dispatch scheduled. dispatchKey=%s runId=%s stepIndex=%s reason=%s issues=%s",
        dispatch_key,
        payload.get("runId"),
        payload.get("stepIndex"),
        trigger.get("reason"),
        summarize_triggered_issues(trigger),
    )
    asyncio.create_task(dispatch_llm_analysis(payload, trigger, sanitized_context, dispatch_key))
    return True


def llm_dispatch_cooldown_remaining_seconds(now: float | None = None) -> float:
    """다음 LLM 발송까지 남은 쿨다운 시간을 초 단위로 반환한다."""

    if _last_llm_dispatch_scheduled_at is None:
        return 0.0

    current_time = time.monotonic() if now is None else now
    elapsed_seconds = current_time - _last_llm_dispatch_scheduled_at
    return max(0.0, LLM_DISPATCH_COOLDOWN_SECONDS - elapsed_seconds)


async def dispatch_llm_analysis(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
    dispatch_key: str,
) -> dict[str, Any]:
    """SuperMario_LLM/LangChain 분석 endpoint로 위험 context를 POST한다."""

    request_payload = build_langchain_request_payload(snapshot, trigger, context)

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
) -> dict[str, str]:
    """LangChain 서버가 요구하는 `{id, swmm_raw_data}` payload를 만든다."""

    return {
        "id": extract_situation_id(snapshot, trigger, context),
        "swmm_raw_data": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
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
) -> None:
    """LLM 전송 후보 trigger마다 로컬 JSONL 기록을 남긴다."""

    try:
        LLM_DISPATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "loggedAt": datetime.now().isoformat(timespec="milliseconds"),
            "dispatchKey": dispatch_key,
            "status": "scheduled",
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
