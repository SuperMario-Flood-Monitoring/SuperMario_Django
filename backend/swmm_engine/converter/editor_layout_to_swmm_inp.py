#!/usr/bin/env python3
"""React editor layout JSON을 1차 SWMM .inp 모델로 변환한다.

이 converter는 React editor JSON을 배수도 topology의 source of truth로 취급한다.
기존 `viewer/overall_drainage_diagram.html`이나 HTML contract에 의존하지 않는다.
출력은 실행 가능한 보수적 SWMM 골격이며, 이후 editor가 명시적인 수리 필드를
제공하면 더 정밀하게 보강할 수 있다.

이 Django package copy는 `swmm.interface.convert_layout_to_inp()`의 내부 구현이다.
기존 CLI/FastAPI 호환용 루트 script는 남겨 두지만, Django 코드는 공개
interface를 통해 이 package copy를 사용해야 한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Literal


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PACKAGE_ROOT / "models" / "generated_from_editor.inp"

NodeSection = Literal["JUNCTIONS", "STORAGE", "OUTFALLS"]
LinkKind = Literal["CONDUIT", "PUMP", "WEIR"]

DEFAULT_SCALE_M_PER_PX = 0.5
DEFAULT_BASE_GROUND_ELEVATION_M = 100.0
DEFAULT_HORIZONTAL_SLOPE = 0.001
DEFAULT_MIN_CONDUIT_LENGTH_M = 1.0
DEFAULT_DRY_WEATHER_FLOW_CMS = 0.005
DEFAULT_RAINFALL_MM_PER_HOUR = 0.0
DEFAULT_CATCHMENT_AREA_M2 = 500.0
DEFAULT_RUNOFF_COEFFICIENT = 0.8
DEFAULT_MANHOLE_RAINFALL_FACTOR = 0.25
DEFAULT_MANNING_N = 0.013
MAX_BLOCKED_MANNING_N = 0.15
MAP_PADDING_M = 50.0
INTERNAL_RELATION_MIN_LENGTH_M = 5.0
INTERNAL_RELATION_MIN_DIAMETER_M = 1.20
DEFAULT_SIMULATION_START = datetime(2026, 6, 16, 0, 0, 0)
DEFAULT_SIMULATION_DURATION_SECONDS = 60 * 60

PIPE_DIAMETER_M = {
    "small": 0.30,
    "medium": 0.60,
    "large": 1.00,
}

PIPE_ROUGHNESS_N = {
    "storm": DEFAULT_MANNING_N,
    "sewer": DEFAULT_MANNING_N,
    "combined": DEFAULT_MANNING_N,
    "overflow": DEFAULT_MANNING_N,
    "treated": DEFAULT_MANNING_N,
}

PIPE_KIND_ALIASES = {
    "default": "storm",
    "sep_sewer_lateral_apartment_1": "sewer",
    "sep_sewer_lateral_apartment_2": "sewer",
    "sep_storm_lateral_catch_basin_1": "storm",
    "sep_storm_lateral_catch_basin_2": "storm",
    "sep_storm_trunk": "storm",
    "sep_interceptor": "sewer",
    "sep_storm_main_1": "storm",
    "sep_storm_main_2": "storm",
    "sep_storm_main_to_trunk": "storm",
    "sep_sewer_main_1": "sewer",
    "sep_sewer_main_2": "sewer",
    "sep_sewer_main_to_interceptor": "sewer",
    "storm_pump_discharge_pipe": "storm",
    "treatment_effluent_pipe": "treated",
    "comb_sewer_lateral_house_1": "sewer",
    "comb_sewer_lateral_house_2": "sewer",
    "comb_storm_lateral_catch_basin_1": "storm",
    "comb_main_1": "combined",
    "comb_main_2": "combined",
    "overflow_to_interceptor_drop": "combined",
    "overflow_pipe": "overflow",
    "comb_storm_lateral_catch_basin_2": "storm",
}

FACILITY_WATER_KIND = {
    "generic": "storm",
    "overflowChamber": "combined",
    "stormPumpStation": "storm",
    "waterReclamationCenter": "treated",
}

OUTFALL_WATER_KIND = {
    "generic": "storm",
    "overflowOutfall": "overflow",
    "pumpOutfall": "storm",
    "treatedOutfall": "treated",
}

STORAGE_AREA_BY_FACILITY_KIND = {
    "generic": 20.0,
    "overflowChamber": 4.0,
    "stormPumpStation": 12.0,
    "waterReclamationCenter": 80.0,
}


class ConversionError(Exception):
    """editor layout을 안전하게 변환할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class MapTransform:
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    scale_m_per_px: float
    padding_m: float = MAP_PADDING_M

    @property
    def dimensions(self) -> tuple[float, float, float, float]:
        width = max(1.0, (self.max_x - self.min_x) * self.scale_m_per_px + self.padding_m * 2)
        height = max(1.0, (self.max_y - self.min_y) * self.scale_m_per_px + self.padding_m * 2)
        return (0.0, 0.0, width, height)


@dataclass
class SwmmNode:
    id: str
    section: NodeSection
    elevation: float
    max_depth: float
    display_max_depth: float
    init_depth: float
    surcharge_depth: float
    ponded_area: float
    map_x: float
    map_y: float
    storage_factor: float = 0.0
    outfall_type: str = "FREE"
    source_editor_id: str | None = None
    source_editor_type: str | None = None
    source_editor_name: str | None = None
    react_x: float = 0.0
    react_y: float = 0.0


@dataclass
class SwmmLink:
    id: str
    kind: LinkKind
    from_node: str
    to_node: str
    length: float
    roughness: float
    diameter: float
    slope_hint: float = 0.001
    max_flow: float = 0.0
    inlet_loss: float = 0.0
    outlet_loss: float = 0.0
    average_loss: float = 0.0
    pump_curve: str = "DEFAULT_PUMP_CURVE"
    pipe_kind: str = "storm"
    blockage_percent: float = 0.0
    initial_setting: float = 1.0
    source_editor_id: str | None = None
    source_editor_type: str | None = None
    source_editor_name: str | None = None


@dataclass
class ConvertResult:
    nodes: dict[str, SwmmNode]
    links: list[SwmmLink]
    inflow_nodes: dict[str, list[str]]
    warnings: list[str]
    errors: list[str]
    editor_node_to_swmm_nodes: dict[str, list[str]]
    editor_node_to_swmm_links: dict[str, list[str]]
    editor_link_to_swmm_links: dict[str, list[str]]
    map_dimensions: tuple[float, float, float, float]


def load_layout(path: str) -> dict[str, Any]:
    if path == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(path).read_text(encoding="utf-8")
    try:
        layout = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConversionError(f"Editor layout JSON 파싱 실패: {exc}") from exc

    if not isinstance(layout, dict):
        raise ConversionError("Editor layout은 JSON object여야 합니다.")
    if layout.get("version") != 1:
        raise ConversionError("EditorLayout version 1만 지원합니다.")
    if not isinstance(layout.get("nodes"), list) or not isinstance(layout.get("links"), list):
        raise ConversionError("Editor layout에는 nodes[]와 links[]가 있어야 합니다.")
    return layout


