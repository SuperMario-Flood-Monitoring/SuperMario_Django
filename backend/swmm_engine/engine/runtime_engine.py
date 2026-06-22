"""Django 서버에서 직접 사용할 SWMM runtime engine.

기존 임시 FastAPI 서버에서 쓰던 PySWMM 세션 제어 로직을 Django 패키지
내부로 분리한 파일이다. 이 모듈은 HTTP route, FastAPI app, WebSocket 객체를
전혀 만들지 않는다. Django view, Channels consumer, Celery worker는
``SwmmRuntimeEngine`` 인스턴스를 만들고 공개 async 메서드를 호출하면 된다.
"""

from __future__ import annotations

import asyncio
import json
import math
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from swmm_engine.converter import (
    DEFAULT_CATCHMENT_AREA_M2,
    DEFAULT_DRY_WEATHER_FLOW_CMS,
    DEFAULT_HORIZONTAL_SLOPE,
    DEFAULT_RUNOFF_COEFFICIENT,
    ConversionError,
    convert_layout,
    render_conversion_report,
    render_inp,
    render_mapping_json,
)
from swmm_engine.risk import build_swmm_context_packet, evaluate_swmm_risk

from .bridge import (
    CONTROL_LINK_TYPES,
    MIN_BLOCKED_FLOW_CMS,
    build_runtime_control_model,
    display_velocity_mps,
    full_flow_area_sqm,
    full_flow_capacity_cms,
    import_pyswmm,
    safe_attr,
    safe_number,
)


PACKAGE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_STEP_SECONDS = 1
DEFAULT_MAX_RAINFALL_MM_PER_HOUR = 100.0
DEFAULT_SPEED_MULTIPLIER = 1.0
MAX_SPEED_MULTIPLIER = 10.0
MAX_RAINFALL_RATIO = 1000.0
RUNTIME_TICK_LOG_DIR = PACKAGE_DIR / "logs" / "runtime-tick-logs"
RISK_ALERT_SEVERITIES = {"WARNING", "CRITICAL"}
RISK_CONTEXT_LEVEL = "optimal"
RISK_RESOLUTION_GRACE_TICKS = 3
RISK_SEVERITY_RANK = {
    "NORMAL": 0,
    "WATCH": 1,
    "WARNING": 2,
    "CRITICAL": 3,
}


