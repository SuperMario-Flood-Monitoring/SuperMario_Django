"""React editor layout to SWMM INP conversion boundary.
안녕하세ㅐ요.
이 패키지는 Django 서버가 React 편집 JSON을 SWMM `.inp` 모델로 변환할 때
사용하는 내부 구현이다. 외부 Django view/worker에서는 가능하면 이 패키지를
직접 import하지 않고 `swmm_engine.interface.convert_layout_to_inp()`를 사용한다.
"""

from .editor_layout_to_swmm_inp import (
    ConversionError,
    DEFAULT_BASE_GROUND_ELEVATION_M,
    DEFAULT_CATCHMENT_AREA_M2,
    DEFAULT_DRY_WEATHER_FLOW_CMS,
    DEFAULT_HORIZONTAL_SLOPE,
    DEFAULT_MANHOLE_RAINFALL_FACTOR,
    DEFAULT_RUNOFF_COEFFICIENT,
    DEFAULT_SCALE_M_PER_PX,
    convert_layout,
    render_conversion_report,
    render_inp,
    render_mapping_json,
)

__all__ = [
    "ConversionError",
    "DEFAULT_BASE_GROUND_ELEVATION_M",
    "DEFAULT_CATCHMENT_AREA_M2",
    "DEFAULT_DRY_WEATHER_FLOW_CMS",
    "DEFAULT_HORIZONTAL_SLOPE",
    "DEFAULT_MANHOLE_RAINFALL_FACTOR",
    "DEFAULT_RUNOFF_COEFFICIENT",
    "DEFAULT_SCALE_M_PER_PX",
    "convert_layout",
    "render_conversion_report",
    "render_inp",
    "render_mapping_json",
]
