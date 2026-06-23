"""Future SuperMario_LLM dispatch hook for SWMM risk snapshots.

This module intentionally does not call the LLM server yet. It marks the exact
place where the Django service should hand off a triggered SWMM context packet
to SuperMario_LLM once that API contract is ready.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any


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
    """Schedule a future LLM analysis call when a snapshot asks for it.

    현재 구현은 실제 HTTP 호출을 하지 않는다. 다만 WebSocket broadcast 직전에
    이 함수를 호출하면, 나중에 `dispatch_llm_analysis()` 안에 SuperMario_LLM
    API 호출만 채워 넣으면 된다.

    반환:
    - True: 이번 snapshot은 LLM dispatch 대상이라 background task로 예약됨.
    - False: trigger가 없거나, 같은 trigger가 이미 예약되어 건너뜀.
    """

    trigger = payload.get("llmTrigger")
    if not isinstance(trigger, Mapping) or not trigger.get("shouldTrigger"):
        return False

    context = trigger.get("context")
    if not isinstance(context, Mapping):
        logger.warning("LLM trigger was set but context payload is missing.")
        return False
    sanitized_context = sanitize_llm_context(context)

    dispatch_key = build_llm_dispatch_key(payload, trigger)
    if not remember_dispatch_key(dispatch_key):
        return False

    append_llm_dispatch_log(payload, trigger, sanitized_context, dispatch_key)
    logger.warning(
        "LLM would-dispatch. dispatchKey=%s runId=%s stepIndex=%s reason=%s issues=%s",
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
    """Future SuperMario_LLM API call location.

    실제 연결 시 이 함수 안에서 아래 순서로 처리하면 된다.
    1. `context`를 SuperMario_LLM `/analyze` 같은 endpoint로 POST한다.
    2. 실패하면 logger/DB/job retry 정책으로 남긴다.

    예시:

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                settings.SUPERMARIO_LLM_ANALYZE_URL,
                json={"context": context},
            )
            response.raise_for_status()
    """

    logger.debug(
        "LLM dispatch placeholder. dispatchKey=%s runId=%s stepIndex=%s reason=%s contextKeys=%s",
        dispatch_key,
        snapshot.get("runId"),
        snapshot.get("stepIndex"),
        trigger.get("reason"),
        sorted(context.keys()),
    )
    return {
        "ok": True,
        "status": "placeholder_not_sent",
        "dispatchKey": dispatch_key,
        "targetService": "SuperMario_LLM",
    }


def sanitize_llm_context(value: Any) -> Any:
    """Return a copy of LLM context without local paths or debug export metadata."""

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
    """Write one local JSONL record for every would-call trigger."""

    try:
        LLM_DISPATCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "loggedAt": datetime.now().isoformat(timespec="milliseconds"),
            "dispatchKey": dispatch_key,
            "status": "placeholder_not_sent",
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
        logger.warning("Failed to write LLM dispatch placeholder log: %s", exc)


def summarize_triggered_issues(trigger: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return compact issue details for console/file logs."""

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
    """Build an idempotency key for one logical LLM trigger."""

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
    """Remember a dispatch key and return False when it was already seen."""

    if dispatch_key in _scheduled_dispatch_keys:
        return False

    _scheduled_dispatch_keys.add(dispatch_key)
    _scheduled_dispatch_key_order.append(dispatch_key)
    while len(_scheduled_dispatch_key_order) > MAX_REMEMBERED_DISPATCH_KEYS:
        old_key = _scheduled_dispatch_key_order.popleft()
        _scheduled_dispatch_keys.discard(old_key)
    return True
