"""Django 서버에서 사용할 SWMM 공개 인터페이스.

이 파일의 목적은 Django view, Django REST Framework viewset, Channels
consumer, Celery/background worker가 SWMM 내부 구현 파일을 직접 import하지
않도록 막는 것이다.

장고 쪽에서는 가능하면 아래처럼 이 파일만 import한다.

    from swmm.interface import (
        convert_layout_to_inp,
        create_engine_session,
        start_engine,
        pause_engine,
        resume_engine,
        stop_engine,
        apply_controls,
        get_latest_snapshot,
        validate_snapshot,
        detect_risks,
        build_llm_context,
    )

현재 구현 상태:
- React editor JSON -> SWMM INP 변환은 `swmm.converter` 내부 구현을 감싼다.
- PySWMM 실행/세션 제어는 `swmm.engine` 내부 구현을 감싼다.
- 위험 감지와 LLM context 생성은 `swmm.risk` 내부 구현을 감싼다.

즉, Django 서버는 FastAPI 임시 서버나 루트 scripts 경로를 직접 import하지
않고 이 interface import만 유지하면 된다.
"""

from __future__ import annotations

from typing import Any, Literal


from swmm.converter import (  # noqa: E402
    ConversionError,
    DEFAULT_BASE_GROUND_ELEVATION_M,
    DEFAULT_SCALE_M_PER_PX,
    convert_layout,
    render_conversion_report,
    render_inp,
    render_mapping_json,
)
from swmm.engine import SwmmRuntimeEngine  # noqa: E402
from swmm.risk import (  # noqa: E402
    build_swmm_context_packet,
    evaluate_swmm_risk,
    validate_swmm_snapshot,
)


ContextLevel = Literal["optimal", "medium", "full"]
"""LLM/LangChain 서버로 보낼 SWMM context 크기.

- optimal: 알림/요약에 필요한 최소 위험 이벤트와 전역 요약 중심.
- medium: optimal + 주요 비정상 node/link/editor object와 관련 상태.
- full: medium + raw snapshot 전체. 비용은 크지만 디버깅에는 가장 풍부하다.
"""


def convert_layout_to_inp(
    layout: dict[str, Any],
    *,
    title: str = "React editor SWMM model",
    scale_m_per_px: float = DEFAULT_SCALE_M_PER_PX,
    map_height: float = 2000.0,
    base_ground_elevation_m: float = DEFAULT_BASE_GROUND_ELEVATION_M,
) -> dict[str, Any]:
    """React 편집 JSON을 SWMM INP 텍스트와 매핑 정보로 변환한다.

    Django에서 쓰는 위치:
    - 사용자가 저장한 React editor layout JSON을 DB에서 꺼낸 뒤,
      시뮬레이션 시작 전에 SWMM이 읽을 `.inp` 모델로 변환할 때 사용한다.
    - UI에서 "INP 다운로드" 또는 "모델 변환 검증" API를 만들 때도 같은
      함수 하나를 사용하면 된다.

    입력:
    - layout: React 편집모드가 저장한 전체 layout JSON.
    - title: 생성될 INP의 [TITLE]에 들어갈 설명.
    - scale_m_per_px: React 좌표 1px을 몇 m로 해석할지 정하는 값.
      기본값은 기존 converter의 DEFAULT_SCALE_M_PER_PX를 따른다.
    - map_height: SWMM map 좌표 변환에 쓰는 fallback 높이.
    - base_ground_elevation_m: 지표면 기준 고도.

    반환:
    - ok: 변환 성공 여부.
    - inpText: SWMM GUI/PySWMM이 읽을 수 있는 INP 문자열.
    - report: 변환 요약, warnings/errors, 변환된 node/link 수.
    - mapping: React editor object와 SWMM node/link 사이의 매핑 JSON.

    예외:
    - layout 구조가 잘못되거나 SWMM 모델로 변환할 수 없으면
      ConversionError가 발생할 수 있다.
    """

    result = convert_layout(
        layout,
        scale_m_per_px=scale_m_per_px,
        map_height=map_height,
        base_ground_elevation_m=base_ground_elevation_m,
    )
    inp_text = render_inp(result, title=title)
    return {
        "ok": True,
        "inpText": inp_text,
        "report": render_conversion_report(result, inp_text=inp_text),
        "mapping": render_mapping_json(result),
    }


