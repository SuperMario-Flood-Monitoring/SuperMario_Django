from __future__ import annotations

import tempfile
import threading
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from pyswmm import Links, Nodes, Simulation
from swmm.toolkit import solver

from .contracts import MAX_SIMULATION_SECONDS, parse_control, parse_model
from .inp_builder import build_inp
from .model_adapter import normalize_model_payload
from .output import SCHEMA_VERSION, build_step_output
from .scenario import apply_facility_conditions

ProgressCallback = Callable[[dict[str, Any]], None]


class BaseSwmmEngine(ABC):
    @abstractmethod
    def start(
        self,
        *,
        facilities: list[dict[str, Any]],
        rainfall_status: str,
        rainfall_amount: float,
        duration_minutes: int,
        parameters: dict[str, Any],
        model: dict[str, Any],
        control: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run a simulation and return JSON-serializable result data."""

    @abstractmethod
    def stop(self) -> None:
        """Request cancellation of the active simulation."""


class PySwmmEngine(BaseSwmmEngine):
    def __init__(self):
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def start(
        self,
        *,
        facilities: list[dict[str, Any]],
        rainfall_status: str,
        rainfall_amount: float,
        duration_minutes: int,
        parameters: dict[str, Any],
        model: dict[str, Any],
        control: dict[str, Any],
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A SWMM simulation is already running.")
        self._stop_event.clear()
        try:
            model = normalize_model_payload(model)
            model, control = apply_facility_conditions(
                model,
                control,
                facilities,
            )
            parsed_model = parse_model(model)
            parsed_control = parse_control(
                control,
                fallback={
                    "rainfall": rainfall_amount,
                    "step_seconds": parameters.get("step_seconds", 60),
                },
            )
            if (
                parsed_control.duration_seconds is None
                and duration_minutes * 60 > MAX_SIMULATION_SECONDS
            ):
                raise ValueError(
                    f"Simulation duration cannot exceed "
                    f"{MAX_SIMULATION_SECONDS} seconds."
                )
            built = build_inp(parsed_model, parsed_control, duration_minutes)
            return self._run(
                built=built,
                parsed_model=parsed_model,
                parsed_control=parsed_control,
                model_input_format=model.get("inputFormat", "ui-graph-v1"),
                rainfall_status=rainfall_status,
                facilities=facilities,
                progress_callback=progress_callback,
            )
        finally:
            self._lock.release()

    def _run(
        self,
        *,
        built,
        parsed_model,
        parsed_control,
        model_input_format,
        rainfall_status,
        facilities,
        progress_callback,
    ) -> dict[str, Any]:
        snapshots = []
        peak_nodes = {}
        peak_links = {}
        with tempfile.TemporaryDirectory(
            prefix="urban_flood_swmm_",
            ignore_cleanup_errors=True,
        ) as temp_dir:
            inp_path = Path(temp_dir) / "generated.inp"
            inp_path.write_text(built.content, encoding="utf-8")

            try:
                with Simulation(str(inp_path)) as simulation:
                    simulation.step_advance(parsed_control.step_seconds)
                    nodes = Nodes(simulation)
                    links = Links(simulation)
                    hydraulic_nodes = {
                        model_id: nodes[swmm_id]
                        for model_id, swmm_id in built.node_ids.items()
                        if swmm_id not in built.outfall_ids
                    }
                    hydraulic_links = {
                        model_id: links[swmm_id]
                        for model_id, swmm_id in built.link_ids.items()
                    }

                    for sequence, _ in enumerate(simulation, start=1):
                        if self._stop_event.is_set():
                            break
                        node_values = []
                        link_values = []
                        anomalies = []

                        for model_id, node in hydraulic_nodes.items():
                            max_depth = built.node_max_depths[node.nodeid]
                            depth_ratio = node.depth / max_depth if max_depth else 0.0
                            is_anomaly = node.flooding > 0 or depth_ratio >= 0.8
                            value = {
                                "id": model_id,
                                "swmm_id": node.nodeid,
                                "depth": round(node.depth, 6),
                                "head": round(node.head, 6),
                                "flooding": round(node.flooding, 6),
                                "total_inflow": round(node.total_inflow, 6),
                                "depth_ratio": round(depth_ratio, 6),
                                "is_anomaly": is_anomaly,
                            }
                            node_values.append(value)
                            previous = peak_nodes.get(model_id)
                            if previous is None or value["depth"] > previous["depth"]:
                                peak_nodes[model_id] = value
                            if is_anomaly:
                                anomalies.append({"object_type": "node", **value})

                        for model_id, link in hydraulic_links.items():
                            full_depth = max(
                                built.link_full_depths[link.linkid],
                                0.000001,
                            )
                            capacity_ratio = abs(link.depth / full_depth)
                            is_anomaly = capacity_ratio >= 0.9
                            value = {
                                "id": model_id,
                                "swmm_id": link.linkid,
                                "flow": round(link.flow, 6),
                                "depth": round(link.depth, 6),
                                "velocity": round(
                                    link.flow
                                    / max(link.ups_xsection_area, 0.000001),
                                    6,
                                ),
                                "capacity_ratio": round(capacity_ratio, 6),
                                "is_anomaly": is_anomaly,
                            }
                            link_values.append(value)
                            previous = peak_links.get(model_id)
                            if previous is None or abs(value["flow"]) > abs(
                                previous["flow"]
                            ):
                                peak_links[model_id] = value
                            if is_anomaly:
                                anomalies.append({"object_type": "link", **value})

                        snapshot = build_step_output(
                            sequence=sequence,
                            simulated_at=simulation.current_time.isoformat(),
                            percent_complete=round(
                                min(100.0, simulation.percent_complete * 100),
                                3,
                            ),
                            step_seconds=parsed_control.step_seconds,
                            rainfall_status=rainfall_status,
                            rainfall_amount=parsed_control.effective_rainfall,
                            nodes=node_values,
                            links=link_values,
                            link_blockages=built.link_blockages,
                            link_obstructions=built.link_obstructions,
                        )
                        snapshots.append(snapshot)
                        if progress_callback:
                            progress_callback(snapshot)
                        if (
                            parsed_control.realtime
                            and parsed_control.broadcast_interval_seconds > 0
                            and self._stop_event.wait(
                                parsed_control.broadcast_interval_seconds
                            )
                        ):
                            break
            except Exception:
                try:
                    solver.swmm_close()
                except Exception:
                    pass
                raise

            report_text = inp_path.with_suffix(".rpt").read_text(
                encoding="utf-8",
                errors="replace",
            )

        latest_snapshot = snapshots[-1] if snapshots else None
        final_anomalies = (
            latest_snapshot["anomalies"] if latest_snapshot else []
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "event": "simulation.completed",
            "rainfall_status": rainfall_status,
            "rainfall_amount": parsed_control.effective_rainfall,
            "step_seconds": parsed_control.step_seconds,
            "steps": len(snapshots),
            "stopped": self._stop_event.is_set(),
            "nodes": list(peak_nodes.values()),
            "links": list(peak_links.values()),
            "facilities": (
                latest_snapshot["facilities"] if latest_snapshot else []
            ),
            "anomalies": final_anomalies,
            "has_anomaly": (
                latest_snapshot["has_anomaly"]
                if latest_snapshot
                else bool(final_anomalies)
            ),
            "engine": "pyswmm",
            "engine_version": "2.1.0",
            "model_version": parsed_model.version,
            "model_input_format": model_input_format,
            "control_version": parsed_control.version,
            "report_summary": self._report_summary(report_text),
        }

    @staticmethod
    def _report_summary(report: str) -> dict[str, Any]:
        return {
            "report_generated": bool(report.strip()),
            "warnings": [
                line.strip()
                for line in report.splitlines()
                if "WARNING" in line
            ][:20],
        }

    def stop(self) -> None:
        self._stop_event.set()


_engine: BaseSwmmEngine = PySwmmEngine()


def get_engine() -> BaseSwmmEngine:
    return _engine