class SwmmRuntimeError(RuntimeError):
    """Django view/consumer가 HTTP 오류로 바꿀 수 있는 runtime 예외."""

    def __init__(self, message: str, *, status_code: int = 500, detail: Any | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail if detail is not None else message


@dataclass
class RuntimeModelSpec:
    """SWMM runtime이 실행할 임시/저장 모델과 React 매핑 정보."""

    model_path: Path
    mapping: dict[str, Any]
    report: dict[str, Any]
    source: str
    temp_dir: tempfile.TemporaryDirectory[str] | None = None

    def cleanup(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()
            self.temp_dir = None


def clamp_ratio(value: Any) -> float:
    """0~1 막힘 비율로 정규화한다. 100 같은 퍼센트 입력도 허용한다."""

    parsed = safe_number(value, 0.0)
    if parsed > 1.0:
        parsed = parsed / 100.0
    return max(0.0, min(1.0, parsed))


def clamp_rainfall_ratio(value: Any) -> float:
    """강수량 비율을 0~MAX_RAINFALL_RATIO 범위로 정규화한다."""

    parsed = safe_number(value, 0.0)
    if parsed > 1.0:
        parsed = parsed / 100.0
    return max(0.0, min(MAX_RAINFALL_RATIO, parsed))


def clamp_speed_multiplier(value: Any) -> float:
    """시뮬레이션 표시/진행 배속을 허용 범위로 제한한다."""

    return max(1.0, min(MAX_SPEED_MULTIPLIER, safe_number(value, DEFAULT_SPEED_MULTIPLIER)))


def build_editor_conversion_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """React editor layout payload를 INP/report/mapping 묶음으로 변환한다."""

    layout = payload.get("layout", payload)
    if not isinstance(layout, dict):
        raise ConversionError("Request body must contain an EditorLayout object or { layout }.")
    scale_m_per_px = float(payload.get("scaleMPerPx", 0.5) or 0.5)
    map_height = float(payload.get("mapHeight", 2000.0) or 2000.0)
    title = str(payload.get("title") or "SWMM model generated from React editor layout")
    result = convert_layout(layout, scale_m_per_px=scale_m_per_px, map_height=map_height)
    inp_text = render_inp(result, title=title)
    report = render_conversion_report(result, inp_text=inp_text)
    mapping = render_mapping_json(result)
    return {
        "ok": len(result.errors) == 0,
        "inpText": inp_text,
        "report": report,
        "mapping": mapping,
        "warnings": result.warnings,
        "errors": result.errors,
    }


def build_runtime_model_spec(payload: dict[str, Any]) -> RuntimeModelSpec:
    """엔진 시작 payload에서 실행할 SWMM 모델 spec을 만든다.

    새 분리 구조에서는 React editor layout JSON을 source of truth로 삼는다.
    기존 HTML contract 기반 fallback은 새 repo-ready 실행 경로에서 제외했다.
    """

    layout = payload.get("layout")
    if not isinstance(layout, dict):
        raise SwmmRuntimeError(
            "Runtime start payload must include a React editor layout.",
            status_code=422,
            detail={"ok": False, "error": "layout_required"},
        )

    conversion = build_editor_conversion_payload(payload)
    if not conversion["ok"]:
        raise SwmmRuntimeError(
            "React editor layout has SWMM conversion errors.",
            status_code=422,
            detail={
                "ok": False,
                "error": "editor_layout_conversion_has_errors",
                "report": conversion["report"],
                "mapping": conversion["mapping"],
            },
        )

    temp_dir = tempfile.TemporaryDirectory(prefix="swmm-django-runtime-")
    model_path = Path(temp_dir.name) / "react_editor_runtime.inp"
    model_path.write_text(conversion["inpText"], encoding="utf-8")
    return RuntimeModelSpec(
        model_path=model_path,
        mapping=conversion["mapping"],
        report=conversion["report"],
        source="react-editor-json",
        temp_dir=temp_dir,
    )


def rainfall_cms_for_percent(
    ratio: float,
    *,
    max_rainfall_mm_per_hour: float,
    catchment_area_m2: float = DEFAULT_CATCHMENT_AREA_M2,
    runoff_coefficient: float = DEFAULT_RUNOFF_COEFFICIENT,
) -> float:
    """강수량 비율을 노드별 직접 유입량(cms)으로 변환한다."""

    rainfall_mm_per_hour = max(0.0, max_rainfall_mm_per_hour) * ratio
    return rainfall_mm_per_hour / 1000.0 / 3600.0 * catchment_area_m2 * runoff_coefficient


def link_capacity_from_mapping(link_id: str, mapping: dict[str, Any]) -> float:
    """React->SWMM mapping 정보로 링크 만관 용량을 계산한다."""

    link_meta = (mapping.get("swmmLinks") or {}).get(link_id) or {}
    if str(link_meta.get("kind") or "").upper() != "CONDUIT":
        return 0.0
    diameter = safe_number(link_meta.get("diameter"), 0.0)
    roughness = safe_number(link_meta.get("roughness"), 0.013)
    length = max(safe_number(link_meta.get("length"), 0.0), 0.001)
    from_node = (mapping.get("swmmNodes") or {}).get(link_meta.get("fromNode")) or {}
    to_node = (mapping.get("swmmNodes") or {}).get(link_meta.get("toNode")) or {}
    from_elev = safe_number(from_node.get("invertElevation"), 0.0)
    to_elev = safe_number(to_node.get("invertElevation"), from_elev - DEFAULT_HORIZONTAL_SLOPE * length)
    slope = max(abs(from_elev - to_elev) / length, DEFAULT_HORIZONTAL_SLOPE)
    if diameter <= 0 or roughness <= 0:
        return 0.0
    area = math.pi * diameter * diameter / 4.0
    hydraulic_radius = diameter / 4.0
    return (1.0 / roughness) * area * (hydraulic_radius ** (2.0 / 3.0)) * math.sqrt(slope)


def risk_issue_id(event: dict[str, Any]) -> str:
    """LLM 호출 중복 방지용 안정적인 이슈 ID를 만든다.

    감지 eventType은 수위 상승 단계에 따라 바뀔 수 있으므로, 같은 시설의
    같은 계열 이상상황은 하나의 이슈로 묶는다.
    """

    event_type = str(event.get("eventType") or "UNKNOWN")
    source = str(event.get("source") or "unknown")
    source_id = str(event.get("sourceId") or "unknown")
    if "BLOCKAGE" in event_type:
        family = "BLOCKAGE"
    elif "REVERSE" in event_type:
        family = "REVERSE_FLOW"
    elif "FLOODING" in event_type:
        family = "FLOODING"
    elif "CAPACITY" in event_type:
        family = "CAPACITY"
    elif (
        "SURCHARGE" in event_type
        or "HIGH_FILL" in event_type
        or "ELEVATED_FILL" in event_type
        or "HIGH_WATER" in event_type
        or "RISING_WATER" in event_type
    ):
        family = "FILL_LEVEL"
    else:
        family = event_type
    return f"{family}:{source}:{source_id}"


def risk_event_rank(event: dict[str, Any]) -> int:
    return RISK_SEVERITY_RANK.get(str(event.get("severity") or "NORMAL"), 0)


class RealtimeSwmmSession:
    """하나의 SWMM 모델 실행 세션.

    이 객체는 PySWMM Simulation을 열고, 1초 tick마다 외부 유입/막힘 제어를
    적용한 뒤 snapshot JSON을 만든다.
    """

    def __init__(
        self,
        spec: RuntimeModelSpec,
        *,
        step_seconds: int,
        max_rainfall_mm_per_hour: float,
    ) -> None:
        self.spec = spec
        self.step_seconds = max(1, int(step_seconds))
        self.max_rainfall_mm_per_hour = max_rainfall_mm_per_hour
        self.speed_multiplier = DEFAULT_SPEED_MULTIPLIER
        self.step_index = 0
        self.rainfall_ratio = 0.0
        self.blockages_by_id: dict[str, float] = {}
        self.last_snapshot: dict[str, Any] | None = None
        self.last_log_error: str | None = None
        self.previous_risk_result: dict[str, Any] | None = None
        self.active_risk_issues: dict[str, dict[str, Any]] = {}
        self.risk_clear_counts: dict[str, int] = {}
        self.run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}"
        RUNTIME_TICK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.tick_log_path = RUNTIME_TICK_LOG_DIR / f"swmm-runtime-{self.run_id}.jsonl"
        self.tick_log_file = self.tick_log_path.open("a", encoding="utf-8", buffering=1)

        self.mapping = spec.mapping
        self.report = spec.report
        self.swmm_nodes = self.mapping.get("swmmNodes") or {}
        self.swmm_links = self.mapping.get("swmmLinks") or {}
        self.node_connected_links = self.build_node_connected_links()
        self.control_link_ids = self.build_control_link_ids()
        dynamic_controls = self.report.get("dynamicControls") or {}
        rainfall_targets = [str(node_id) for node_id in dynamic_controls.get("rainfallTargets") or []]
        raw_rainfall_weights = dynamic_controls.get("rainfallTargetWeights") or {}
        self.rainfall_target_weights = {
            str(node_id): max(0.0, safe_number(weight, 1.0))
            for node_id, weight in raw_rainfall_weights.items()
        } if isinstance(raw_rainfall_weights, dict) else {}
        self.rainfall_targets = sorted(set(rainfall_targets) | set(self.rainfall_target_weights.keys()))
        self.dry_weather_targets = [
            str(node_id)
            for node_id in dynamic_controls.get("dryWeatherTargets") or []
        ]

        self.runtime_model_path = build_runtime_control_model(spec.model_path, disable_dry_weather_inflows=True)
        Simulation, Nodes, Links = import_pyswmm()
        self.sim = Simulation(str(self.runtime_model_path))
        self.sim.__enter__()
        self.sim.step_advance(self.step_seconds)
        self.nodes = Nodes(self.sim)
        self.links = Links(self.sim)
        self._iterator = iter(self.sim)

    def build_node_connected_links(self) -> dict[str, set[str]]:
        connected: dict[str, set[str]] = {}
        for link_id, meta in self.swmm_links.items():
            from_node = str(meta.get("fromNode") or "")
            to_node = str(meta.get("toNode") or "")
            if from_node:
                connected.setdefault(from_node, set()).add(link_id)
            if to_node:
                connected.setdefault(to_node, set()).add(link_id)
        return connected

    def build_control_link_ids(self) -> set[str]:
        targets = {
            str(target.get("swmmLinkId"))
            for target in (self.report.get("dynamicControls") or {}).get("blockageTargets") or []
            if target.get("swmmLinkId")
        }
        if targets:
            return targets
        return {
            link_id
            for link_id, meta in self.swmm_links.items()
            if str(meta.get("kind") or "").upper() in {"CONDUIT", "ORIFICE", "WEIR", "PUMP"}
        }

    def get_node(self, node_id: str) -> Any | None:
        try:
            return self.nodes[node_id]
        except Exception:
            return None

    def get_link(self, link_id: str) -> Any | None:
        try:
            return self.links[link_id]
        except Exception:
            return None

    def close(self) -> None:
        try:
            self.sim.__exit__(None, None, None)
        finally:
            try:
                self.tick_log_file.close()
            finally:
                self.spec.cleanup()

    def append_tick_log(self, payload: dict[str, Any]) -> None:
        try:
            record = {
                "loggedAt": datetime.now().isoformat(timespec="milliseconds"),
                **payload,
            }
            self.tick_log_file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.tick_log_file.flush()
        except Exception as exc:  # pragma: no cover - logging must not stop simulation
            self.last_log_error = f"{exc.__class__.__name__}: {exc}"

    def append_runtime_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        self.append_tick_log({
            "type": event_type,
            "ok": event_type != "error",
            "runId": self.run_id,
            "tickLogPath": str(self.tick_log_path),
            "sourceOfTruth": "SWMM",
            "source": self.spec.source,
            "modelPath": str(self.spec.model_path),
            "runtimeModelPath": str(self.runtime_model_path),
            "modelTime": self.model_time_iso(),
            "stepSeconds": self.step_seconds,
            "stepIndex": self.step_index,
            "control": self.control_state(),
            **(payload or {}),
        })

    def update_controls(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "rainfallRatio" in payload or "rainfall" in payload:
            self.rainfall_ratio = clamp_rainfall_ratio(payload.get("rainfallRatio", payload.get("rainfall", self.rainfall_ratio)))

        if "speedMultiplier" in payload:
            self.speed_multiplier = clamp_speed_multiplier(payload.get("speedMultiplier"))

        next_blockages: dict[str, float] = {}
        raw_blockages = payload.get("blockagesById") or {}
        if isinstance(raw_blockages, dict):
            for raw_id, raw_value in raw_blockages.items():
                blockage = clamp_ratio(raw_value.get("blockage", raw_value.get("blockageRatio", 0.0)) if isinstance(raw_value, dict) else raw_value)
                next_blockages[str(raw_id)] = blockage
        for exception in payload.get("exceptions", []) if isinstance(payload.get("exceptions"), list) else []:
            blockage = clamp_ratio(exception.get("blockage", 0.0))
            for link_id in exception.get("swmmLinks", []):
                next_blockages[str(link_id)] = blockage

        if "blockagesById" in payload or "exceptions" in payload:
            self.blockages_by_id = next_blockages

        return self.control_state()

    def control_state(self) -> dict[str, Any]:
        return {
            "rainfallRatio": self.rainfall_ratio,
            "rainfallPercent": round(self.rainfall_ratio * 100.0, 2),
            "blockagesById": self.blockages_by_id,
            "maxRainfallMmPerHour": self.max_rainfall_mm_per_hour,
            "speedMultiplier": self.speed_multiplier,
        }

    def apply_external_inflows(self) -> None:
        inflow_cms = rainfall_cms_for_percent(
            self.rainfall_ratio,
            max_rainfall_mm_per_hour=self.max_rainfall_mm_per_hour,
        )
        inflows_by_node: dict[str, float] = {}
        for node_id in self.rainfall_targets:
            weight = self.rainfall_target_weights.get(node_id, 1.0)
            inflows_by_node[node_id] = inflows_by_node.get(node_id, 0.0) + inflow_cms * weight
        if self.rainfall_ratio > 0:
            for node_id in self.dry_weather_targets:
                inflows_by_node[node_id] = inflows_by_node.get(node_id, 0.0) + DEFAULT_DRY_WEATHER_FLOW_CMS
        controlled_nodes = set(self.rainfall_targets) | set(self.dry_weather_targets)
        for node_id in controlled_nodes:
            inflows_by_node.setdefault(node_id, 0.0)
        for node_id, node_inflow_cms in inflows_by_node.items():
            node = self.get_node(node_id)
            if node is not None:
                node.generated_inflow(node_inflow_cms)

    def blockage_for_link(self, link_id: str) -> float:
        blockage = self.blockages_by_id.get(link_id, 0.0)
        meta = self.swmm_links.get(link_id) or {}
        for node_id in (str(meta.get("fromNode") or ""), str(meta.get("toNode") or "")):
            blockage = max(blockage, self.blockages_by_id.get(node_id, 0.0))
        return max(0.0, min(1.0, blockage))

    def apply_blockage_to_link(self, link_id: str, blockage_ratio: float) -> None:
        link = self.get_link(link_id)
        if link is None:
            return
        meta = self.swmm_links.get(link_id) or {}
        link_type = str(meta.get("kind") or "").upper()
        open_ratio = max(0.0, min(1.0, 1.0 - blockage_ratio))
        is_fully_blocked = open_ratio <= 0.000001

        try:
            link.target_setting = 0.0 if is_fully_blocked else open_ratio
        except Exception:
            pass

        try:
            link.current_setting = 0.0 if is_fully_blocked else open_ratio
        except Exception:
            pass

        try:
            if link_type in CONTROL_LINK_TYPES:
                link.target_setting = 0.0 if is_fully_blocked else open_ratio
                return
        except Exception:
            pass

        capacity = link_capacity_from_mapping(link_id, self.mapping)
        if capacity <= 0:
            return
        try:
            if is_fully_blocked:
                link.flow_limit = 0.0
            elif open_ratio >= 0.999:
                link.flow_limit = 0.0
            else:
                link.flow_limit = max(capacity * open_ratio, MIN_BLOCKED_FLOW_CMS)
        except Exception:
            return

    def apply_blockages(self) -> None:
        for link_id in self.control_link_ids:
            self.apply_blockage_to_link(link_id, self.blockage_for_link(link_id))

    def apply_controls(self) -> None:
        self.apply_external_inflows()
        self.apply_blockages()

    def collect_node_states(self) -> dict[str, Any]:
        states: dict[str, Any] = {}
        for node_id, meta in self.swmm_nodes.items():
            node = self.get_node(node_id)
            if node is None:
                continue
            max_depth = max(safe_number(meta.get("maxDepth"), 1.0), 0.001)
            depth = safe_attr(node, "depth")
            states[node_id] = {
                "depthM": depth,
                "headM": safe_attr(node, "head"),
                "invertElevationM": safe_number(meta.get("invertElevation"), 0.0),
                "depthRatio": max(0.0, min(2.0, depth / max_depth)),
                "totalInflowCms": safe_attr(node, "total_inflow"),
                "floodingCms": safe_attr(node, "flooding"),
            }
        return states

    def collect_link_states(self) -> dict[str, Any]:
        states: dict[str, Any] = {}
        for link_id, meta in self.swmm_links.items():
            link = self.get_link(link_id)
            if link is None:
                continue
            flow = safe_attr(link, "flow")
            raw_velocity = abs(safe_attr(link, "velocity"))
            depth = safe_attr(link, "depth")
            full_depth = max(safe_number(meta.get("diameter"), 0.0), 0.001)
            capacity = link_capacity_from_mapping(link_id, self.mapping)
            if capacity <= 0:
                capacity = full_flow_capacity_cms({
                    "linkType": meta.get("kind"),
                    "lengthM": meta.get("length"),
                    "roughnessN": meta.get("roughness"),
                    "computedSlope": DEFAULT_HORIZONTAL_SLOPE,
                    "crossSection": {
                        "shape": "CIRCULAR",
                        "geom1": meta.get("diameter"),
                        "geom2": 0,
                        "barrels": 1,
                    },
                })
            velocity_meta = {
                "linkType": meta.get("kind"),
                "crossSection": {
                    "shape": "CIRCULAR",
                    "geom1": meta.get("diameter"),
                    "geom2": 0,
                    "barrels": 1,
                },
            }
            velocity = display_velocity_mps(velocity_meta, flow, raw_velocity)
            if velocity <= 0 and abs(flow) > 0:
                area = full_flow_area_sqm(velocity_meta)
                velocity = abs(flow) / area if area > 0 else 0.0
            states[link_id] = {
                "kind": meta.get("kind"),
                "flowCms": flow,
                "velocityMps": velocity,
                "depthM": depth,
                "fullness": max(0.0, min(2.0, depth / full_depth)),
                "capacityCms": capacity,
                "capacityRatio": abs(flow) / capacity if capacity > 0 else 0.0,
                "direction": "reverse" if flow < -0.0005 else "forward",
                "targetSetting": safe_attr(link, "target_setting", 1.0),
                "currentSetting": safe_attr(link, "current_setting", 1.0),
                "blockageRatio": self.blockage_for_link(link_id),
            }
        return states

    def aggregate_editor_states(self, node_states: dict[str, Any], link_states: dict[str, Any]) -> dict[str, Any]:
        editor_states: dict[str, Any] = {}
        for editor_id, refs in (self.mapping.get("editorNodes") or {}).items():
            linked_node_ids = [node_id for node_id in refs.get("swmmNodes", []) if node_id in node_states]
            linked_link_ids = set(link_id for link_id in refs.get("swmmLinks", []) if link_id in link_states)
            is_manhole_editor_node = any(
                (self.swmm_nodes.get(node_id) or {}).get("sourceEditorType") == "manhole"
                for node_id in linked_node_ids
            )
            is_storage_facility_editor_node = any(
                (self.swmm_nodes.get(node_id) or {}).get("sourceEditorType") in {"catchBasin", "facility"}
                for node_id in linked_node_ids
            )
            if is_manhole_editor_node or is_storage_facility_editor_node:
                for node_id in linked_node_ids:
                    linked_link_ids.update(self.node_connected_links.get(node_id, set()))
            linked_node_states = [node_states[node_id] for node_id in linked_node_ids]
            linked_link_states = [link_states[link_id] for link_id in linked_link_ids if link_id in link_states]
            if not linked_node_states and not linked_link_states:
                continue
            editor_states[editor_id] = {
                "maxDepthRatio": max((state.get("depthRatio", 0.0) for state in linked_node_states), default=0.0),
                "maxFullness": max((state.get("fullness", 0.0) for state in linked_link_states), default=0.0),
                "maxCapacityRatio": max((state.get("capacityRatio", 0.0) for state in linked_link_states), default=0.0),
                "maxBlockageRatio": max((state.get("blockageRatio", 0.0) for state in linked_link_states), default=0.0),
                "maxFloodingCms": max((state.get("floodingCms", 0.0) for state in linked_node_states), default=0.0),
                "flowCms": max((state.get("flowCms", 0.0) for state in linked_link_states), key=abs, default=0.0),
                "maxVelocityMps": max((state.get("velocityMps", 0.0) for state in linked_link_states), key=abs, default=0.0),
                "totalInflowCms": max((state.get("totalInflowCms", 0.0) for state in linked_node_states), default=0.0),
            }
        for editor_id, refs in (self.mapping.get("editorLinks") or {}).items():
            linked_link_states = [link_states[link_id] for link_id in refs.get("swmmLinks", []) if link_id in link_states]
            if not linked_link_states:
                continue
            editor_states[editor_id] = {
                "maxFullness": max((state.get("fullness", 0.0) for state in linked_link_states), default=0.0),
                "maxCapacityRatio": max((state.get("capacityRatio", 0.0) for state in linked_link_states), default=0.0),
                "maxBlockageRatio": max((state.get("blockageRatio", 0.0) for state in linked_link_states), default=0.0),
                "flowCms": max((state.get("flowCms", 0.0) for state in linked_link_states), key=abs, default=0.0),
                "maxVelocityMps": max((state.get("velocityMps", 0.0) for state in linked_link_states), key=abs, default=0.0),
            }
        return editor_states

    def update_risk_issue_lifecycle(self, risk_result: dict[str, Any]) -> dict[str, Any]:
        """LLM 호출 후보를 새 이슈/심각도 상승에만 열어준다."""

        raw_events = [event for event in risk_result.get("events", []) if isinstance(event, dict)]
        alert_events = [
            event
            for event in raw_events
            if str(event.get("severity") or "NORMAL") in RISK_ALERT_SEVERITIES
        ]
        current_events_by_issue: dict[str, dict[str, Any]] = {}
        for event in alert_events:
            issue_id = risk_issue_id(event)
            previous = current_events_by_issue.get(issue_id)
            if previous is None or risk_event_rank(event) > risk_event_rank(previous):
                current_events_by_issue[issue_id] = event

        new_issues: list[dict[str, Any]] = []
        escalated_issues: list[dict[str, Any]] = []
        resolved_issues: list[dict[str, Any]] = []

        for issue_id, event in current_events_by_issue.items():
            severity = str(event.get("severity") or "NORMAL")
            existing = self.active_risk_issues.get(issue_id)
            if existing is None:
                issue = self._risk_issue_record(issue_id, event)
                issue["firstSeenStepIndex"] = self.step_index
                issue["lastTriggeredStepIndex"] = self.step_index
                self.active_risk_issues[issue_id] = issue
                new_issues.append(dict(issue))
            else:
                previous_severity = str(existing.get("severity") or "NORMAL")
                existing.update(self._risk_issue_record(issue_id, event))
                if RISK_SEVERITY_RANK.get(severity, 0) > RISK_SEVERITY_RANK.get(previous_severity, 0):
                    existing["previousSeverity"] = previous_severity
                    existing["lastTriggeredStepIndex"] = self.step_index
                    escalated_issues.append(dict(existing))
                else:
                    existing.pop("previousSeverity", None)
            self.risk_clear_counts.pop(issue_id, None)

        for issue_id in list(self.active_risk_issues):
            if issue_id in current_events_by_issue:
                continue
            clear_count = self.risk_clear_counts.get(issue_id, 0) + 1
            if clear_count >= RISK_RESOLUTION_GRACE_TICKS:
                issue = self.active_risk_issues.pop(issue_id)
                issue["resolvedStepIndex"] = self.step_index
                resolved_issues.append(dict(issue))
                self.risk_clear_counts.pop(issue_id, None)
            else:
                self.risk_clear_counts[issue_id] = clear_count

        triggered_issues = new_issues + escalated_issues
        return {
            "shouldTrigger": bool(triggered_issues),
            "reason": self._risk_trigger_reason(new_issues, escalated_issues),
            "contextLevel": RISK_CONTEXT_LEVEL,
            "triggeredIssues": triggered_issues,
            "newIssueCount": len(new_issues),
            "escalatedIssueCount": len(escalated_issues),
            "activeIssueCount": len(self.active_risk_issues),
            "resolvedIssues": resolved_issues,
            "suppression": {
                "policy": "trigger_once_until_resolved_or_escalated",
                "alertSeverities": sorted(RISK_ALERT_SEVERITIES),
                "resolutionGraceTicks": RISK_RESOLUTION_GRACE_TICKS,
            },
        }

    def _risk_issue_record(self, issue_id: str, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "issueId": issue_id,
            "eventId": event.get("eventId"),
            "eventType": event.get("eventType"),
            "severity": event.get("severity"),
            "source": event.get("source"),
            "sourceId": event.get("sourceId"),
            "metrics": event.get("metrics", {}),
            "lastSeenStepIndex": self.step_index,
        }

    def _risk_trigger_reason(self, new_issues: list[dict[str, Any]], escalated_issues: list[dict[str, Any]]) -> str | None:
        if new_issues and escalated_issues:
            return "new_issue_and_severity_escalation"
        if new_issues:
            return "new_issue"
        if escalated_issues:
            return "severity_escalation"
        return None

    def attach_risk_payload(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        risk_result = evaluate_swmm_risk(snapshot, previous_state=self.previous_risk_result)
        trigger = self.update_risk_issue_lifecycle(risk_result)
        snapshot["risk"] = {
            "ok": risk_result.get("ok", False),
            "highestSeverity": risk_result.get("highestSeverity", "NORMAL"),
            "events": risk_result.get("events", []),
            "summary": risk_result.get("summary", {}),
            "validation": risk_result.get("validation", {}),
            "counters": risk_result.get("counters", {}),
        }
        if trigger["shouldTrigger"]:
            trigger["context"] = build_swmm_context_packet(
                snapshot,
                risk_result,
                context_level=RISK_CONTEXT_LEVEL,
                system_meta={
                    "sourceService": "SuperMario_Django",
                    "targetService": "SuperMario_LLM",
                    "dispatchStatus": "not_called",
                },
                raw_snapshot_ref=str(self.tick_log_path),
            )
        snapshot["llmTrigger"] = trigger
        self.previous_risk_result = risk_result
        return snapshot

    def collect_snapshot(self, event_type: str = "snapshot") -> dict[str, Any]:
        node_states = self.collect_node_states()
        link_states = self.collect_link_states()
        editor_states = self.aggregate_editor_states(node_states, link_states)
        snapshot = {
            "type": event_type,
            "ok": True,
            "sourceOfTruth": "SWMM",
            "runId": self.run_id,
            "tickLogPath": str(self.tick_log_path),
            "source": self.spec.source,
            "modelPath": str(self.spec.model_path),
            "runtimeModelPath": str(self.runtime_model_path),
            "modelTime": self.model_time_iso(),
            "stepSeconds": self.step_seconds,
            "stepIndex": self.step_index,
            "control": self.control_state(),
            "nodes": node_states,
            "links": link_states,
            "editorObjects": editor_states,
            "summary": {
                "nodeCount": len(node_states),
                "linkCount": len(link_states),
                "rainfallTargetCount": len(self.rainfall_targets),
                "blockageTargetCount": len(self.control_link_ids),
                "activeBlockageCount": sum(1 for value in self.blockages_by_id.values() if value > 0),
            },
        }
        return self.attach_risk_payload(snapshot)

    def model_time_iso(self) -> str | None:
        try:
            return self.sim.current_time.isoformat()
        except Exception:
            return None

    def step(self) -> dict[str, Any]:
        self.apply_controls()
        next(self._iterator)
        self.step_index += 1
        self.last_snapshot = self.collect_snapshot("tick")
        self.append_tick_log(self.last_snapshot)
        return self.last_snapshot


class SwmmRuntimeEngine:
    """Django가 보유할 수 있는 비동기 SWMM 엔진 세션 wrapper."""

    def __init__(self) -> None:
        self.session: RealtimeSwmmSession | None = None
        self.task: asyncio.Task[None] | None = None
        self.lock = asyncio.Lock()
        self.last_start_payload: dict[str, Any] | None = None
        self.last_error: str | None = None
        self.paused = False

    def status_payload(self) -> dict[str, Any]:
        session = self.session
        running = session is not None and self.task is not None and not self.task.done()
        return {
            "ok": True,
            "running": running,
            "paused": session is not None and self.paused and not running,
            "hasSession": session is not None,
            "stepIndex": session.step_index if session else 0,
            "stepSeconds": session.step_seconds if session else DEFAULT_STEP_SECONDS,
            "modelTime": session.model_time_iso() if session else None,
            "control": session.control_state() if session else {
                "rainfallRatio": 0.0,
                "rainfallPercent": 0.0,
                "blockagesById": {},
                "maxRainfallMmPerHour": DEFAULT_MAX_RAINFALL_MM_PER_HOUR,
                "speedMultiplier": DEFAULT_SPEED_MULTIPLIER,
            },
            "lastError": self.last_error,
            "runId": session.run_id if session else None,
            "tickLogPath": str(session.tick_log_path) if session else None,
            "lastLogError": session.last_log_error if session else None,
        }

    async def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            await self.stop_locked()
            step_seconds = max(1, int(payload.get("stepSeconds") or DEFAULT_STEP_SECONDS))
            max_rainfall = safe_number(payload.get("maxRainfallMmPerHour"), DEFAULT_MAX_RAINFALL_MM_PER_HOUR)
            spec = build_runtime_model_spec(payload)
            try:
                session = RealtimeSwmmSession(
                    spec,
                    step_seconds=step_seconds,
                    max_rainfall_mm_per_hour=max_rainfall,
                )
            except Exception:
                spec.cleanup()
                raise
            session.update_controls(payload.get("control") or payload)
            self.session = session
            self.last_start_payload = payload
            self.last_error = None
            self.paused = False
            self.task = asyncio.create_task(self.run_loop())
            snapshot = session.collect_snapshot("started")
            session.last_snapshot = snapshot
            session.append_tick_log(snapshot)

        return {
            "ok": True,
            "running": True,
            "status": self.status_payload(),
            "report": session.report,
            "mapping": session.mapping,
            "snapshot": snapshot,
        }

    async def stop_locked(self) -> None:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
        if self.session is not None:
            self.session.append_runtime_event("stopped")
            self.session.close()
            self.session = None
        self.paused = False

    async def stop(self) -> dict[str, Any]:
        async with self.lock:
            await self.stop_locked()
        return self.status_payload()

    async def pause(self) -> dict[str, Any]:
        async with self.lock:
            if self.session is None:
                raise SwmmRuntimeError("SWMM engine is not running.", status_code=409)
            if self.task is not None and not self.task.done():
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
                self.task = None
            self.paused = True
            self.session.append_runtime_event("paused")
            snapshot = self.session.collect_snapshot("paused")
            self.session.last_snapshot = snapshot
            self.session.append_tick_log(snapshot)
        return self.status_payload()

    async def resume(self) -> dict[str, Any]:
        async with self.lock:
            if self.session is None:
                raise SwmmRuntimeError("SWMM engine is not running.", status_code=409)
            if self.task is None or self.task.done():
                self.paused = False
                self.session.append_runtime_event("resumed")
                snapshot = self.session.collect_snapshot("resumed")
                self.session.last_snapshot = snapshot
                self.session.append_tick_log(snapshot)
                self.task = asyncio.create_task(self.run_loop())
            else:
                self.paused = False
        return self.status_payload()

    async def reset(self, payload: dict[str, Any]) -> dict[str, Any]:
        next_payload = payload or self.last_start_payload
        if not next_payload:
            return await self.stop()
        return await self.start(next_payload)

    async def update_controls(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            if self.session is None:
                raise SwmmRuntimeError("SWMM engine is not running.", status_code=409)
            control = self.session.update_controls(payload)
            snapshot = self.session.collect_snapshot("control")
            self.session.last_snapshot = snapshot
            self.session.append_tick_log(snapshot)
        return {"ok": True, "control": control, "snapshot": snapshot}

    async def run_loop(self) -> None:
        while self.session is not None:
            started_at = time.monotonic()
            try:
                snapshot = self.session.step()
            except StopIteration:
                self.last_error = "simulation_finished"
                if self.session is not None:
                    self.session.append_runtime_event("finished")
                async with self.lock:
                    await self.stop_locked()
                return
            except Exception as exc:  # pragma: no cover - runtime safety net
                self.last_error = f"{exc.__class__.__name__}: {exc}"
                if self.session is not None:
                    self.session.append_runtime_event("error", {"message": self.last_error})
                async with self.lock:
                    await self.stop_locked()
                return

            if self.session is None:
                return
            self.session.last_snapshot = snapshot
            elapsed = time.monotonic() - started_at
            step_delay = self.session.step_seconds / max(DEFAULT_SPEED_MULTIPLIER, self.session.speed_multiplier)
            await asyncio.sleep(max(0.0, step_delay - elapsed))

    def latest_snapshot(self) -> dict[str, Any] | None:
        """마지막 snapshot을 반환한다. Django WebSocket이 필요할 때 호출한다."""

        if self.session is None:
            return None
        return self.session.last_snapshot
