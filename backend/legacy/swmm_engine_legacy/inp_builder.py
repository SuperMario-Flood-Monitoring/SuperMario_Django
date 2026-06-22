import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from .contracts import SwmmControl, SwmmModel


@dataclass(frozen=True)
class BuiltModel:
    content: str
    node_ids: dict[str, str]
    link_ids: dict[str, str]
    outfall_ids: frozenset[str]
    node_max_depths: dict[str, float]
    link_full_depths: dict[str, float]
    link_blockages: dict[str, float]
    link_obstructions: dict[str, str]


def _swmm_id(value: str, used: set[str]) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_") or "item"
    candidate = candidate[:28]
    base = candidate
    suffix = 2
    while candidate.lower() in used:
        candidate = f"{base[:24]}_{suffix}"
        suffix += 1
    used.add(candidate.lower())
    return candidate


def _format_step(seconds: int) -> str:
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_inp(
    model: SwmmModel,
    control: SwmmControl,
    duration_minutes: int,
) -> BuiltModel:
    used = set()
    node_ids = {node.id: _swmm_id(node.swmm_id, used) for node in model.nodes}
    link_ids = {link.id: _swmm_id(link.swmm_id, used) for link in model.links}
    connected_ids = {
        node_id
        for link in model.links
        for node_id in (link.from_node_id, link.to_node_id)
    }
    outgoing = {link.from_node_id for link in model.links}
    outfall_model_ids = connected_ids - outgoing
    if not outfall_model_ids:
        outfall_model_ids = {model.links[-1].to_node_id}
    outfall_ids = frozenset(node_ids[item] for item in outfall_model_ids)
    elevations = {model_id: 0.0 for model_id in connected_ids}
    for _ in range(len(connected_ids)):
        changed = False
        for link in reversed(model.links):
            required = elevations[link.to_node_id] + 0.1
            if elevations[link.from_node_id] < required:
                elevations[link.from_node_id] = required
                changed = True
        if not changed:
            break

    start = datetime(2026, 1, 1, 0, 0, 0)
    duration_seconds = (
        control.duration_seconds
        if control.duration_seconds is not None
        else max(1, duration_minutes) * 60
    )
    end = start + timedelta(seconds=duration_seconds)
    report_step = _format_step(control.step_seconds)
    rainfall = control.effective_rainfall

    junction_lines = []
    outfall_lines = []
    coordinate_lines = []
    node_max_depths = {}
    node_by_id = {node.id: node for node in model.nodes}
    for model_id in connected_ids:
        node = node_by_id[model_id]
        swmm_id = node_ids[model_id]
        max_depth = max(0.1, float(node.props.get("maxDepth", 2.0)))
        node_max_depths[swmm_id] = max_depth
        invert = float(node.props.get("invertElevation", elevations[model_id]))
        if model_id in outfall_model_ids:
            outfall_lines.append(f"{swmm_id} {invert:.3f} FREE NO")
        else:
            initial_water_percent = min(
                100.0,
                max(0.0, float(node.props.get("initialWaterPercent", 0))),
            )
            initial_depth = float(
                node.props.get(
                    "initialDepth",
                    max_depth * initial_water_percent / 100.0,
                )
            )
            initial_depth = min(max_depth, max(0.0, initial_depth))
            junction_lines.append(
                f"{swmm_id} {invert:.3f} {max_depth:.3f} "
                f"{initial_depth:.3f} 0 0"
            )
        coordinate_lines.append(f"{swmm_id} {node.x:.3f} {-node.y:.3f}")

    conduit_lines = []
    xsection_lines = []
    link_full_depths = {}
    link_blockages = {}
    link_obstructions = {}
    size_diameters = {"small": 0.3, "medium": 0.6, "large": 1.0}
    for link in model.links:
        from_node = node_by_id[link.from_node_id]
        to_node = node_by_id[link.to_node_id]
        length = max(
            1.0,
            float(
                link.props.get(
                    "length",
                    math.hypot(to_node.x - from_node.x, to_node.y - from_node.y),
                )
            ),
        )
        slope = float(link.props.get("slope", 0.01))
        roughness = max(0.001, float(link.props.get("roughness", 0.013)))
        blockage = control.blockages_by_id.get(
            link.id,
            control.blockages_by_id.get(
                link.swmm_id,
                float(link.props.get("blockage", 0)),
            ),
        )
        blockage_ratio = min(0.99, max(0.0, blockage / 100.0))
        diameter = float(
            link.props.get("diameter", size_diameters.get(link.size, 0.6))
        )
        effective_diameter = max(0.05, diameter * math.sqrt(1 - blockage_ratio))
        initial_water_percent = min(
            100.0,
            max(0.0, float(link.props.get("initialWaterPercent", 0))),
        )
        area = math.pi * effective_diameter**2 / 4
        hydraulic_radius = effective_diameter / 4
        full_flow = (
            area
            * hydraulic_radius ** (2 / 3)
            * math.sqrt(max(slope, 0.0001))
            / roughness
        )
        initial_flow = full_flow * initial_water_percent / 100
        swmm_id = link_ids[link.id]
        link_full_depths[swmm_id] = effective_diameter
        link_blockages[swmm_id] = round(blockage, 3)
        obstruction = control.obstructions_by_id.get(
            link.id,
            control.obstructions_by_id.get(link.swmm_id, ""),
        )
        link_obstructions[swmm_id] = obstruction
        conduit_lines.append(
            f"{swmm_id} {node_ids[link.from_node_id]} "
            f"{node_ids[link.to_node_id]} {length:.3f} {roughness:.4f} "
            f"0 0 {initial_flow:.6f} 0"
        )
        xsection_lines.append(
            f"{swmm_id} CIRCULAR {effective_diameter:.3f} 0 0 0 1"
        )

    source_model_ids = connected_ids - {link.to_node_id for link in model.links}
    if not source_model_ids:
        source_model_ids = {model.links[0].from_node_id}
    subcatchment_lines = []
    subarea_lines = []
    infiltration_lines = []
    polygon_lines = []
    for index, model_id in enumerate(sorted(source_model_ids), start=1):
        node = node_by_id[model_id]
        sub_id = f"SC_{index}"
        area = max(0.01, float(node.props.get("catchmentArea", 1.0)))
        impervious = min(100.0, max(0.0, float(node.props.get("impervious", 70))))
        width = max(1.0, float(node.props.get("catchmentWidth", 100)))
        catchment_slope = max(0.01, float(node.props.get("catchmentSlope", 1)))
        subcatchment_lines.append(
            f"{sub_id} RG1 {node_ids[model_id]} {area:.3f} "
            f"{impervious:.2f} {width:.3f} {catchment_slope:.3f} 0"
        )
        subarea_lines.append(f"{sub_id} 0.01 0.1 0.05 0.05 25 OUTLET 100")
        infiltration_lines.append(f"{sub_id} 75 10 4 7 0")
        x = node.x
        y = -node.y
        polygon_lines.extend(
            [
                f"{sub_id} {x - 10:.3f} {y - 10:.3f}",
                f"{sub_id} {x + 10:.3f} {y - 10:.3f}",
                f"{sub_id} {x + 10:.3f} {y + 10:.3f}",
                f"{sub_id} {x - 10:.3f} {y + 10:.3f}",
            ]
        )

    time_series_lines = []
    elapsed = 0
    while elapsed <= duration_seconds:
        current = start + timedelta(seconds=elapsed)
        time_series_lines.append(
            f"TS1 {current:%m/%d/%Y} {current:%H:%M:%S} {rainfall:.3f}"
        )
        elapsed += control.step_seconds
    if time_series_lines[-1].split()[2] != f"{end:%H:%M:%S}":
        time_series_lines.append(
            f"TS1 {end:%m/%d/%Y} {end:%H:%M:%S} {rainfall:.3f}"
        )

    content = f"""[TITLE]
Generated by intelligent urban flood backend

[OPTIONS]
FLOW_UNITS CMS
INFILTRATION HORTON
FLOW_ROUTING DYNWAVE
START_DATE {start:%m/%d/%Y}
START_TIME {start:%H:%M:%S}
REPORT_START_DATE {start:%m/%d/%Y}
REPORT_START_TIME {start:%H:%M:%S}
END_DATE {end:%m/%d/%Y}
END_TIME {end:%H:%M:%S}
SWEEP_START 01/01
SWEEP_END 12/31
DRY_DAYS 0
REPORT_STEP {report_step}
WET_STEP {report_step}
DRY_STEP {report_step}
ROUTING_STEP 00:00:01
ALLOW_PONDING YES
INERTIAL_DAMPING PARTIAL
VARIABLE_STEP 0.75
LENGTHENING_STEP 0
MIN_SURFAREA 1.167
NORMAL_FLOW_LIMITED BOTH
SKIP_STEADY_STATE NO
FORCE_MAIN_EQUATION H-W
LINK_OFFSETS DEPTH
MIN_SLOPE 0

[EVAPORATION]
CONSTANT 0
DRY_ONLY NO

[RAINGAGES]
RG1 INTENSITY {report_step} 1.0 TIMESERIES TS1

[TIMESERIES]
{chr(10).join(time_series_lines)}

[JUNCTIONS]
;;Name Elevation MaxDepth InitDepth SurDepth Aponded
{chr(10).join(junction_lines)}

[OUTFALLS]
;;Name Elevation Type StageData Gated RouteTo
{chr(10).join(outfall_lines)}

[CONDUITS]
;;Name FromNode ToNode Length Roughness InOffset OutOffset InitFlow MaxFlow
{chr(10).join(conduit_lines)}

[XSECTIONS]
;;Link Shape Geom1 Geom2 Geom3 Geom4 Barrels
{chr(10).join(xsection_lines)}

[SUBCATCHMENTS]
;;Name RainGage Outlet Area PctImperv Width Slope CurbLen
{chr(10).join(subcatchment_lines)}

[SUBAREAS]
;;Subcatchment N-Imperv N-Perv S-Imperv S-Perv PctZero RouteTo PctRouted
{chr(10).join(subarea_lines)}

[INFILTRATION]
;;Subcatchment MaxRate MinRate Decay DryTime MaxInfil
{chr(10).join(infiltration_lines)}

[COORDINATES]
;;Node X-Coord Y-Coord
{chr(10).join(coordinate_lines)}

[POLYGONS]
;;Subcatchment X-Coord Y-Coord
{chr(10).join(polygon_lines)}

[REPORT]
INPUT NO
CONTROLS NO
SUBCATCHMENTS ALL
NODES ALL
LINKS ALL
"""
    return BuiltModel(
        content=content,
        node_ids=node_ids,
        link_ids=link_ids,
        outfall_ids=outfall_ids,
        node_max_depths=node_max_depths,
        link_full_depths=link_full_depths,
        link_blockages=link_blockages,
        link_obstructions=link_obstructions,
    )
