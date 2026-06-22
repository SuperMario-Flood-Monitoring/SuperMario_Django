import csv
import io
import json
from datetime import datetime
from typing import Any

SCHEMA_VERSION = "2026-06-15-swmm-output-v1"


def facility_status(
    *,
    water_level_percent: float,
    blockage_percent: float,
    flooding: float = 0.0,
) -> tuple[str, bool]:
    has_failure = (
        flooding > 0
        or water_level_percent >= 90
        or blockage_percent >= 80
    )
    if has_failure:
        return "CRITICAL", True
    if water_level_percent >= 70 or blockage_percent >= 50:
        return "WARNING", False
    return "NORMAL", False


def build_step_output(
    *,
    sequence: int,
    simulated_at: str,
    percent_complete: float,
    step_seconds: int,
    rainfall_status: str,
    rainfall_amount: float,
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    link_blockages: dict[str, float],
    link_obstructions: dict[str, str],
) -> dict[str, Any]:
    facilities = []
    anomalies = []

    for node in nodes:
        water_level_percent = round(node["depth_ratio"] * 100, 3)
        status, has_failure = facility_status(
            water_level_percent=water_level_percent,
            blockage_percent=0,
            flooding=node["flooding"],
        )
        facility = {
            "id": node["id"],
            "swmm_id": node["swmm_id"],
            "object_type": "node",
            "water_level": node["depth"],
            "water_level_unit": "m",
            "water_level_percent": water_level_percent,
            "flow": node["total_inflow"],
            "flow_unit": "m3/s",
            "blockage_percent": 0.0,
            "status": status,
            "has_failure": has_failure,
        }
        facilities.append(facility)
        if has_failure:
            anomalies.append(facility)

    for link in links:
        blockage_percent = link_blockages.get(link["swmm_id"], 0.0)
        water_level_percent = round(link["capacity_ratio"] * 100, 3)
        status, has_failure = facility_status(
            water_level_percent=water_level_percent,
            blockage_percent=blockage_percent,
        )
        facility = {
            "id": link["id"],
            "swmm_id": link["swmm_id"],
            "object_type": "pipe",
            "water_level": link["depth"],
            "water_level_unit": "m",
            "water_level_percent": water_level_percent,
            "flow": link["flow"],
            "flow_unit": "m3/s",
            "velocity": link["velocity"],
            "velocity_unit": "m/s",
            "blockage_percent": blockage_percent,
            "obstruction_type": link_obstructions.get(link["swmm_id"], ""),
            "status": status,
            "has_failure": has_failure,
        }
        facilities.append(facility)
        if has_failure:
            anomalies.append(facility)

    return {
        "schema_version": SCHEMA_VERSION,
        "event": "simulation.step",
        "sequence": sequence,
        "simulated_at": simulated_at,
        "generated_at": datetime.now().astimezone().isoformat(),
        "interval_seconds": step_seconds,
        "percent_complete": percent_complete,
        "rainfall": {
            "status": rainfall_status,
            "intensity": rainfall_amount,
            "unit": "mm/hour",
        },
        "facilities": facilities,
        "nodes": nodes,
        "links": links,
        "anomalies": anomalies,
        "has_anomaly": bool(anomalies),
    }


def records_to_csv(records: list[dict[str, Any]]) -> str:
    if not records:
        return ""
    fieldnames = list(dict.fromkeys(key for row in records for key in row))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fieldnames)
    writer.writeheader()
    for row in records:
        writer.writerow(
            {
                key: (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                )
                for key, value in row.items()
            }
        )
    return stream.getvalue()


def csv_to_records(content: str) -> list[dict[str, str]]:
    if not content.strip():
        return []
    return list(csv.DictReader(io.StringIO(content)))
