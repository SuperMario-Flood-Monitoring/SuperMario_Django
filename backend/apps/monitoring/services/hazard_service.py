from __future__ import annotations

from typing import Any, Mapping

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from ..models import HazardAction, HazardCaseEmbedding, HazardEvent
from .maintenance_dispatcher import dispatch_maintenance_log
from .vector_service import build_embedding_text, save_hazard_case_to_vector_db


CRITICAL_LEVEL = "CRITICAL"


def serialize_hazard_row(event: HazardEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "target_id": event.target_id,
        "pipe_id": event.target_id if event.source == "link" else None,
        "source": event.source,
        "hazard_level": event.hazard_level,
        "hazard_type": event.hazard_type,
        "hazard_detail": event.hazard_detail,
        "status": event.status,
        "created_at": event.created_at.isoformat(),
    }


def serialize_hazard_detail(event: HazardEvent) -> dict[str, Any]:
    data = serialize_hazard_row(event)
    data.update(
        {
            "run_id": event.run_id,
            "step_index": event.step_index,
            "model_time": event.model_time,
            "metrics_snapshot": event.metrics_snapshot,
            "actions": [
                {
                    "id": action.id,
                    "action_detail": action.action_detail,
                    "action_type": action.action_type,
                    "result_detail": action.result_detail,
                    "result_status": action.result_status,
                    "recurrence_note": action.recurrence_note,
                    "fastapi_sync_status": action.fastapi_sync_status,
                    "fastapi_vector_id": action.fastapi_vector_id,
                    "fastapi_error_message": action.fastapi_error_message,
                    "created_at": action.created_at.isoformat(),
                }
                for action in event.actions.all()
            ],
        }
    )
    return data


def list_hazard_events(status: str = HazardEvent.Status.OPEN, include_deleted: bool = False) -> list[HazardEvent]:
    queryset = HazardEvent.objects.all()
    if status:
        queryset = queryset.filter(status=status)
    if not include_deleted:
        queryset = queryset.filter(is_deleted=False)
    return list(queryset)


def create_hazard_events_from_swmm_tick(tick: Mapping[str, Any]) -> list[HazardEvent]:
    risk = tick.get("risk")
    if not isinstance(risk, Mapping):
        return []

    events = risk.get("events")
    if not isinstance(events, list):
        return []

    created_events: list[HazardEvent] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if str(event.get("severity") or "") != CRITICAL_LEVEL:
            continue

        target_id = str(event.get("sourceId") or "").strip()
        hazard_type = str(event.get("eventType") or "").strip()
        hazard_level = str(event.get("severity") or "").strip()
        if not target_id or not hazard_type or not hazard_level:
            continue

        source = str(event.get("source") or "").strip()
        event_key = build_event_key(
            run_id=str(tick.get("runId") or ""),
            hazard_type=hazard_type,
            target_id=target_id,
            hazard_level=hazard_level,
        )
        hazard_event, created = HazardEvent.objects.get_or_create(
            event_key=event_key,
            defaults={
                "run_id": str(tick.get("runId") or ""),
                "step_index": int(tick.get("stepIndex") or 0),
                "model_time": str(tick.get("modelTime") or ""),
                "source": source,
                "target_id": target_id,
                "hazard_level": hazard_level,
                "hazard_type": hazard_type,
                "hazard_detail": build_hazard_detail(event, tick),
                "metrics_snapshot": metrics_snapshot_for_event(event, tick),
            },
        )
        if created:
            created_events.append(hazard_event)

    return created_events


def build_event_key(*, run_id: str, hazard_type: str, target_id: str, hazard_level: str) -> str:
    return ":".join([run_id, hazard_type, target_id, hazard_level])


def metrics_snapshot_for_event(event: Mapping[str, Any], tick: Mapping[str, Any]) -> dict[str, Any]:
    source = str(event.get("source") or "")
    source_id = str(event.get("sourceId") or "")
    collection_name = "links" if source == "link" else "nodes" if source == "node" else ""
    collection = tick.get(collection_name) if collection_name else None
    if isinstance(collection, Mapping):
        metrics = collection.get(source_id)
        if isinstance(metrics, Mapping):
            return dict(metrics)
    raw_metrics = event.get("metrics")
    return dict(raw_metrics) if isinstance(raw_metrics, Mapping) else {}


