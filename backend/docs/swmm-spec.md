# SWMM 출력 데이터 명세

## 문서 정보

- 기준일: 2026-06-15
- 출력 형식: JSON
- 스키마 버전: `2026-06-15-swmm-output-v1`
- 엔진: PySWMM 2.1.0 / EPA SWMM 5.2.4
- 단위계: SI

## 출력 원칙

PySWMM에서 읽은 원시 수리값은 `nodes`, `links`에 보존한다. 클라이언트가 시설
상태를 바로 표시할 수 있도록 단위와 상태 판정을 적용한 값은 `facilities`에
정규화한다. WebSocket과 최종 HTTP 응답은 JSON 객체를 사용한다.

## SWMM 입력 구조

클라이언트 화면 배치 JSON은 아직 확정되지 않았으므로 엔진은 클라이언트 원본
구조에 직접 의존하지 않는다. `swmm_engine.model_adapter.normalize_model_payload`
가 입력을 내부 그래프 계약으로 변환하고, 이후 `inp_builder`가 SWMM `.inp`
섹션을 생성한다.

현재 지원하는 입력 형식은 다음 두 가지다.

| 형식 | 설명 |
| --- | --- |
| `ui-graph-v1` | 기존 데모의 `nodes`/`links` 기반 화면 그래프 |
| `swmm-section-v1` | SWMM 섹션에 가까운 `junctions`, `outfalls`, `conduits` 기반 JSON |

SWMM 자체가 계산에 요구하는 핵심 입력은 화면 컴포넌트가 아니라 다음 수리 객체와
제어값이다.

| SWMM 섹션 | 최소 역할 |
| --- | --- |
| `[OPTIONS]` | 유량 단위, 라우팅 방식, 시작/종료 시각, 계산 간격 |
| `[RAINGAGES]`, `[TIMESERIES]` | 강우 강도 시계열 |
| `[JUNCTIONS]` | 접합 노드의 관저고, 최대 수심, 초기 수심 |
| `[OUTFALLS]` | 방류구의 관저고와 방류 조건 |
| `[CONDUITS]` | 관로의 시작/종료 노드, 길이, 조도계수, 초기 유량 |
| `[XSECTIONS]` | 관로 단면 형상과 관경 |
| `[SUBCATCHMENTS]` | 강우가 유입되는 집수면과 연결 노드 |
| `[SUBAREAS]`, `[INFILTRATION]` | 표면 저류와 침투 계수 |
| `[COORDINATES]`, `[POLYGONS]` | 결과 확인용 좌표와 집수면 형상 |

### SWMM 섹션형 JSON 예시

아래 예시는 클라이언트 배치 구조와 무관하게 SWMM 입력에 필요한 키를 한 번씩
보여주기 위한 최소 구조다.

```json
{
  "format": "swmm-section-v1",
  "version": 1,
  "junctions": [
    {
      "id": "CB_1",
      "elevation": 0.1,
      "max_depth": 1.2,
      "initial_depth": 0.2,
      "x": 100,
      "y": 100,
      "catchment": {
        "area": 1.5,
        "impervious": 80,
        "width": 120,
        "slope": 1
      }
    }
  ],
  "outfalls": [
    {
      "id": "OUT_1",
      "elevation": 0,
      "type": "FREE",
      "x": 520,
      "y": 220
    }
  ],
  "conduits": [
    {
      "id": "P_1",
      "from_node": "CB_1",
      "to_node": "OUT_1",
      "length": 180,
      "roughness": 0.013,
      "slope": 0.01,
      "blockage_percent": 0,
      "initial_water_percent": 10,
      "xsection": {
        "shape": "CIRCULAR",
        "diameter": 0.6
      }
    }
  ]
}
```

현재 구현은 원형 `CIRCULAR` 관로를 기준으로 한다. 향후 실제 SWMM 엔진
인터페이스가 별도 제공되면 어댑터만 교체하거나 추가하고, 시뮬레이션 API와
WebSocket 출력 계약은 유지하는 방향으로 확장한다.

## 단계 출력

