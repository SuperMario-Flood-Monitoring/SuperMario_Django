# API 명세서

## 문서 정보

- 기준일: 2026-06-15
- Base URL: `http://localhost:8000`
- Content-Type: `application/json`
- 인증: 현재 없음

## 공통 응답

모든 API 응답은 다음 형식을 사용한다.

```json
{
  "code": 200,
  "message": "요청 처리 결과",
  "status": "OK",
  "data": {}
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `code` | integer | HTTP 상태 코드 |
| `message` | string | 처리 결과 메시지 |
| `status` | string | `OK`, `BAD_REQUEST`, `NOT_FOUND`, `CONFLICT`, `ERROR` |
| `data` | object, array, null | 실제 응답 데이터 |

## 시설 API

### 시설 목록 조회

- Method: `GET`
- Path: `/api/facilities/`
- 성공: `200 OK`

`data`는 시설 객체 배열이다.

### 시설 초기 상태 저장

- Method: `POST`
- Path: `/api/facilities/`
- 성공: `200 OK`
- 실패: `400 Bad Request`

단건 객체, 배열 또는 `facilities` 배열을 가진 객체를 받을 수 있다. 같은 `name`이
존재하면 새 레코드를 만들지 않고 초기 상태를 갱신한다.

```json
{
  "facilities": [
    {
      "name": "catch-basin-1",
      "facility_type": "CATCH_BASIN",
      "location": "A district",
      "normal_value": 10,
      "unit": "cm",
      "metadata": {
        "anomaly_threshold": 15
      }
    }
  ]
}
```

| 필드 | 타입 | 필수 | 기본값/제약 |
| --- | --- | --- | --- |
| `name` | string | 예 | 공백 불가, 전체 시설에서 유일 |
| `facility_type` | string | 아니요 | `OTHER`; 허용값은 아래 표 참조 |
| `location` | string | 아니요 | 빈 문자열 |
| `normal_value` | number | 아니요 | `0.0` |
| `unit` | string | 아니요 | 빈 문자열 |
| `metadata` | object | 아니요 | `{}` |

시설 유형은 `DRAINAGE_PIPE`, `CATCH_BASIN`, `MANHOLE`, `PUMP`, `OTHER`이다.

LEVEL 5 시뮬레이션 초기 상태는 `metadata`에 다음 필드로 저장한다.

| 필드 | 타입 | 범위 | 설명 |
| --- | --- | --- | --- |
| `swmm_id` | string | - | SWMM 모델 객체 연결 키 |
| `initial_water_percent` | number | 0~100 | 기존 물 채움 비율 |
| `blockage_percent` | number | 0~100 | 시설 폐색률 |
| `obstruction_type` | string | 자유 문자열 | `LEAVES`, `CIGARETTE_BUTTS` 등 |

### 시설 상세 조회

- Method: `GET`
- Path: `/api/facilities/{facility_id}/`
- 성공: `200 OK`
- 실패: `404 Not Found`

### 시설 수정

- Method: `PUT`
- Path: `/api/facilities/{facility_id}/`
- 성공: `200 OK`
- 실패: `400 Bad Request`, `404 Not Found`

요청 본문은 시설 초기 상태 저장의 단건 형식과 같다.

### 시설 삭제

- Method: `DELETE`
- Path: `/api/facilities/{facility_id}/`
- 성공: `200 OK`
- 실패: `404 Not Found`

## 시뮬레이션 API

### 최근 실행 목록 조회

- Method: `GET`
- Path: `/api/simulations/`
- 성공: `200 OK`
- 정렬: 최신 실행 우선
- 최대 개수: 20개

### 시뮬레이션 실행

- Method: `POST`
- Path: `/api/simulations/`
- 성공: `200 OK`
- 실패: `400 Bad Request`, `409 Conflict`, `500 Internal Server Error`

```json
{
  "rainfall_status": "HEAVY_RAIN",
  "rainfall_amount": 80,
  "duration_minutes": 30,
  "parameters": {}
}
```

| 필드 | 타입 | 필수 | 기본값/제약 |
| --- | --- | --- | --- |
| `rainfall_status` | string | 예 | 공백 불가 |
| `rainfall_amount` | number | 아니요 | `0.0`, 음수 불가 |
| `duration_minutes` | integer | 아니요 | `0`, 음수 불가 |
| `parameters` | object | 아니요 | `{}` |
| `model` | object | 예 | UI 그래프 모델 또는 SWMM 섹션형 모델 |
| `control` | object | 예 | 강수, 계산 간격, 관로 폐색 상태 |

주요 `control` 필드는 다음과 같다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `stepSeconds` | integer | SWMM 계산 간격, 1 이상 |
| `durationSeconds` | integer | 1~30, 지정 시 `duration_minutes`보다 우선 |
| `realtime` | boolean | 실제 시간 간격 방송 활성화 |
| `broadcastIntervalSeconds` | number | 단계 메시지 사이 실제 대기 시간 |
| `rainfall` | number | 강우강도 |
| `rainfallRatio` | number | 강우강도 배율 |
| `blockagesById` | object | 관로 ID별 폐색률 0~100 |

활성 시설이 없으면 `409 Conflict`를 반환한다. 성공 시 결과를 DB에 저장하고
`simulation` WebSocket 그룹으로 단계별 결과와 최종 응답을 방송한다.

`model`은 기존 `nodes`/`links` 기반 `ui-graph-v1` 또는
`junctions`/`outfalls`/`conduits` 기반 `swmm-section-v1`을 받을 수 있다.
UI 그래프 형식의 `model.nodes`는 고유한 `id`를 가져야 하며
`model.links[].from.nodeId`와 `to.nodeId`는 존재하는 노드를 참조해야 한다.
SWMM 섹션형 형식의 `conduits[].from_node`와 `to_node`도 존재하는
`junctions[].id` 또는 `outfalls[].id`를 참조해야 한다.
`control.stepSeconds`는 1 이상이고
`rainfall`, `rainfallRatio`, `blockagesById` 값은 음수가 될 수 없다.
시뮬레이션 시간은 최대 30초다.

성공 응답의 주요 결과 필드는 다음과 같다.

| 필드 | 설명 |
| --- | --- |
| `engine` | `pyswmm` |
| `model_input_format` | 정규화된 입력 형식, `ui-graph-v1` 또는 `swmm-section-v1` |
| `steps` | 완료한 SWMM 계산 단계 수 |
| `nodes` | 노드별 최대 수심 시점의 측정값 |
| `links` | 관로별 최대 절대 유량 시점의 측정값 |
| `anomalies` | 침수 또는 용량 임계값을 넘은 객체 |
| `report_summary` | SWMM report 생성 여부와 경고 목록 |

### 시뮬레이션 중지

- Method: `POST`
- Path: `/api/simulations/stop/`
- 성공: `200 OK`

`stop()`은 실행 중인 PySWMM 반복문의 중지 플래그를 설정한다.

### 엔진 테스트 화면

- Method: `GET`
- Path: `/api/simulations/demo/`
- 성공: `200 OK`

샘플 모델과 제어 JSON을 편집하고 HTTP 최종 결과 및 WebSocket 단계 결과를
브라우저에서 확인한다. `더미 시설 초기화` 버튼으로 409 오류 없이 필요한 시설
4개를 먼저 저장할 수 있다.
