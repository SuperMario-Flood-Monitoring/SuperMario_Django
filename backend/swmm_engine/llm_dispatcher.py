"""SWMM 위험 snapshot을 SuperMario_LLM으로 전달하는 hook."""

from __future__ import annotations

import asyncio
import json
import logging
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


def schedule_llm_analysis_dispatch(payload: Mapping[str, Any]) -> bool:
    """snapshot이 요청한 경우 LLM 분석 호출을 예약한다.

    반환:
    - True: 이번 snapshot은 LLM dispatch 대상이라 background task로 예약됨.
    - False: trigger가 없거나, 같은 trigger가 이미 예약되어 건너뜀.
    """

    trigger = payload.get("llmTrigger")
    if not isinstance(trigger, Mapping) or not trigger.get("shouldTrigger"):
        return False

    context = trigger.get("context")
    if not isinstance(context, Mapping):
        logger.warning("LLM trigger가 설정되었지만 context payload가 없습니다.")
        return False
    sanitized_context = sanitize_llm_context(context)

    dispatch_key = build_llm_dispatch_key(payload, trigger)
    if not remember_dispatch_key(dispatch_key):
        return False

    append_llm_dispatch_log(payload, trigger, sanitized_context, dispatch_key)
    logger.warning(
        "LLM dispatch 예약됨. dispatchKey=%s runId=%s stepIndex=%s reason=%s issues=%s",
        dispatch_key,
        payload.get("runId"),
        payload.get("stepIndex"),
        trigger.get("reason"),
        summarize_triggered_issues(trigger),
    )
    asyncio.create_task(dispatch_llm_analysis(payload, trigger, sanitized_context, dispatch_key))
    return True


async def dispatch_llm_analysis(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
    dispatch_key: str,
) -> dict[str, Any]:
    """trigger된 SWMM context를 LangChain 서버로 POST한다."""

    request_payload = build_langchain_request_payload(snapshot, trigger, context)
    try:
        result = await asyncio.to_thread(post_langchain_request, request_payload)
    except Exception as exc:  # pragma: no cover - dispatch failure must not stop simulation
        logger.warning(
            "LLM dispatch 실패. dispatchKey=%s runId=%s stepIndex=%s error=%s",
            dispatch_key,
            snapshot.get("runId"),
            snapshot.get("stepIndex"),
            exc,
        )
        append_llm_dispatch_result_log(
            snapshot,
            trigger,
            dispatch_key,
            status="send_failed",
            detail={"error": f"{exc.__class__.__name__}: {exc}"},
        )
        return {
            "ok": False,
            "status": "send_failed",
            "dispatchKey": dispatch_key,
            "targetUrl": settings.SUPERMARIO_LLM_ANALYZE_URL,
        }

    append_llm_dispatch_result_log(
        snapshot,
        trigger,
        dispatch_key,
        status="sent",
        detail=result,
    )
    return {
        "ok": True,
        "status": "sent",
        "dispatchKey": dispatch_key,
        "targetUrl": settings.SUPERMARIO_LLM_ANALYZE_URL,
        **result,
    }


def build_langchain_request_payload(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """LEVEL 10 LangChain 요청 형식을 만든다."""

    situation_id = extract_situation_id(snapshot, trigger, context)
    return {
        "id": situation_id,
        "swmm_raw_data": json.dumps(context, ensure_ascii=False, separators=(",", ":")),
    }


def extract_situation_id(
    snapshot: Mapping[str, Any],
    trigger: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    """React가 부여한 상황 id를 찾고, 없으면 위험 metadata로 fallback한다."""

    control = snapshot.get("control")
    if isinstance(control, Mapping):
        for key in ("id", "situationId", "scenarioId", "reason"):
            value = control.get(key)
            if value not in (None, ""):
                return str(value)

    simulation = context.get("simulation")
    if isinstance(simulation, Mapping):
        nested_control = simulation.get("control")
        if isinstance(nested_control, Mapping):
            for key in ("id", "situationId", "scenarioId", "reason"):
                value = nested_control.get(key)
                if value not in (None, ""):
                    return str(value)

    return str(trigger.get("reason") or context.get("highestSeverity") or "risk")


def post_langchain_request(request_payload: Mapping[str, Any]) -> dict[str, Any]:
    """runtime dependency 추가를 피하기 위해 stdlib urllib로 요청을 보낸다."""

    target_url = settings.SUPERMARIO_LLM_ANALYZE_URL
    body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        target_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return {
                "httpStatus": response.status,
                "responseBody": response_body[:2000],
            }
    except urllib.error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM 서버가 HTTP {exc.code}를 반환했습니다: {response_body[:500]}") from exc


def sanitize_llm_context(value: Any) -> Any:
    """local path와 debug export metadata를 제거한 LLM context copy를 반환한다."""

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
    """호출 대상 trigger마다 local JSONL record 하나를 기록한다."""

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
    """console/file log에 남길 간결한 issue detail을 반환한다."""

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
    """논리적 LLM trigger 하나에 대한 idempotency key를 만든다."""

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