def validate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """SWMM runtime snapshot이 위험 감지/LLM 분석에 쓸 수 있는 형태인지 검증한다.

    Django에서 쓰는 위치:
    - 엔진에서 1초 tick 결과를 받은 직후.
    - Channels WebSocket으로 React에 보내기 전.
    - LangChain 서버로 위험 context를 보내기 전.

    검증하는 것:
    - nodes, links, editorObjects, summary 기본 키 존재 여부.
    - depthRatio, floodingCms, flowCms, fullness, capacityRatio 등 주요 수치
      필드가 숫자인지 여부.
    - link direction이 forward/reverse 범위인지 여부.

    반환:
    - ok: 치명적 오류가 없으면 True.
    - errors: 후속 처리 전에 막아야 할 구조 오류.
    - warnings: 이상하지만 처리는 가능한 값.
    - counts: snapshot에 들어있는 node/link/editor object 개수.
    """

    return validate_swmm_snapshot(snapshot)


def detect_risks(
    snapshot: dict[str, Any],
    previous_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """SWMM snapshot에서 위험 이벤트를 deterministic rule로 감지한다.

    Django에서 쓰는 위치:
    - 매 tick마다 snapshot을 받은 뒤 즉시 호출.
    - 결과의 highestSeverity/events를 보고 사용자 알림, DB 기록,
      LangChain 분석 요청 여부를 결정한다.

    감지 기준:
    - flooding: SWMM node floodingCms가 0보다 큰 경우.
    - reverse flow: link flowCms가 음수이거나 direction이 reverse인 경우.
    - surcharge/full pipe: node depthRatio 또는 link fullness가 1.0 이상.
    - high fill: depthRatio/fullness가 0.7 이상.
    - capacity exceeded: capacityRatio가 1.0 이상.
    - blockage: blockageRatio가 0.5 이상.

    previous_state:
    - 이전 tick의 risk result를 넘기면 reverseTicks 같은 연속 발생 카운터를
      이어서 계산한다.
    - 첫 tick이나 단발 분석에서는 None으로 두면 된다.

    반환:
    - highestSeverity: NORMAL/WATCH/WARNING/CRITICAL 중 최고 위험도.
    - events: 위험 이벤트 목록.
    - summary: 이벤트 타입/위험도별 집계.
    - validation: validate_snapshot 결과.
    - counters: 다음 tick에 넘길 연속 발생 카운터.
    """

    return evaluate_swmm_risk(snapshot, previous_state=previous_state)


def build_llm_context(
    snapshot: dict[str, Any],
    risk_result: dict[str, Any] | None = None,
    *,
    context_level: ContextLevel = "optimal",
    weather: dict[str, Any] | None = None,
    system_meta: dict[str, Any] | None = None,
    raw_snapshot_ref: str | None = None,
) -> dict[str, Any]:
    """LLM/LangChain 서버로 넘길 SWMM 분석 context를 만든다.

    Django에서 쓰는 위치:
    - detect_risks 결과에서 WARNING/CRITICAL이 발생했을 때.
    - LangChain 서버에 "현재 상황 요약/원인 추정/대응 안내"를 요청하기 전.

    중요한 원칙:
    - LLM이 수치 기반 위험 판정을 새로 하게 만들지 않는다.
    - 위험 판정은 detect_risks가 deterministic하게 끝낸다.
    - LLM에는 위험 이벤트, 관련 객체, 전체 상태 요약, 날씨/강수량,
      시스템 프롬프트에 필요한 context만 넘긴다.

    context_level:
    - optimal: 운영 알림용. 위험 이벤트와 요약 중심이라 가장 작다.
    - medium: 분석용 기본값. 주요 비정상 객체와 관련 상태를 포함한다.
    - full: 디버깅용. raw nodes/links/editorObjects까지 포함한다.

    weather:
    - 외부 날씨 API나 현재 강수량 설정값을 넣는 곳.
    - 예: {"rainfallMmPerHour": 80, "source": "KMA"}.

    system_meta:
    - 서비스명, 사용자/현장 ID, 모델 ID, 알림 정책 등 앱 레벨 metadata.

    raw_snapshot_ref:
    - tick log 파일 경로, DB row id, object storage key처럼 raw snapshot을
      나중에 다시 찾을 수 있는 참조값.
    """

    return build_swmm_context_packet(
        snapshot,
        risk_result,
        context_level=context_level,
        weather=weather,
        system_meta=system_meta,
        raw_snapshot_ref=raw_snapshot_ref,
    )


def create_engine_session() -> Any:
    """PySWMM 런타임 엔진 세션 객체를 생성한다.

    Django에서 쓰는 위치:
    - Django Channels consumer가 WebSocket 연결 단위로 엔진을 하나 들고 있을 때.
    - Celery/background worker가 특정 모델 시뮬레이션 job 단위로 엔진을 만들 때.
    - DRF API에서 "시뮬레이션 시작" 요청을 받아 세션 registry에 저장할 때.

    반환:
    - `swmm.engine.SwmmRuntimeEngine` 인스턴스.
    - 이 객체는 async start/pause/resume/stop/update_controls 메서드를 가진다.

    주의:
    - 모델/layout 자체를 바꾸려면 기존 엔진 세션을 stop하고 새로 start해야 한다.
    - 강수량/막힘/속도 같은 runtime control은 apply_controls로 바꿀 수 있다.
    - HTTP/WebSocket 라우팅은 Django/DRF/Channels 계층에서 직접 만든다.
    """

    return SwmmRuntimeEngine()


async def start_engine(engine: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """SWMM 엔진을 시작한다.

    Django에서 쓰는 위치:
    - 사용자가 시뮬레이션할 저장 모델을 선택하고 "엔진 시작"을 누른 시점.

    payload 예상 구조:
    - layout: React editor layout JSON.
    - stepSeconds: 기본 1.
    - control:
        - rainfallRatio 또는 rainfallPercent.
        - blockagesById.
        - speedMultiplier.

    반환:
    - status: 엔진 상태.
    - report/mapping: 변환 결과.
    - snapshot: 시작 직후 SWMM 상태.

    주의:
    - 이미 실행 중인 모델을 다른 layout으로 교체하려면 stop 후 새 payload로
      start해야 한다.
    """

    return await engine.start(payload)


async def pause_engine(engine: Any) -> dict[str, Any]:
    """엔진 계산 loop만 일시정지한다.

    Django에서 쓰는 위치:
    - 사용자가 현재 상태를 멈춰놓고 특정 관/시설 정보를 확인할 때.

    특징:
    - PySWMM 세션을 닫지 않는다.
    - resume_engine으로 같은 상태에서 이어갈 수 있다.
    - stop_engine과 달리 재시작 초기화가 아니다.
    """

    return await engine.pause()


async def resume_engine(engine: Any) -> dict[str, Any]:
    """일시정지된 엔진을 다시 진행한다.

    Django에서 쓰는 위치:
    - pause_engine 이후 사용자가 "재개"를 누른 시점.

    반환:
    - 재개 직후 status/snapshot 정보.
    """

    return await engine.resume()


async def stop_engine(engine: Any) -> dict[str, Any]:
    """엔진을 완전히 정지하고 PySWMM 세션을 닫는다.

    Django에서 쓰는 위치:
    - 사용자가 시뮬레이션을 종료할 때.
    - 다른 모델/layout으로 갈아타기 전.
    - 서버 job cleanup 시점.

    특징:
    - stop 후 다시 start하면 시뮬레이션은 처음부터 시작된다.
    - 현재 tick 상태를 이어가려면 stop이 아니라 pause/resume을 사용해야 한다.
    """

    return await engine.stop()


async def apply_controls(engine: Any, control: dict[str, Any]) -> dict[str, Any]:
    """실행 중인 엔진에 강수량/막힘/속도 제어값을 적용한다.

    Django에서 쓰는 위치:
    - React에서 강수량 slider를 움직였을 때.
    - 특정 관/시설 막힘 slider를 적용했을 때.
    - 시뮬레이션 속도 배수를 변경했을 때.

    control 예상 구조:
    - rainfallRatio: 0~1000 같은 비율 값. 현재 프로젝트는 극단 테스트를 위해
      큰 배율을 허용한다.
    - blockagesById: {"SWMM_LINK_ID": 0~100}.
    - speedMultiplier: 1, 2, 3, 4, 10 등.

    반환:
    - control 적용 후 최신 snapshot.

    주의:
    - layout/model 구조 자체를 바꾸는 함수가 아니다.
    - 모델 구조 변경은 stop_engine 후 start_engine을 다시 호출해야 한다.
    """

    return await engine.update_controls(control)


def get_latest_snapshot(engine: Any) -> dict[str, Any] | None:
    """현재 엔진 세션의 마지막 snapshot을 가져온다.

    Django에서 쓰는 위치:
    - WebSocket 클라이언트가 늦게 접속했을 때 최근 상태를 즉시 보내기.
    - 위험 감지/LLM 분석을 특정 시점에 수동 재실행하기.
    - API에서 현재 상태 확인 endpoint를 만들기.

    반환:
    - 세션이 있으면 마지막 snapshot dict.
    - 아직 엔진이 시작되지 않았거나 snapshot이 없으면 None.
    """

    session = getattr(engine, "session", None)
    if session is None:
        return None
    return getattr(session, "last_snapshot", None)


__all__ = [
    "ConversionError",
    "apply_controls",
    "build_llm_context",
    "convert_layout_to_inp",
    "create_engine_session",
    "detect_risks",
    "get_latest_snapshot",
    "pause_engine",
    "resume_engine",
    "start_engine",
    "stop_engine",
    "validate_snapshot",
]