def build_hazard_detail(event: Mapping[str, Any], tick: Mapping[str, Any]) -> str:
    hazard_type = str(event.get("eventType") or "UNKNOWN")
    source = str(event.get("source") or "object")
    source_id = str(event.get("sourceId") or "unknown")
    display_name = display_name_for_event(event, tick) or source_id
    metrics = metrics_snapshot_for_event(event, tick)

    if hazard_type == "REVERSE_FLOW":
        flow = metrics.get("flowCms")
        if flow is not None:
            return f"{display_name}({source_id})에서 역류가 감지되었습니다. 현재 flowCms={flow}입니다."
        return f"{display_name}({source_id})에서 역류가 감지되었습니다."
    if hazard_type in {"FLOODING", "NODE_FLOODING"}:
        flooding = metrics.get("floodingCms")
        if flooding is not None:
            return f"{display_name}({source_id})에서 월류가 감지되었습니다. 현재 floodingCms={flooding}입니다."
        return f"{display_name}({source_id})에서 월류가 감지되었습니다."
    if hazard_type == "BLOCKAGE":
        blockage = metrics.get("blockageRatio")
        if blockage is not None:
            return f"{display_name}({source_id})의 막힘 비율이 {float(blockage) * 100:.1f}%로 감지되었습니다."
        return f"{display_name}({source_id})에서 막힘 위험이 감지되었습니다."

    return f"{display_name}({source_id})에서 {hazard_type} 위험이 감지되었습니다. 대상 유형은 {source}입니다."


def display_name_for_event(event: Mapping[str, Any], tick: Mapping[str, Any]) -> str | None:
    source = str(event.get("source") or "")
    source_id = str(event.get("sourceId") or "")
    collection_name = "links" if source == "link" else "nodes" if source == "node" else ""
    collection = tick.get(collection_name) if collection_name else None
    if isinstance(collection, Mapping):
        data = collection.get(source_id)
        if isinstance(data, Mapping):
            return str(data.get("sourceEditorName") or data.get("id") or source_id)
    return None


def start_hazard_action(event_id: int, payload: Mapping[str, Any]) -> HazardAction:
    with transaction.atomic():
        event = get_object_or_404(HazardEvent.objects.select_for_update(), id=event_id)
        action_detail = str(payload.get("action_detail") or "").strip()
        if not action_detail:
            raise ValueError("action_detail은 필수입니다.")

        action = HazardAction.objects.create(
            event=event,
            action_detail=action_detail,
            action_type=str(payload.get("action_type") or "").strip(),
        )
        event.status = HazardEvent.Status.IN_PROGRESS
        event.save(update_fields=["status", "updated_at"])

    action.refresh_from_db()
    return action


def complete_hazard_action(event_id: int, action_id: int, payload: Mapping[str, Any]) -> HazardAction:
    with transaction.atomic():
        event = get_object_or_404(HazardEvent.objects.select_for_update(), id=event_id)
        action = get_object_or_404(HazardAction.objects.select_for_update(), id=action_id, event=event)

        result_detail = str(payload.get("result_detail") or "").strip()
        if not result_detail:
            raise ValueError("result_detail은 필수입니다.")

        action.result_detail = result_detail
        action.recurrence_note = str(payload.get("recurrence_note") or "").strip()
        action.result_status = str(payload.get("result_status") or HazardEvent.Status.RESOLVED).strip()
        if "action_detail" in payload:
            action_detail = str(payload.get("action_detail") or "").strip()
            if not action_detail:
                raise ValueError("action_detail은 비워둘 수 없습니다.")
            action.action_detail = action_detail
        if "action_type" in payload:
            action.action_type = str(payload.get("action_type") or "").strip()
        action.save(
            update_fields=[
                "action_detail",
                "action_type",
                "result_detail",
                "result_status",
                "recurrence_note",
            ]
        )

        event.status = HazardEvent.Status.RESOLVED
        event.is_deleted = True
        event.resolved_at = timezone.now()
        event.save(update_fields=["status", "is_deleted", "resolved_at", "updated_at"])

        embedding_text = build_embedding_text(event, action)
        metadata = {
            "event_id": event.id,
            "target_id": event.target_id,
            "source": event.source,
            "hazard_level": event.hazard_level,
            "hazard_type": event.hazard_type,
            "run_id": event.run_id,
        }
        vector_id = save_hazard_case_to_vector_db(embedding_text, metadata)
        HazardCaseEmbedding.objects.create(
            event=event,
            embedding_text=embedding_text,
            vector_id=vector_id,
            metadata=metadata,
        )

    dispatch_maintenance_log(action)
    action.refresh_from_db()
    return action
