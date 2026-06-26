import uuid


def build_embedding_text(event, action) -> str:
    metrics = event.metrics_snapshot or {}
    lines = [
        "[도시침수 위험 대응 사례]",
        "",
        f"이벤트 ID: {event.id}",
        f"실행 ID: {event.run_id}",
        f"시뮬레이션 Step: {event.step_index}",
        f"대상 ID: {event.target_id}",
        f"대상 유형: {event.source}",
        f"위험 등급: {event.hazard_level}",
        f"위험 유형: {event.hazard_type}",
        "",
        "위험 상황:",
        event.hazard_detail,
        "",
        "당시 주요 지표:",
    ]
    for key in ("flowCms", "velocityMps", "depthM", "fullness", "capacityRatio", "direction", "blockageRatio", "floodingCms", "depthRatio"):
        if key in metrics:
            lines.append(f"- {key}: {metrics.get(key)}")
    lines.extend([
        "",
        "관리자 조치:",
        action.action_detail,
        "",
        "조치 유형:",
        action.action_type,
        "",
        "조치 결과:",
        action.result_status,
    ])
    return "\n".join(lines).strip()


def save_hazard_case_to_vector_db(text: str, metadata: dict) -> str:
    return f"hazard-case-{uuid.uuid4()}"