WebSocket으로 각 계산 단계마다 다음 `data` 객체를 전송한다.

```json
{
  "schema_version": "2026-06-15-swmm-output-v1",
  "event": "simulation.step",
  "sequence": 1,
  "simulated_at": "2026-01-01T00:00:01",
  "generated_at": "2026-06-15T18:00:00+09:00",
  "interval_seconds": 1,
  "percent_complete": 10.0,
  "rainfall": {
    "status": "HEAVY_RAIN",
    "intensity": 80.0,
    "unit": "mm/hour"
  },
  "facilities": [],
  "nodes": [],
  "links": [],
  "anomalies": [],
  "has_anomaly": false
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `schema_version` | string | 출력 계약 버전 |
| `event` | string | 단계 이벤트는 `simulation.step` |
| `sequence` | integer | 1부터 증가하는 실행 내 순번 |
| `simulated_at` | ISO 8601 string | SWMM 모의 시각 |
| `generated_at` | ISO 8601 string | 서버가 메시지를 생성한 실제 시각 |
| `interval_seconds` | integer | SWMM 계산 간격 |
| `percent_complete` | number | SWMM 실행 진행률 |
| `rainfall` | object | 강수 상태, 강도, 단위 |
| `facilities` | array | 클라이언트용 정규화 시설 상태 |
| `nodes` | array | PySWMM 노드 원시값 |
| `links` | array | PySWMM 관로 원시값 |
| `anomalies` | array | 장애 판정된 정규화 시설 |
| `has_anomaly` | boolean | 장애 존재 여부 |

## 정규화 시설

```json
{
  "id": "pipe_1",
  "swmm_id": "P_1",
  "object_type": "pipe",
  "water_level": 0.08,
  "water_level_unit": "m",
  "water_level_percent": 13.33,
  "flow": 0.006,
  "flow_unit": "m3/s",
  "velocity": 0.2,
  "velocity_unit": "m/s",
  "blockage_percent": 0.0,
  "obstruction_type": "",
  "status": "NORMAL",
  "has_failure": false
}
```

노드에는 `velocity`, `velocity_unit`이 없다. 노드의 `flow`는 총 유입량이고,
관로의 `flow`는 관로 유량이다.

`obstruction_type`은 초기화 데이터에서 전달된 장애 원인이다. 값은 자유 문자열이며
데모에서는 `LEAVES`를 사용한다.

### 상태 판정

| 상태 | 조건 |
| --- | --- |
| `CRITICAL` | 침수량 > 0, 물 채움 ≥ 90%, 또는 폐색 ≥ 80% |
| `WARNING` | 물 채움 ≥ 70% 또는 폐색 ≥ 50% |
| `NORMAL` | 위 조건에 해당하지 않음 |

`has_failure`는 `CRITICAL`일 때 `true`다.

## 노드 원시 데이터

| 필드 | 단위 | PySWMM 의미 |
| --- | --- | --- |
| `depth` | m | 노드 수심 |
| `head` | m | 노드 수두 |
| `flooding` | m3/s | 지표로 월류하는 유량 |
| `total_inflow` | m3/s | 노드 총 유입량 |
| `depth_ratio` | ratio | 수심 / 최대 수심 |
| `is_anomaly` | boolean | 침수 또는 수심 80% 이상 |

## 관로 원시 데이터

| 필드 | 단위 | PySWMM 의미 |
| --- | --- | --- |
| `flow` | m3/s | 관로 유량 |
| `depth` | m | 관로 내부 수심 |
| `velocity` | m/s | 유량 / 상류 단면적 |
| `capacity_ratio` | ratio | 수심 / 유효 관경 |
| `is_anomaly` | boolean | 관로 충만도 90% 이상 |

## 최종 출력

HTTP 응답과 마지막 WebSocket 메시지는 `event=simulation.completed`를 사용한다.
`nodes`는 노드별 최대 수심 시점, `links`는 관로별 최대 절대 유량 시점의 값이며
`facilities`는 마지막 계산 단계의 시설 상태다.

```json
{
  "schema_version": "2026-06-15-swmm-output-v1",
  "event": "simulation.completed",
  "rainfall_status": "HEAVY_RAIN",
  "rainfall_amount": 80,
  "step_seconds": 1,
  "steps": 30,
  "stopped": false,
  "nodes": [
    {
      "id": "CB_1",
      "swmm_id": "CB_1",
      "depth": 0.3,
      "head": 0.4,
      "flooding": 0,
      "total_inflow": 0.01,
      "depth_ratio": 0.25,
      "is_anomaly": false
    }
  ],
  "links": [
    {
      "id": "P_1",
      "swmm_id": "P_1",
      "flow": 0.006,
      "depth": 0.08,
      "velocity": 0.2,
      "capacity_ratio": 0.13,
      "is_anomaly": false
    }
  ],
  "facilities": [],
  "anomalies": [],
  "has_anomaly": false,
  "engine": "pyswmm",
  "engine_version": "2.1.0",
  "model_version": 1,
  "model_input_format": "swmm-section-v1",
  "control_version": "2026-06-15-swmm-control-v1",
  "report_summary": {
    "report_generated": true,
    "warnings": []
  }
}
```

## JSON/CSV 변환

프로젝트의 기준 형식은 JSON이다. 표 형태로 내보낼 때 다음 유틸을 사용할 수 있다.

```python
from swmm_engine import csv_to_records, records_to_csv

