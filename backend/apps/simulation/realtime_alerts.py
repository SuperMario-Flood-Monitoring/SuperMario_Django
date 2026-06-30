from __future__ import annotations

from collections.abc import Mapping
from typing import Any

RUNTIME_ALERT_TITLE = "지속적인 이상 현상 감지"
RUNTIME_ALERT_MESSAGE_BY_SOURCE = {
    "forecast": "10분 예측 기준 지속적인 이상 현상이 감지되었습니다. 관로/시설 상태를 확인해주세요.",
    "llm_request": "지속적인 이상 현상이 감지되어 LLM 분석 요청을 전송했습니다. 관로/시설 상태를 확인해주세요.",
    "runtime": "지속적인 이상 현상이 감지되었습니다. 관로/시설 상태를 확인해주세요.",
}
RUNTIME_ALERT_EVENT_LABELS = {
    "BLOCKAGE_CLOSED": "관로 막힘",
    "REVERSE_FLOW": "역류",
    "PREDICTED_BLOCKAGE_CLOSED": "관로 막힘 예측",
    "PREDICTED_CAPACITY_EXCEEDED": "통수능 초과 예측",
    "PREDICTED_FLOODING": "침수 예측",
    "PREDICTED_FULL_PIPE": "만관 예측",
    "PREDICTED_NODE_DEPTH": "수위 상승 예측",
}
RUNTIME_ALERT_SEVERITY_RANK = {
    "NORMAL": 0,
    "WATCH": 1,
    "WARNING": 2,
    "CRITICAL": 3,
}


def is_triggered_llm_event(trigger: Any) -> bool:
    return isinstance(trigger, Mapping) and bool(trigger.get("shouldTrigger"))


def build_realtime_alert(trigger: Any, *, source: str) -> dict[str, Any] | None:
    """React WebSocket 소비자가 바로 표시할 수 있는 런타임 경고 payload를 만든다."""

    if not is_triggered_llm_event(trigger):
        return None

    assert isinstance(trigger, Mapping)
    issues = [dict(issue) for issue in trigger.get("triggeredIssues") or [] if isinstance(issue, Mapping)]
    severity = highest_alert_severity(issues)
    reason = str(trigger.get("reason") or "persistent_abnormal")
    message = RUNTIME_ALERT_MESSAGE_BY_SOURCE.get(source, RUNTIME_ALERT_MESSAGE_BY_SOURCE["runtime"])
    targets = ", ".join(alert_issue_label(issue) for issue in issues[:3])
    if targets:
        message = f"{message} 주요 대상: {targets}."

    return {
        "kind": "persistent_abnormal",
        "severity": severity,
        "title": RUNTIME_ALERT_TITLE,
        "message": message,
        "reason": reason,
        "source": source,
        "key": build_realtime_alert_key(reason, issues, source=source),
        "triggeredIssues": issues,
    }


def highest_alert_severity(issues: list[dict[str, Any]]) -> str:
    severity = "CRITICAL"
    for issue in issues:
        next_severity = str(issue.get("severity") or "CRITICAL")
        if RUNTIME_ALERT_SEVERITY_RANK.get(next_severity, 0) > RUNTIME_ALERT_SEVERITY_RANK.get(severity, 0):
            severity = next_severity
    return severity


def alert_issue_label(issue: Mapping[str, Any]) -> str:
    event_type = str(issue.get("eventType") or "")
    source_id = str(issue.get("sourceId") or issue.get("issueId") or "unknown")
    event_label = RUNTIME_ALERT_EVENT_LABELS.get(event_type, event_type or "이상현상")
    return f"{source_id} {event_label}"


def build_realtime_alert_key(reason: str, issues: list[dict[str, Any]], *, source: str) -> str:
    issue_keys = [
        str(issue.get("issueId") or issue.get("eventId") or f"{issue.get('eventType')}:{issue.get('sourceId')}")
        for issue in issues[:10]
    ]
    return "|".join([source, reason, *sorted(issue_keys)])