def sanitize_id(value: Any, fallback: str) -> str:
    text = str(value or fallback).strip()
    text = re.sub(r"[^0-9A-Za-z_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"n_{text}"
    return text[:64]


def unique_id(base: str, used: set[str]) -> str:
    candidate = base
    index = 2
    while candidate in used:
        suffix = f"_{index}"
        candidate = f"{base[:64 - len(suffix)]}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def number(value: Any, fallback: float) -> float:
    if isinstance(value, bool):
        return fallback
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if math.isfinite(parsed) else fallback


def node_center(node: dict[str, Any]) -> Point:
    return Point(
        number(node.get("x"), 0.0) + number(node.get("width"), 0.0) / 2,
        number(node.get("y"), 0.0) + number(node.get("height"), 0.0) / 2,
    )


def layout_map_transform(
    nodes: Iterable[dict[str, Any]],
    *,
    scale_m_per_px: float,
    fallback_height: float,
) -> MapTransform:
    xs: list[float] = []
    ys: list[float] = []
    for node in nodes:
        x = number(node.get("x"), 0.0)
        y = number(node.get("y"), 0.0)
        width = number(node.get("width"), 0.0)
        height = number(node.get("height"), 0.0)
        xs.extend([x, x + width])
        ys.extend([y, y + height])

    if not xs or not ys:
        return MapTransform(0.0, 0.0, 3000.0, fallback_height, scale_m_per_px)

    return MapTransform(min(xs), min(ys), max(xs), max(ys), scale_m_per_px)


def map_point(point: Point, transform: MapTransform) -> Point:
    return Point(
        (point.x - transform.min_x) * transform.scale_m_per_px + transform.padding_m,
        (transform.max_y - point.y) * transform.scale_m_per_px + transform.padding_m,
    )


def depth_for_point(point: Point, ground_surface_y: float, scale_m_per_px: float) -> float:
    # React 화면 y축은 아래로 증가한다. 음수 깊이는 지표면/지상 객체를 의미한다.
    return max(0.0, (point.y - ground_surface_y) * scale_m_per_px)


def elevation_for_point(
    point: Point,
    ground_surface_y: float,
    scale_m_per_px: float,
    base_ground_elevation_m: float,
) -> float:
    return base_ground_elevation_m - depth_for_point(point, ground_surface_y, scale_m_per_px)


def normalize_pipe_size(value: Any) -> str:
    return str(value) if value in PIPE_DIAMETER_M else "medium"


def normalize_pipe_kind(value: Any) -> str:
    raw = str(value or "storm")
    raw = PIPE_KIND_ALIASES.get(raw, raw)
    return raw if raw in PIPE_ROUGHNESS_N else "storm"


def normalize_facility_kind(node: dict[str, Any]) -> str:
    kind = str((node.get("props") or {}).get("facilityKind") or "generic")
    return kind if kind in FACILITY_WATER_KIND else "generic"


def normalize_outfall_kind(node: dict[str, Any]) -> str:
    kind = str((node.get("props") or {}).get("outfallKind") or "generic")
    return kind if kind in OUTFALL_WATER_KIND else "generic"


FLOOD_ALLOWED_NODE_TYPES = {"catchBasin", "manhole"}
NO_FLOOD_STORAGE_MAX_DEPTH_M = 1000.0


def flooding_allowed(source_node: dict[str, Any] | None) -> bool:
    if not source_node:
        return False
    return str(source_node.get("type") or "") in FLOOD_ALLOWED_NODE_TYPES


def node_section(node: dict[str, Any]) -> NodeSection | None:
    node_type = node.get("type")
    if node_type == "pipeSegment":
        return None
    if node_type == "outfall":
        return "OUTFALLS"
    if node_type in {"catchBasin", "facility"}:
        return "STORAGE"
    return "JUNCTIONS"


def node_depth_defaults(node: dict[str, Any]) -> tuple[float, float, float, float]:
    node_type = node.get("type")
    height = number(node.get("height"), 100.0)
    if node_type == "catchBasin":
        return 1.20, 0.00, 0.70, 18.0
    if node_type == "manhole":
        return max(2.5, height * 0.018), 0.00, 1.20, 20.0
    if node_type == "facility":
        kind = normalize_facility_kind(node)
        if kind == "waterReclamationCenter":
            return 4.00, 0.00, 0.80, 0.0
        if kind == "stormPumpStation":
            return 2.40, 0.00, 0.50, 0.0
        if kind == "overflowChamber":
            return 2.80, 0.00, 1.00, 20.0
        return 2.00, 0.00, 0.50, 0.0
    if node_type == "outfall":
        return 0.0, 0.0, 0.0, 0.0
    if node_type in {"apartment", "house"}:
        return 1.00, 0.00, 0.50, 0.0
    if node_type in {"connector", "elbowConnector", "teeConnector"}:
        return 1.50, 0.00, 0.50, 0.0
    return 2.00, 0.00, 0.50, 0.0


def storage_factor(node: dict[str, Any]) -> float:
    if node.get("type") == "catchBasin":
        return 2.0
    if node.get("type") == "facility":
        return STORAGE_AREA_BY_FACILITY_KIND[normalize_facility_kind(node)]
    return 0.0


def node_inflow_series(node: dict[str, Any]) -> str | None:
    node_type = node.get("type")
    if node_type == "catchBasin":
        return "TS_STORM_RAIN"
    if node_type == "manhole":
        return "TS_MANHOLE_RAIN"
    if node_type in {"apartment", "house"}:
        return "TS_SEWER_DWF"
    return None


def tap_info(port_id: str) -> tuple[str, float] | None:
    match = re.match(r"^tap-(top|right|bottom|left)-(\d+(?:\.\d+)?)$", port_id)
    if not match:
        return None
    percent = float(match.group(2))
    if percent <= 0 or percent >= 100:
        return None
    return match.group(1), percent / 100


def node_orientation(node: dict[str, Any]) -> str:
    props = node.get("props") or {}
    rotation = number(props.get("rotation"), 0)
    if node.get("type") == "pipeSegment" and rotation in {90, 270}:
        return "vertical"
    return "horizontal" if number(node.get("width"), 0) >= number(node.get("height"), 0) else "vertical"


def node_rotation_degrees(node: dict[str, Any]) -> int:
    rotation = int(round(number((node.get("props") or {}).get("rotation"), 0))) % 360
    return rotation if rotation in {0, 90, 180, 270} else 0


def expected_station_direction(node: dict[str, Any]) -> int:
    # 0도는 left -> right, 90도는 top -> bottom이다. 180/270도 회전은 station 순서를 뒤집는다.
    return -1 if node_rotation_degrees(node) in {180, 270} else 1


def display_name_for_report(node: dict[str, Any]) -> str:
    return str(node.get("name") or node.get("swmmId") or node.get("id") or "")


def text_matches_any(value: str, terms: Iterable[str]) -> bool:
    normalized = value.lower().replace(" ", "")
    return any(term.lower().replace(" ", "") in normalized for term in terms)


def blockage_to_roughness(blockage_percent: float, base_roughness: float) -> tuple[float, float]:
    clamped = max(0.0, min(100.0, blockage_percent))
    if clamped >= 100.0:
        return MAX_BLOCKED_MANNING_N, 0.0
    ratio = clamped / 100.0
    return base_roughness + (MAX_BLOCKED_MANNING_N - base_roughness) * ratio, 1.0


def standard_port_point(node: dict[str, Any], port_id: str) -> Point:
    x = number(node.get("x"), 0.0)
    y = number(node.get("y"), 0.0)
    width = number(node.get("width"), 0.0)
    height = number(node.get("height"), 0.0)
    parsed_tap = tap_info(port_id)
    if parsed_tap:
        side, ratio = parsed_tap
        if side == "top":
            return Point(x + width * ratio, y)
        if side == "bottom":
            return Point(x + width * ratio, y + height)
        if side == "right":
            return Point(x + width, y + height * ratio)
        return Point(x, y + height * ratio)
    if port_id == "top":
        return Point(x + width / 2, y)
    if port_id == "right":
        return Point(x + width, y + height / 2)
    if port_id == "bottom":
        return Point(x + width / 2, y + height)
    if port_id == "left":
        return Point(x, y + height / 2)
    return node_center(node)


def pipe_station_point(node: dict[str, Any], station: float) -> Point:
    x = number(node.get("x"), 0.0)
    y = number(node.get("y"), 0.0)
    width = number(node.get("width"), 0.0)
    height = number(node.get("height"), 0.0)
    if node_orientation(node) == "vertical":
        return Point(x + width / 2, y + height * station)
    return Point(x + width * station, y + height / 2)


def pipe_station_for_port(node: dict[str, Any], port_id: str) -> float | None:
    orientation = node_orientation(node)
    if orientation == "horizontal":
        if port_id == "left":
            return 0.0
        if port_id == "right":
            return 1.0
        parsed_tap = tap_info(port_id)
        if parsed_tap and parsed_tap[0] in {"top", "bottom"}:
            return parsed_tap[1]
        return None
    if port_id == "top":
        return 0.0
    if port_id == "bottom":
        return 1.0
    parsed_tap = tap_info(port_id)
    if parsed_tap and parsed_tap[0] in {"left", "right"}:
        return parsed_tap[1]
    return None


def visual_length_m(
    start: Point,
    end: Point,
    scale_m_per_px: float,
    minimum: float = DEFAULT_MIN_CONDUIT_LENGTH_M,
) -> float:
    return max(minimum, math.hypot(end.x - start.x, end.y - start.y) * scale_m_per_px)


def make_swmm_node(
    node_id: str,
    section: NodeSection,
    point: Point,
    ground_surface_y: float,
    map_transform: MapTransform,
    base_ground_elevation_m: float,
    source_node: dict[str, Any] | None = None,
) -> SwmmNode:
    max_depth, init_depth, surcharge_depth, ponded_area = node_depth_defaults(source_node or {})
    display_max_depth = max_depth
    depth_m = depth_for_point(point, ground_surface_y, map_transform.scale_m_per_px)
    if section != "OUTFALLS":
        if section == "STORAGE":
            # STORAGE 계열 editor object는 자체 수리 깊이를 가진다.
            # 화면상의 깊이는 diagram에서 수백 m가 될 수 있으므로,
            # 채움/월류 거동에 쓰는 tank 깊이로 사용하지 않는다.
            max_depth = max(max_depth, 1.0)
            display_max_depth = max(display_max_depth, 1.0)
        else:
            max_depth = max(depth_m, max_depth, 1.0)
            display_max_depth = max_depth
    if section != "OUTFALLS" and source_node and not flooding_allowed(source_node):
        max_depth = max(max_depth, NO_FLOOD_STORAGE_MAX_DEPTH_M)
    mapped = map_point(point, map_transform)
    return SwmmNode(
        id=node_id,
        section=section,
        elevation=elevation_for_point(point, ground_surface_y, map_transform.scale_m_per_px, base_ground_elevation_m),
        max_depth=max_depth,
        display_max_depth=display_max_depth,
        init_depth=init_depth,
        surcharge_depth=surcharge_depth,
        ponded_area=ponded_area,
        map_x=mapped.x,
        map_y=mapped.y,
        storage_factor=storage_factor(source_node or {}),
        source_editor_id=str(source_node.get("id")) if source_node and source_node.get("id") is not None else None,
        source_editor_type=str(source_node.get("type")) if source_node and source_node.get("type") is not None else None,
        source_editor_name=str(source_node.get("name")) if source_node and source_node.get("name") is not None else None,
        react_x=point.x,
        react_y=point.y,
    )


def validate_unique_editor_ids(nodes: list[dict[str, Any]], links: list[dict[str, Any]]) -> None:
    node_ids = [str(node.get("id", "")) for node in nodes]
    link_ids = [str(link.get("id", "")) for link in links]
    duplicate_nodes = sorted({item for item in node_ids if node_ids.count(item) > 1})
    duplicate_links = sorted({item for item in link_ids if link_ids.count(item) > 1})
    if duplicate_nodes:
        raise ConversionError(f"Duplicate editor node ids: {', '.join(duplicate_nodes)}")
    if duplicate_links:
        raise ConversionError(f"Duplicate editor link ids: {', '.join(duplicate_links)}")


def convert_layout(
    layout: dict[str, Any],
    *,
    scale_m_per_px: float = DEFAULT_SCALE_M_PER_PX,
    map_height: float = 2000.0,
    base_ground_elevation_m: float = DEFAULT_BASE_GROUND_ELEVATION_M,
) -> ConvertResult:
    editor_nodes = layout["nodes"]
    editor_links = layout["links"]
    validate_unique_editor_ids(editor_nodes, editor_links)

    ground_surface_y = number(layout.get("groundSurfaceY"), 330.0)
    map_transform = layout_map_transform(
        editor_nodes,
        scale_m_per_px=scale_m_per_px,
        fallback_height=map_height,
    )
    nodes_by_editor_id = {str(node["id"]): node for node in editor_nodes if "id" in node}
    relation_links = [link for link in editor_links if link.get("type") == "relation"]
    relation_attach_points_by_node: dict[str, list[Point]] = {}

    def relation_endpoint_point(link: dict[str, Any], endpoint_name: str) -> Point | None:
        endpoint = link.get(endpoint_name) or {}
        node_id = str(endpoint.get("nodeId") or "")
        node = nodes_by_editor_id.get(node_id)
        if not node:
            return None
        attach = link.get("attach")
        attach_key = "parentEndpoint" if endpoint_name == "from" else "childEndpoint"
        if isinstance(attach, dict):
            attach_endpoint = attach.get(attach_key)
            if isinstance(attach_endpoint, dict) and str(attach_endpoint.get("nodeId")) == node_id:
                point = attach_endpoint.get("point")
                if isinstance(point, dict):
                    return Point(number(point.get("x"), 0.0), number(point.get("y"), 0.0))
        return standard_port_point(node, str(endpoint.get("portId")))

    for relation in relation_links:
        for endpoint_name in ("from", "to"):
            endpoint = relation.get(endpoint_name) or {}
            node_id = str(endpoint.get("nodeId") or "")
            point = relation_endpoint_point(relation, endpoint_name)
            if point is not None:
                relation_attach_points_by_node.setdefault(node_id, []).append(point)

    def hydraulic_point_for_node(node: dict[str, Any]) -> Point:
        center = node_center(node)
        node_type = node.get("type")
        attach_points = relation_attach_points_by_node.get(str(node.get("id")), [])
        if node_type == "manhole":
            if attach_points:
                # 맨홀은 시각 중심보다 실제 관이 붙는 하단 접속부를 SWMM node 위치로 쓴다.
                return Point(center.x, max(point.y for point in attach_points))
        if node_type in {"connector", "elbowConnector", "teeConnector"} and attach_points:
            # 커넥터류의 시각 중심은 관 중심선보다 위에 있을 수 있다. 이를 그대로
            # invert elevation에 쓰면 수평관이 오르막으로 변하므로, 실제 관이 붙는
            # 가장 깊은 접속부를 hydraulic junction 위치로 사용한다.
            deepest_y = max(point.y for point in attach_points)
            deepest_points = [point for point in attach_points if abs(point.y - deepest_y) < 1e-6]
            deepest_x = sum(point.x for point in deepest_points) / max(1, len(deepest_points))
            return Point(deepest_x, deepest_y)
        return center

    used_node_ids: set[str] = set()
    used_link_ids: set[str] = set()
    swmm_nodes: dict[str, SwmmNode] = {}
    editor_to_swmm_node: dict[str, str] = {}
    inflow_nodes: dict[str, list[str]] = {}
    warnings: list[str] = []
    errors: list[str] = []
    editor_node_to_swmm_nodes: dict[str, list[str]] = {}
    editor_node_to_swmm_links: dict[str, list[str]] = {}
    editor_link_to_swmm_links: dict[str, list[str]] = {}

    def add_node(node: SwmmNode) -> str:
        if node.id in swmm_nodes:
            return node.id
        swmm_nodes[node.id] = node
        if node.source_editor_id:
            editor_node_to_swmm_nodes.setdefault(node.source_editor_id, []).append(node.id)
        return node.id

    def add_inflow(node_id: str, series: str) -> None:
        series_list = inflow_nodes.setdefault(node_id, [])
        if series not in series_list:
            series_list.append(series)

    def add_node_link_mapping(editor_node_id: Any, link_id: str) -> None:
        if editor_node_id is not None:
            editor_node_to_swmm_links.setdefault(str(editor_node_id), []).append(link_id)

    def add_editor_link_mapping(editor_link_id: Any, link_id: str) -> None:
        if editor_link_id is not None:
            editor_link_to_swmm_links.setdefault(str(editor_link_id), []).append(link_id)

    for editor_node in editor_nodes:
        section = node_section(editor_node)
        if section is None:
            continue
        editor_id = str(editor_node["id"])
        base_id = sanitize_id(editor_node.get("swmmId") or editor_node.get("id"), f"node_{len(swmm_nodes) + 1}")
        swmm_id = unique_id(base_id, used_node_ids)
        editor_to_swmm_node[editor_id] = swmm_id
        point = hydraulic_point_for_node(editor_node)
        swmm_node = make_swmm_node(
            swmm_id,
            section,
            point,
            ground_surface_y,
            map_transform,
            base_ground_elevation_m,
            editor_node,
        )
        if section == "OUTFALLS":
            swmm_node.elevation = max(0.2, swmm_node.elevation)
        add_node(swmm_node)
        series = node_inflow_series(editor_node)
        if series:
            add_inflow(swmm_id, series)

    def generated_junction(base: str, point: Point, source_node: dict[str, Any] | None = None) -> str:
        node_id = unique_id(sanitize_id(base, "junction"), used_node_ids)
        add_node(make_swmm_node(
            node_id,
            "JUNCTIONS",
            point,
            ground_surface_y,
            map_transform,
            base_ground_elevation_m,
            source_node,
        ))
        return node_id

    swmm_links: list[SwmmLink] = []

    def station_from_relation_attach(pipe: dict[str, Any], link: dict[str, Any], endpoint_name: str) -> float | None:
        attach = link.get("attach")
        if not isinstance(attach, dict):
            return None
        attach_key = "childOnParent" if endpoint_name == "from" else "parentOnChild"
        attach_point = attach.get(attach_key)
        if not isinstance(attach_point, dict):
            return None
        if str(attach_point.get("nodeId")) != str(pipe.get("id")):
            return None
        side = str(attach_point.get("side") or "")
        orientation = node_orientation(pipe)
        if orientation == "horizontal" and side not in {"top", "bottom"}:
            return None
        if orientation == "vertical" and side not in {"left", "right"}:
            return None
        ratio = number(attach_point.get("ratio"), math.nan)
        if not math.isfinite(ratio) or ratio <= 0 or ratio >= 1:
            return None
        return ratio

    def relation_counterparts_for_pipe(pipe: dict[str, Any]) -> list[tuple[str, float | None, dict[str, str], str, str]]:
        pipe_id = str(pipe["id"])
        pairs: list[tuple[str, float | None, dict[str, str], str, str]] = []
        for link in relation_links:
            for endpoint_name, other_name in (("from", "to"), ("to", "from")):
                endpoint = link.get(endpoint_name) or {}
                if endpoint.get("nodeId") != pipe_id:
                    continue
                other = link.get(other_name) or {}
                port_id = str(endpoint.get("portId"))
                station = station_from_relation_attach(pipe, link, endpoint_name)
                if station is None:
                    station = pipe_station_for_port(pipe, port_id)
                pairs.append((
                    port_id,
                    station,
                    {"nodeId": str(other.get("nodeId")), "portId": str(other.get("portId"))},
                    endpoint_name,
                    str(link.get("id") or ""),
                ))
        return pairs

    def resolve_non_pipe_endpoint(endpoint: dict[str, Any]) -> str | None:
        node_id = str(endpoint.get("nodeId"))
        node = nodes_by_editor_id.get(node_id)
        if not node:
            return None
        if node.get("type") == "pipeSegment":
            return None
        return editor_to_swmm_node.get(node_id)

    def add_link(link: SwmmLink) -> str | None:
        if link.from_node == link.to_node:
            warnings.append(f"자기 자신을 연결하는 SWMM link를 건너뜀: {link.id}: {link.from_node}")
            return None
        link.id = unique_id(sanitize_id(link.id, f"link_{len(swmm_links) + 1}"), used_link_ids)
        swmm_links.append(link)
        if link.source_editor_type == "pipeSegment":
            add_node_link_mapping(link.source_editor_id, link.id)
        else:
            add_editor_link_mapping(link.source_editor_id, link.id)
        return link.id

    def pipe_defaults(source: dict[str, Any]) -> tuple[str, str, float, float, float]:
        props = source.get("props") or {}
        size = normalize_pipe_size(props.get("size") or source.get("size"))
        pipe_kind = normalize_pipe_kind(props.get("pipeKind"))
        diameter = PIPE_DIAMETER_M[size]
        blockage = number(props.get("blockage"), 0.0)
        roughness, initial_setting = blockage_to_roughness(blockage, PIPE_ROUGHNESS_N[pipe_kind])
        return size, pipe_kind, diameter, roughness, initial_setting

    def hydraulic_relation_defaults(
        from_node: dict[str, Any],
        to_node: dict[str, Any],
        relation: dict[str, Any],
    ) -> tuple[str, float, float]:
        candidates = [from_node.get("props") or {}, to_node.get("props") or {}, relation.get("props") or {}]
        size = "medium"
        pipe_kind = "storm"
        for props in candidates:
            if props.get("size") in PIPE_DIAMETER_M:
                size = str(props.get("size"))
                break
        for props in candidates:
            raw_kind = props.get("pipeKind") or props.get("manholeKind")
            normalized = normalize_pipe_kind(raw_kind)
            if raw_kind is not None:
                pipe_kind = normalized
                break
        return pipe_kind, PIPE_DIAMETER_M[size], PIPE_ROUGHNESS_N[pipe_kind]

    def infer_pipe_station_direction(
        pipe: dict[str, Any],
        relations: list[tuple[str, float | None, dict[str, str], str, str]],
    ) -> int:
        inflow_stations = [
            station for _port_id, station, _other, endpoint_name, _relation_id in relations
            if station is not None and endpoint_name == "to"
        ]
        outflow_stations = [
            station for _port_id, station, _other, endpoint_name, _relation_id in relations
            if station is not None and endpoint_name == "from"
        ]
        if inflow_stations and outflow_stations:
            inferred = 1 if min(inflow_stations) <= max(outflow_stations) else -1
            expected = expected_station_direction(pipe)
            if inferred != expected:
                warnings.append(
                    "Pipe flow direction follows relation click order but conflicts with rotation: "
                    f"{display_name_for_report(pipe)} expected {'ascending' if expected > 0 else 'descending'} station order."
                )
            return inferred
        return expected_station_direction(pipe)

    pipe_endpoint_cache: dict[tuple[str, float], str] = {}

    def resolve_pipe_station_node(pipe: dict[str, Any], station: float, counterpart: dict[str, str] | None = None) -> str:
        rounded_station = round(station, 6)
        cache_key = (str(pipe["id"]), rounded_station)
        if cache_key in pipe_endpoint_cache:
            return pipe_endpoint_cache[cache_key]

        counterpart_node_id = counterpart.get("nodeId") if counterpart else None
        counterpart_node = nodes_by_editor_id.get(str(counterpart_node_id)) if counterpart_node_id else None
        if counterpart_node and counterpart_node.get("type") != "pipeSegment":
            swmm_id = editor_to_swmm_node.get(str(counterpart_node_id))
            if swmm_id:
                pipe_endpoint_cache[cache_key] = swmm_id
                return swmm_id

        point = pipe_station_point(pipe, station)
        node_id = generated_junction(
            f"{pipe.get('swmmId') or pipe.get('id')}_station_{int(rounded_station * 1000):03d}",
            point,
            pipe,
        )
        pipe_endpoint_cache[cache_key] = node_id
        return node_id

    def add_pipe_segment_links(pipe: dict[str, Any]) -> None:
        pipe_id = str(pipe["id"])
        relations = relation_counterparts_for_pipe(pipe)
        stations: dict[float, dict[str, str] | None] = {0.0: None, 1.0: None}

        for port_id, station, other, _endpoint_name, _relation_id in relations:
            if station is None:
                warnings.append(f"Ignored relation on unsupported pipe port {pipe_id}:{port_id}")
                continue
            if station in stations and stations[station] is not None and stations[station] != other:
                warnings.append(f"여러 연결이 같은 관로 station을 공유함: {pipe_id}@{station:.2f}; 첫 연결만 SWMM junction으로 사용합니다.")
                continue
            stations[station] = other

        ordered = sorted(stations.items(), key=lambda item: item[0])
        station_nodes = [
            (station, resolve_pipe_station_node(pipe, station, other))
            for station, other in ordered
        ]
        if len(station_nodes) < 2:
            return

        _size, pipe_kind, diameter, roughness, initial_setting = pipe_defaults(pipe)
        slope_hint = number((pipe.get("props") or {}).get("slope"), DEFAULT_HORIZONTAL_SLOPE)
        point_by_station = {station: pipe_station_point(pipe, station) for station, _node in station_nodes}
        station_direction = infer_pipe_station_direction(pipe, relations)
        ordered_station_nodes = station_nodes if station_direction > 0 else list(reversed(station_nodes))
        segment_count = 0
        for index in range(len(ordered_station_nodes) - 1):
            start_station, start_node = ordered_station_nodes[index]
            end_station, end_node = ordered_station_nodes[index + 1]
            if abs(end_station - start_station) < 1e-6:
                continue
            segment_count += 1
            visual_start = point_by_station[start_station]
            visual_end = point_by_station[end_station]
            base_link_id = sanitize_id(pipe.get("swmmId") or pipe.get("id"), "pipe")
            link_id = base_link_id if len(station_nodes) == 2 else f"{base_link_id}_segment_{segment_count:02d}"
            add_link(SwmmLink(
                id=link_id,
                kind="CONDUIT",
                from_node=start_node,
                to_node=end_node,
                length=visual_length_m(visual_start, visual_end, scale_m_per_px),
                roughness=roughness,
                diameter=diameter,
                slope_hint=slope_hint,
                average_loss=0.0,
                pipe_kind=pipe_kind,
                blockage_percent=number((pipe.get("props") or {}).get("blockage"), 0.0),
                initial_setting=initial_setting,
                source_editor_id=str(pipe.get("id")),
                source_editor_type="pipeSegment",
                source_editor_name=str(pipe.get("name") or ""),
            ))

    for editor_node in editor_nodes:
        if editor_node.get("type") == "pipeSegment":
            add_pipe_segment_links(editor_node)

    def add_internal_relation_links() -> None:
        for relation in relation_links:
            from_endpoint = relation.get("from") or {}
            to_endpoint = relation.get("to") or {}
            from_editor_node = nodes_by_editor_id.get(str(from_endpoint.get("nodeId") or ""))
            to_editor_node = nodes_by_editor_id.get(str(to_endpoint.get("nodeId") or ""))
            if not from_editor_node or not to_editor_node:
                continue
            if from_editor_node.get("type") == "pipeSegment" or to_editor_node.get("type") == "pipeSegment":
                continue

            from_node = resolve_non_pipe_endpoint(from_endpoint)
            to_node = resolve_non_pipe_endpoint(to_endpoint)
            if not from_node or not to_node:
                continue

            from_point = relation_endpoint_point(relation, "from") or standard_port_point(from_editor_node, str(from_endpoint.get("portId")))
            to_point = relation_endpoint_point(relation, "to") or standard_port_point(to_editor_node, str(to_endpoint.get("portId")))
            pipe_kind, diameter, roughness = hydraulic_relation_defaults(from_editor_node, to_editor_node, relation)
            diameter = max(diameter, INTERNAL_RELATION_MIN_DIAMETER_M)
            relation_id = str(relation.get("swmmId") or relation.get("id") or "relation")
            add_link(SwmmLink(
                id=f"{sanitize_id(relation_id, 'relation')}_CONDUIT",
                kind="CONDUIT",
                from_node=from_node,
                to_node=to_node,
                length=visual_length_m(from_point, to_point, scale_m_per_px, INTERNAL_RELATION_MIN_LENGTH_M),
                roughness=roughness,
                diameter=diameter,
                slope_hint=DEFAULT_HORIZONTAL_SLOPE,
                pipe_kind=pipe_kind,
                source_editor_id=str(relation.get("id") or ""),
                source_editor_type="relation",
                source_editor_name=str(relation.get("name") or ""),
            ))

    add_internal_relation_links()

    def resolve_endpoint(endpoint: dict[str, Any]) -> str | None:
        node_id = str(endpoint.get("nodeId"))
        node = nodes_by_editor_id.get(node_id)
        if not node:
            return None
        if node.get("type") != "pipeSegment":
            return resolve_non_pipe_endpoint(endpoint)
        station = pipe_station_for_port(node, str(endpoint.get("portId")))
        if station is None:
            return None
        return resolve_pipe_station_node(node, station)

    for editor_link in editor_links:
        link_type = str(editor_link.get("type") or "relation")
        if link_type == "relation":
            continue
        from_node = resolve_endpoint(editor_link.get("from") or {})
        to_node = resolve_endpoint(editor_link.get("to") or {})
        if not from_node or not to_node:
            warnings.append(f"Skipped editor link {editor_link.get('id')}: unresolved endpoint")
            continue

        props = editor_link.get("props") or {}
        size = normalize_pipe_size(editor_link.get("size"))
        pipe_kind = normalize_pipe_kind(props.get("pipeKind"))
        diameter = PIPE_DIAMETER_M[size]
        blockage = number(props.get("blockage"), 0.0)
        roughness, initial_setting = blockage_to_roughness(blockage, PIPE_ROUGHNESS_N[pipe_kind])
        start_point = standard_port_point(nodes_by_editor_id[str((editor_link.get("from") or {}).get("nodeId"))], str((editor_link.get("from") or {}).get("portId")))
        end_point = standard_port_point(nodes_by_editor_id[str((editor_link.get("to") or {}).get("nodeId"))], str((editor_link.get("to") or {}).get("portId")))
        length = number(props.get("length"), 0.0) or visual_length_m(start_point, end_point, scale_m_per_px)
        route = props.get("route")
        average_loss = 0.35 if route == "elbow" or link_type == "elbowPipe" else 0.0
        swmm_kind: LinkKind = "CONDUIT"
        if link_type == "pump":
            swmm_kind = "PUMP"
        elif link_type == "weir":
            swmm_kind = "WEIR"
        add_link(SwmmLink(
            id=sanitize_id(editor_link.get("swmmId") or editor_link.get("id"), "link"),
            kind=swmm_kind,
            from_node=from_node,
            to_node=to_node,
            length=length,
            roughness=roughness,
            diameter=diameter,
            slope_hint=number(props.get("slope"), DEFAULT_HORIZONTAL_SLOPE),
            average_loss=average_loss,
            pipe_kind=pipe_kind,
            blockage_percent=blockage,
            initial_setting=initial_setting,
            source_editor_id=str(editor_link.get("id")),
            source_editor_type=link_type,
            source_editor_name=str(editor_link.get("name") or ""),
        ))

    def add_main_head_inflows() -> None:
        conduits = [
            link for link in swmm_links
            if link.kind == "CONDUIT" and link.source_editor_type == "pipeSegment"
        ]
        incoming_by_kind: dict[str, set[str]] = {}
        for link in swmm_links:
            if link.kind != "CONDUIT":
                continue
            incoming_by_kind.setdefault(link.pipe_kind, set()).add(link.to_node)

        for link in conduits:
            source_node = nodes_by_editor_id.get(str(link.source_editor_id or ""))
            label_text = " ".join([
                str(link.id),
                str(link.source_editor_name or ""),
                str(source_node.get("swmmId") if source_node else ""),
                str(source_node.get("name") if source_node else ""),
            ])
            is_main = text_matches_any(label_text, ["본관", "main", "trunk"])
            if not is_main:
                continue
            if link.from_node in incoming_by_kind.get(link.pipe_kind, set()):
                continue
            if link.pipe_kind == "storm":
                add_inflow(link.from_node, "TS_STORM_RAIN")
            elif link.pipe_kind == "sewer":
                add_inflow(link.from_node, "TS_SEWER_DWF")
            elif link.pipe_kind == "combined":
                add_inflow(link.from_node, "TS_SEWER_DWF")
                add_inflow(link.from_node, "TS_STORM_RAIN")

    add_main_head_inflows()

    validate_swmm_references(swmm_nodes, swmm_links)
    if not any(node.section == "OUTFALLS" for node in swmm_nodes.values()):
        errors.append("생성된 모델에 OUTFALLS node가 없습니다. 실행 가능한 배수 모델을 내보내기 전에 방류구를 최소 1개 추가하세요.")
    if not swmm_links:
        errors.append("생성된 모델에 수리 link가 없습니다.")

    return ConvertResult(
        swmm_nodes,
        swmm_links,
        inflow_nodes,
        warnings,
        errors,
        editor_node_to_swmm_nodes,
        editor_node_to_swmm_links,
        editor_link_to_swmm_links,
        map_transform.dimensions,
    )


def validate_swmm_references(nodes: dict[str, SwmmNode], links: Iterable[SwmmLink]) -> None:
    node_ids = set(nodes)
    errors: list[str] = []
    for link in links:
        if link.from_node not in node_ids:
            errors.append(f"{link.id} references missing from node {link.from_node}")
        if link.to_node not in node_ids:
            errors.append(f"{link.id} references missing to node {link.to_node}")
    if errors:
        raise ConversionError("; ".join(errors))


def format_node_line(node: SwmmNode) -> str:
    if node.section == "JUNCTIONS":
        return (
            f"{node.id:<40} {node.elevation:>7.2f} {node.max_depth:>7.2f} "
            f"{node.init_depth:>7.2f} {node.surcharge_depth:>8.2f} {node.ponded_area:>8.1f}"
        )
    if node.section == "STORAGE":
        return (
            f"{node.id:<40} {node.elevation:>7.2f} {node.max_depth:>7.2f} "
            f"{node.init_depth:>7.2f} FUNCTIONAL {node.storage_factor:<7.2f} 0      0      0    0"
        )
    return f"{node.id:<40} {node.elevation:>7.2f} FREE                        NO"


def simulation_window(duration_seconds: int | float | None = None) -> tuple[datetime, datetime]:
    safe_duration_seconds = max(
        1,
        int(duration_seconds if duration_seconds is not None else DEFAULT_SIMULATION_DURATION_SECONDS),
    )
    start = DEFAULT_SIMULATION_START
    return start, start + timedelta(seconds=safe_duration_seconds)


def render_inp(
    result: ConvertResult,
    *,
    title: str,
    duration_seconds: int | float | None = None,
) -> str:
    nodes = result.nodes
    links = result.links
    junctions = [node for node in nodes.values() if node.section == "JUNCTIONS"]
    storages = [node for node in nodes.values() if node.section == "STORAGE"]
    outfalls = [node for node in nodes.values() if node.section == "OUTFALLS"]
    conduits = [link for link in links if link.kind == "CONDUIT"]
    pumps = [link for link in links if link.kind == "PUMP"]
    weirs = [link for link in links if link.kind == "WEIR"]
    start_at, end_at = simulation_window(duration_seconds)
    lines: list[str] = []
    add = lines.append

    add("[TITLE]")
    add(f";; {title}")
    add(";; React editor layout JSON에서 생성됨.")
    add("")
    add("[OPTIONS]")
    add("FLOW_UNITS           CMS")
    add("INFILTRATION         HORTON")
    add("FLOW_ROUTING         DYNWAVE")
    add("LINK_OFFSETS         DEPTH")
    add("MIN_SLOPE            0.0001")
    add("ALLOW_PONDING        YES")
    add(f"START_DATE           {start_at:%m/%d/%Y}")
    add(f"START_TIME           {start_at:%H:%M:%S}")
    add(f"REPORT_START_DATE    {start_at:%m/%d/%Y}")
    add(f"REPORT_START_TIME    {start_at:%H:%M:%S}")
    add(f"END_DATE             {end_at:%m/%d/%Y}")
    add(f"END_TIME             {end_at:%H:%M:%S}")
    add("WET_STEP             00:00:01")
    add("DRY_STEP             00:01:00")
    add("REPORT_STEP          00:00:01")
    add("ROUTING_STEP         0:00:01")
    add("")

    if junctions:
        add("[JUNCTIONS]")
        add(";;Name                                   Elev    MaxDepth InitDepth SurDepth Aponded")
        for node in junctions:
            add(format_node_line(node))
        add("")

    if storages:
        add("[STORAGE]")
        add(";;Name                                   Elev    MaxDepth InitDepth Shape      Curve/Params          N/A  Fevap Psi Ksat IMD")
        for node in storages:
            add(format_node_line(node))
        add("")

    if outfalls:
        add("[OUTFALLS]")
        add(";;Name                                   Elev    Type       Stage Data       Gated")
        for node in outfalls:
            add(format_node_line(node))
        add("")

    if pumps:
        add("[PUMPS]")
        add(";;Name                                   From Node                                To Node                                  Pump Curve          Status Startup Shutoff")
        for link in pumps:
            from_node = nodes.get(link.from_node)
            startup = (from_node.max_depth * 0.70) if from_node else 0.80
            shutoff = (from_node.max_depth * 0.20) if from_node else 0.25
            add(f"{link.id:<40} {link.from_node:<40} {link.to_node:<40} {link.pump_curve:<19} OFF    {startup:<7.2f} {shutoff:<7.2f}")
        add("")

    if weirs:
        add("[WEIRS]")
        add(";;Name                                   From Node                                To Node                                  Type       CrestHt Qcoeff Gated EndCon EndCoeff Surcharge RoadWidth RoadSurf")
        for link in weirs:
            add(f"{link.id:<40} {link.from_node:<40} {link.to_node:<40} TRANSVERSE 0.40    1.84   NO    0      0.00     YES       0.00")
        add("")

    if conduits:
        add("[CONDUITS]")
        add(";;Name                                   From Node                                To Node                                  Length Roughness InOffset OutOffset InitFlow MaxFlow")
        for link in conduits:
            add(
                f"{link.id:<40} {link.from_node:<40} {link.to_node:<40} "
                f"{link.length:>6.2f} {link.roughness:<9.3f} 0.00     0.00      0.00     {link.max_flow:.2f}"
            )
        add("")

    xsection_links = conduits + weirs
    if xsection_links:
        add("[XSECTIONS]")
        add(";;Link                                   Shape      Geom1 Geom2 Geom3 Geom4 Barrels Culvert")
        for link in conduits:
            add(f"{link.id:<40} CIRCULAR   {link.diameter:<5.2f} 0.00  0.00  0.00  1       0")
        for link in weirs:
            add(f"{link.id:<40} RECT_OPEN  {link.diameter:<5.2f} 1.00  0.00  0.00  1       0")
        add("")

    if conduits:
        add("[LOSSES]")
        add(";;Link                                   InletLoss OutletLoss AverageLoss FlapGate SeepageRate")
        for link in conduits:
            add(f"{link.id:<40} {link.inlet_loss:<9.2f} {link.outlet_loss:<10.2f} {link.average_loss:<11.2f} NO       0.00")
        add("")

    if result.inflow_nodes:
        add("[INFLOWS]")
        add(";;Node                                   Constituent Time Series       Type   Mfactor Sfactor Baseline Pattern")
        for node_id, series_list in sorted(result.inflow_nodes.items()):
            for series in series_list:
                add(f"{node_id:<40} FLOW        {series:<16} FLOW   1.00    1.00     0.00")
        add("")

    if result.inflow_nodes:
        add("[TIMESERIES]")
        add(";;Name                                   Time      Value")
        all_series = {series for series_list in result.inflow_nodes.values() for series in series_list}
        if "TS_STORM_RAIN" in all_series:
            add("TS_STORM_RAIN                           00:00    0.0000")
            add("TS_STORM_RAIN                           01:00    0.0000")
        if "TS_MANHOLE_RAIN" in all_series:
            add("TS_MANHOLE_RAIN                         00:00    0.0000")
            add("TS_MANHOLE_RAIN                         01:00    0.0000")
        if "TS_SEWER_DWF" in all_series:
            add(f"TS_SEWER_DWF                            00:00    {DEFAULT_DRY_WEATHER_FLOW_CMS:.4f}")
            add(f"TS_SEWER_DWF                            01:00    {DEFAULT_DRY_WEATHER_FLOW_CMS:.4f}")
        add("")

    if pumps:
        add("[CURVES]")
        add(";;Name                                   Type   X-Value  Y-Value")
        add("DEFAULT_PUMP_CURVE                      PUMP4  0.00     0.00")
        add("DEFAULT_PUMP_CURVE                             0.50     0.25")
        add("DEFAULT_PUMP_CURVE                             1.00     0.75")
        add("DEFAULT_PUMP_CURVE                             2.00     1.50")
        add("")

    add("[REPORT]")
    add("INPUT      YES")
    add("CONTROLS   YES")
    add("NODES ALL")
    add("LINKS ALL")
    add("")
    add("[MAP]")
    min_x, min_y, max_x, max_y = result.map_dimensions
    add(f"DIMENSIONS {min_x:.2f} {min_y:.2f} {max_x:.2f} {max_y:.2f}")
    add("Units      Meters")
    add("")
    add("[COORDINATES]")
    add(";;Node                                   X-Coord          Y-Coord")
    for node in nodes.values():
        add(f"{node.id:<40} {node.map_x:<16.2f} {node.map_y:<16.2f}")
    add("")

    return "\n".join(lines)


def conversion_counts(result: ConvertResult) -> dict[str, int]:
    return {
        "junctions": sum(1 for node in result.nodes.values() if node.section == "JUNCTIONS"),
        "storages": sum(1 for node in result.nodes.values() if node.section == "STORAGE"),
        "outfalls": sum(1 for node in result.nodes.values() if node.section == "OUTFALLS"),
        "conduits": sum(1 for link in result.links if link.kind == "CONDUIT"),
        "pumps": sum(1 for link in result.links if link.kind == "PUMP"),
        "weirs": sum(1 for link in result.links if link.kind == "WEIR"),
        "inflowNodes": len(result.inflow_nodes),
        "warnings": len(result.warnings),
        "errors": len(result.errors),
    }


def render_conversion_report(result: ConvertResult, *, inp_text: str | None = None) -> dict[str, Any]:
    rainfall_targets = [
        node_id
        for node_id, series_list in sorted(result.inflow_nodes.items())
        if "TS_STORM_RAIN" in series_list or "TS_MANHOLE_RAIN" in series_list
    ]
    rainfall_target_weights = {
        node_id: (
            DEFAULT_MANHOLE_RAINFALL_FACTOR
            if "TS_MANHOLE_RAIN" in series_list and "TS_STORM_RAIN" not in series_list
            else 1.0
        )
        for node_id, series_list in sorted(result.inflow_nodes.items())
        if "TS_STORM_RAIN" in series_list or "TS_MANHOLE_RAIN" in series_list
    }
    dry_weather_targets = [
        node_id
        for node_id, series_list in sorted(result.inflow_nodes.items())
        if "TS_SEWER_DWF" in series_list
    ]
    controllable_links = [link for link in result.links if link.kind in {"CONDUIT", "PUMP", "WEIR"}]
    facility_notes = []
    for node in result.nodes.values():
        if node.source_editor_type == "facility":
            if node.source_editor_name and "월류" in node.source_editor_name:
                facility_notes.append(f"{node.id}: 월류시설은 ORIFICE + WEIR 확장 대상입니다.")
            elif node.source_editor_name and "펌프" in node.source_editor_name:
                facility_notes.append(f"{node.id}: 펌프장은 STORAGE + PUMP 확장 대상입니다.")
            elif node.source_editor_name and "물재생" in node.source_editor_name:
                facility_notes.append(f"{node.id}: 물재생센터는 STORAGE 처리시설로 변환됩니다.")

    report: dict[str, Any] = {
        "ok": len(result.errors) == 0,
        "counts": conversion_counts(result),
        "warnings": result.warnings,
        "errors": result.errors,
        "decisions": {
            "scaleMPerPx": DEFAULT_SCALE_M_PER_PX,
            "baseGroundElevationM": DEFAULT_BASE_GROUND_ELEVATION_M,
            "mapCoordinateMode": "react_layout_bounds_normalized_with_y_inversion",
            "mapDimensions": result.map_dimensions,
            "horizontalSlope": DEFAULT_HORIZONTAL_SLOPE,
            "verticalPipeMode": "drop_by_invert_elevation",
            "mainHeadInflowDetection": "name_based",
            "rainfallMode": "direct_inflow_now_subcatchments_later",
            "rainfallDefaultMmPerHour": DEFAULT_RAINFALL_MM_PER_HOUR,
            "catchmentAreaM2": DEFAULT_CATCHMENT_AREA_M2,
            "runoffCoefficient": DEFAULT_RUNOFF_COEFFICIENT,
            "manholeRainfallFactor": DEFAULT_MANHOLE_RAINFALL_FACTOR,
            "dryWeatherFlowCms": DEFAULT_DRY_WEATHER_FLOW_CMS,
            "outfallType": "FREE",
            "pipeDiametersM": PIPE_DIAMETER_M,
            "manningN": DEFAULT_MANNING_N,
            "maxBlockedManningN": MAX_BLOCKED_MANNING_N,
            "minConduitLengthM": DEFAULT_MIN_CONDUIT_LENGTH_M,
            "teeHandling": "explicit_junction_no_auto_split",
        },
        "dynamicControls": {
            "rainfallTargets": rainfall_targets,
            "rainfallTargetWeights": rainfall_target_weights,
            "dryWeatherTargets": dry_weather_targets,
            "rainfallCmsFormula": "rainfall_mm_per_hour / 1000 / 3600 * 500 * 0.8 * rainfallTargetWeight",
            "blockageRule": "Editor blockage increases Manning n in the generated model; runtime controls apply link open ratio or flow limit.",
            "blockageTargets": [
                {
                    "swmmLinkId": link.id,
                    "sourceEditorId": link.source_editor_id,
                    "sourceEditorName": link.source_editor_name,
                    "pipeKind": link.pipe_kind,
                }
                for link in controllable_links
                if link.kind == "CONDUIT" and link.source_editor_type != "relation"
            ],
        },
        "facilityNotes": facility_notes,
    }
    if inp_text is not None:
        report["inpSha256"] = hashlib.sha256(inp_text.encode("utf-8")).hexdigest()
        report["inpBytes"] = len(inp_text.encode("utf-8"))
    return report


def render_mapping_json(result: ConvertResult) -> dict[str, Any]:
    return {
        "version": 1,
        "source": "react-editor-json",
        "map": {
            "coordinateMode": "react_layout_bounds_normalized_with_y_inversion",
            "dimensions": result.map_dimensions,
            "units": "Meters",
        },
        "editorNodes": {
            editor_id: {
                "swmmNodes": sorted(set(result.editor_node_to_swmm_nodes.get(editor_id, []))),
                "swmmLinks": sorted(set(result.editor_node_to_swmm_links.get(editor_id, []))),
            }
            for editor_id in sorted(set(result.editor_node_to_swmm_nodes) | set(result.editor_node_to_swmm_links))
        },
        "editorLinks": {
            editor_id: {
                "swmmLinks": sorted(set(swmm_links)),
            }
            for editor_id, swmm_links in sorted(result.editor_link_to_swmm_links.items())
        },
        "swmmNodes": {
            node.id: {
                "section": node.section,
                "sourceEditorId": node.source_editor_id,
                "sourceEditorType": node.source_editor_type,
                "sourceEditorName": node.source_editor_name,
                "reactPoint": {"x": node.react_x, "y": node.react_y},
                "swmmCoordinate": {"x": node.map_x, "y": node.map_y},
                "invertElevation": node.elevation,
                "maxDepth": node.display_max_depth,
                "hydraulicMaxDepth": node.max_depth,
            }
            for node in result.nodes.values()
        },
        "swmmLinks": {
            link.id: {
                "kind": link.kind,
                "fromNode": link.from_node,
                "toNode": link.to_node,
                "sourceEditorId": link.source_editor_id,
                "sourceEditorType": link.source_editor_type,
                "sourceEditorName": link.source_editor_name,
                "pipeKind": link.pipe_kind,
                "length": link.length,
                "diameter": link.diameter,
                "roughness": link.roughness,
                "initialSetting": link.initial_setting,
            }
            for link in result.links
        },
    }


def print_summary(result: ConvertResult, output_path: Path) -> None:
    print(f"Wrote {output_path}")
    print(
        "summary="
        + json.dumps(
            conversion_counts(result),
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="React editor layout JSON을 SWMM .inp 골격으로 변환한다.")
    parser.add_argument("--input", "-i", required=True, help="EditorLayout JSON path, or '-' for stdin.")
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT, help=f"출력 .inp 경로. 기본값: {DEFAULT_OUTPUT}")
    parser.add_argument("--scale-m-per-px", type=float, default=0.5, help="Visual pixel to model meter scale.")
    parser.add_argument("--map-height", type=float, default=2000.0, help="SWMM map Y축 반전에 사용할 높이.")
    parser.add_argument("--title", default="React editor layout에서 생성한 SWMM model")
    args = parser.parse_args()

    try:
        layout = load_layout(args.input)
        result = convert_layout(layout, scale_m_per_px=args.scale_m_per_px, map_height=args.map_height)
        text = render_inp(result, title=args.title)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print_summary(result, args.output)
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