csv_text = records_to_csv(step["facilities"])
facilities = csv_to_records(csv_text)
```

- `records_to_csv(list[dict]) -> str`
- `csv_to_records(str) -> list[dict[str, str]]`

CSV는 타입 정보가 없으므로 역변환 결과의 값은 문자열이다. 중첩된 dict/list는
CSV 셀 안에 JSON 문자열로 기록한다.

## LEVEL 4 더미 데이터

LEVEL 3 예시만으로는 시설 DB가 비어 있어 시뮬레이션 API가 `409 Conflict`를
반환한다. 테스트 화면의 `더미 시설 초기화` 버튼은 다음 4개 시설을 저장한다.

| 이름 | 유형 | SWMM ID | 설명 |
| --- | --- | --- | --- |
| `pipe_1` | `DRAINAGE_PIPE` | `P_1` | 빗물받이에서 맨홀로 연결 |
| `pipe_2` | `DRAINAGE_PIPE` | `P_2` | 맨홀에서 방류구로 연결 |
| `catch_basin_1` | `CATCH_BASIN` | `CB_1` | 상류 집수 시설 |
| `manhole_1` | `MANHOLE` | `MH_1` | 중간 접합 시설 |

수리 모델은 `swmm_engine/models/demo_model.json`, 제어값은
`swmm_engine/models/demo_control.json`에 있다. 데모 제어값은 강우강도
80 mm/hour, SWMM 간격 1초, 실제 방송 간격 1초, 모의 시간 30초다.

## LEVEL 5 초기 상태 변환

테스트 화면은 시설 초기화 JSON을 별도 textarea로 제공한다.

| 시설 | 초기 물 | 폐색 | 장애 원인 |
| --- | --- | --- | --- |
| `pipe_1` | 10% | 0% | 없음 |
| `pipe_2` | 5% | 0% | 없음 |
| `catch_basin_1` | 35% | 60% | `LEAVES` |
| `manhole_1` | 20% | 0% | 없음 |

- 빗물받이와 맨홀의 `initial_water_percent`는 `MaxDepth × 비율`로 계산해 SWMM
  Junction의 `InitDepth`에 기록한다.
- SWMM Conduit에는 초기 수심 필드가 없으므로 관로의 `initial_water_percent`는
  Manning 만관 유량의 비율로 계산해 `InitFlow`에 기록한다.
- 관로 `blockage_percent`는 유효 관경을 감소시킨다.
- 빗물받이 `blockage_percent`는 빗물받이에서 시작하는 모든 관로에 적용한다.
- `obstruction_type`은 물리 계산 계수는 아니며 원인 표시용 출력 데이터다.
- 모든 초기 비율은 0~100으로 제한한다.
