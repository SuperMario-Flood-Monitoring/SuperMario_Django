"""Django 엔진에서 사용하는 PySWMM 런타임 보조 함수.

이 파일은 기존 FastAPI 임시 서버가 사용하던 SWMM 보조 함수 중,
Django 패키지에서 실제 실행에 필요한 순수 함수만 분리한 모듈이다.
FastAPI, WebSocket, 기존 루트 ``scripts/`` 경로에는 의존하지 않는다.
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


FLOW_EPSILON_CMS = 0.0005
MIN_BLOCKED_FLOW_CMS = 0.000001
CONTROL_LINK_TYPES = {"ORIFICE", "WEIR", "PUMP"}


class PySwmmUnavailable(RuntimeError):
    """PySWMM이 설치되어 있지 않을 때 발생하는 엔진 실행 예외."""


_PYSWMM_IMPORT_PROBE_ERROR: str | None = None


def safe_number(value: Any, default: float = 0.0) -> float:
    """알 수 없는 SWMM/PySWMM 값을 안전하게 float로 변환한다."""

    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def safe_attr(obj: Any, name: str, default: float = 0.0) -> float:
    """PySWMM 객체 속성을 예외 없이 float로 읽는다."""

    try:
        return safe_number(getattr(obj, name), default)
    except Exception:
        return default


def build_runtime_control_model(source_model: Path, *, disable_dry_weather_inflows: bool = False) -> Path:
    """Runtime 제어를 위해 모델 내 시간시리즈 유입을 끈 임시 INP를 만든다.

    React/Django runtime은 강수량과 생활오수 유입을 tick마다 직접 주입한다.
    따라서 원본 INP의 ``TS_STORM_RAIN``은 기본적으로 0으로 만들고,
    ``disable_dry_weather_inflows``가 True이면 ``TS_SEWER_DWF``도 0으로 만든다.
    """

    lines = source_model.read_text(encoding="utf-8").splitlines()
    current: str | None = None
    output_lines: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped[1:-1].upper()
            output_lines.append(raw_line)
            continue
        if current == "INFLOWS" and stripped and not stripped.startswith(";"):
            parts = stripped.split()
            should_disable = len(parts) >= 5 and (
                parts[2] == "TS_STORM_RAIN"
                or (disable_dry_weather_inflows and parts[2] == "TS_SEWER_DWF")
            )
            if should_disable:
                parts[4] = "0.00"
                output_lines.append("{:<40} {:<11} {:<17} {:<6} {:<7} {:<8} {}".format(*parts[:7]))
                continue
        output_lines.append(raw_line)

    temp_dir = Path(tempfile.mkdtemp(prefix="swmm-django-runtime-"))
    runtime_model = temp_dir / source_model.name
    runtime_model.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return runtime_model


def full_flow_capacity_cms(link_meta: dict[str, Any]) -> float:
    """관 단면/경사/조도 기반 만관 유량을 계산한다."""

    if float(link_meta.get("maxFlowCms") or 0) > 0:
        return float(link_meta["maxFlowCms"])
    if link_meta.get("linkType") != "CONDUIT":
        return 0.0

    xsection = link_meta.get("crossSection") or {}
    shape = str(xsection.get("shape", "")).upper()
    roughness = float(link_meta.get("roughnessN") or 0.015)
    slope = max(abs(float(link_meta.get("computedSlope") or 0.0005)), 0.0001)
    geom1 = float(xsection.get("geom1") or 0)
    geom2 = float(xsection.get("geom2") or 0)

    if shape == "CIRCULAR" and geom1 > 0:
        area = math.pi * geom1 * geom1 / 4
        hydraulic_radius = geom1 / 4
    elif shape.startswith("RECT") and geom1 > 0 and geom2 > 0:
        area = geom1 * geom2
        hydraulic_radius = area / (2 * (geom1 + geom2))
    else:
        return 0.0
    return (1 / roughness) * area * (hydraulic_radius ** (2 / 3)) * math.sqrt(slope)


def full_flow_area_sqm(link_meta: dict[str, Any]) -> float:
    """관 단면의 만관 면적을 m2 단위로 계산한다."""

    xsection = link_meta.get("crossSection") or {}
    shape = str(xsection.get("shape", "")).upper()
    geom1 = float(xsection.get("geom1") or 0)
    geom2 = float(xsection.get("geom2") or 0)
    barrels = max(int(float(xsection.get("barrels") or 1)), 1)

    if shape == "CIRCULAR" and geom1 > 0:
        return math.pi * geom1 * geom1 / 4 * barrels
    if shape.startswith("RECT") and geom1 > 0 and geom2 > 0:
        return geom1 * geom2 * barrels
    return 0.0


def display_velocity_mps(link_meta: dict[str, Any], flow_cms: float, raw_velocity_mps: float) -> float:
    """PySWMM velocity가 비어 있을 때 유량/단면으로 표시용 유속을 보정한다."""

    if raw_velocity_mps > 0:
        return raw_velocity_mps
    if link_meta.get("linkType") != "CONDUIT" or abs(flow_cms) <= FLOW_EPSILON_CMS:
        return 0.0
    area = full_flow_area_sqm(link_meta)
    if area <= 0:
        return 0.0
    return abs(flow_cms) / area


def import_pyswmm() -> tuple[Any, Any, Any]:
    """PySWMM 런타임 클래스를 lazy import한다."""

    global _PYSWMM_IMPORT_PROBE_ERROR
    if _PYSWMM_IMPORT_PROBE_ERROR is None:
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pyswmm import Links, Nodes, Simulation",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if probe.returncode != 0:
            details = (probe.stderr or probe.stdout or "").strip()
            _PYSWMM_IMPORT_PROBE_ERROR = (
                f"PySWMM import 사전 확인이 종료 코드 {probe.returncode}로 실패했습니다."
                + (f" {details}" if details else "")
            )
        else:
            _PYSWMM_IMPORT_PROBE_ERROR = ""

    if _PYSWMM_IMPORT_PROBE_ERROR:
        raise PySwmmUnavailable(_PYSWMM_IMPORT_PROBE_ERROR)

    try:
        from pyswmm import Links, Nodes, Simulation
    except ModuleNotFoundError as exc:
        raise PySwmmUnavailable(
            "PySWMM이 설치되어 있지 않습니다. 먼저 `python3 -m pip install pyswmm`로 설치하세요."
        ) from exc
    except Exception as exc:
        raise PySwmmUnavailable(f"PySWMM import 실패: {exc}") from exc
    return Simulation, Nodes, Links
