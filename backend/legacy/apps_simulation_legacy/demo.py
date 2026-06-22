import json
from pathlib import Path

from django.conf import settings


def load_demo_payload(realtime: bool = True) -> dict:
    model_dir = settings.BASE_DIR / "legacy" / "swmm_engine_legacy" / "models"
    payload = {
        "rainfall_status": "HEAVY_RAIN",
        "rainfall_amount": 80,
        "duration_minutes": 1,
        "parameters": {},
        "model": json.loads(
            (Path(model_dir) / "demo_model.json").read_text(encoding="utf-8")
        ),
        "control": json.loads(
            (Path(model_dir) / "demo_control.json").read_text(encoding="utf-8")
        ),
    }
    payload["control"]["realtime"] = realtime
    return payload


def load_demo_facilities() -> list[dict]:
    return [
        {
            "name": "pipe_1",
            "facility_type": "DRAINAGE_PIPE",
            "location": "catch_basin_1 -> manhole_1",
            "normal_value": 0,
            "unit": "%",
            "metadata": {
                "swmm_id": "P_1",
                "anomaly_threshold": 90,
                "initial_water_percent": 10,
                "blockage_percent": 0,
                "obstruction_type": "",
                "description": "더미 우수관 1",
            },
        },
        {
            "name": "pipe_2",
            "facility_type": "DRAINAGE_PIPE",
            "location": "manhole_1 -> outfall_1",
            "normal_value": 0,
            "unit": "%",
            "metadata": {
                "swmm_id": "P_2",
                "anomaly_threshold": 90,
                "initial_water_percent": 5,
                "blockage_percent": 0,
                "obstruction_type": "",
                "description": "더미 우수관 2",
            },
        },
        {
            "name": "catch_basin_1",
            "facility_type": "CATCH_BASIN",
            "location": "demo catchment",
            "normal_value": 0,
            "unit": "%",
            "metadata": {
                "swmm_id": "CB_1",
                "anomaly_threshold": 80,
                "initial_water_percent": 35,
                "blockage_percent": 60,
                "obstruction_type": "LEAVES",
                "description": "더미 빗물받이",
            },
        },
        {
            "name": "manhole_1",
            "facility_type": "MANHOLE",
            "location": "demo junction",
            "normal_value": 0,
            "unit": "%",
            "metadata": {
                "swmm_id": "MH_1",
                "anomaly_threshold": 80,
                "initial_water_percent": 20,
                "blockage_percent": 0,
                "obstruction_type": "",
                "description": "더미 맨홀",
            },
        },
    ]
