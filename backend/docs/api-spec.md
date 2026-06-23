# API 명세서

## 문서 정보

- 기준일: 2026-06-23
- 기준 구현: `config/urls.py`, `apps/*/urls.py`, `apps/*/apis/*.py`, `apps/facilities/views.py`
- Base URL: `http://127.0.0.1:8000`
- Content-Type: `application/json`
- 인증: 현재 없음

## 전체 라우팅

| 구분 | Prefix | 구현 |
| --- | --- | --- |
| SWMM 엔진 | `/api/engine/` | `apps/simulation/apis/engine_api.py` |
| 에디터 변환 | `/api/editor/` | `apps/simulation/apis/editor_api.py` |
| 시나리오 | `/api/scenarios` | `apps/scenarios/apis/scenario_api.py` |
| 시설 | `/api/facilities/` | `apps/facilities/views.py` |
| 인증 | `/api/auth/` | `apps/auth/apis.py` |
| 관리자 | `/admin/` | Django admin |

`ENABLE_LEGACY_SIMULATION_API=true`일 때만 legacy API가
`/api/legacy-simulations/` 아래에 추가된다. 현재 기본 라우팅에는 예전
`/api/simulations/` API가 없다.

## 응답 형식

시설 API는 기존 공통 DTO 형식을 사용한다.

```json
{
  "code": 200,
  "message": "Facilities found.",
  "status": "OK",
  "data": []
}
```

Ninja 기반 엔진, 에디터, 시나리오 API는 `ok` 중심의 유연한 응답을 사용한다.

```json
{
  "ok": true
}
```

오류 응답은 다음 형태다.

```json
{
  "ok": false,
  "message": "오류 메시지",
  "detail": "상세 또는 객체"
}
```

## 인증과 보호 범위

로그인, 토큰 재발급, health 확인용 API를 제외한 모든 HTTP `/api` endpoint는
`Authorization: Bearer {accessToken}` 헤더가 필요하다. 현재 health 예외는
`/api/engine/health`이다.

Access token이 없거나 유효하지 않으면 실제 HTTP `401`을 반환한다.

```json
{
  "success": false,
  "httpStatus": 401,
  "status": "UNAUTHORIZED",
  "message": "Access token is invalid or expired.",
  "data": null
}
```

Refresh token은 `refresh_token` 이름의 `HttpOnly`, `Secure`,
`SameSite=Lax`, `Path=/api/auth` 쿠키로 전달한다.

## Auth API

### 로그인

- Method: `POST`
- Path: `/api/auth/login`
- 성공: `200 OK`
- 실패: `401 Unauthorized`

```json
{
  "username": "admin",
  "password": "password"
}
```

성공 시 응답 body는 다음 형태다. `Bearer ` prefix는 넣지 않는다.

```json
{
  "accessToken": "eyJ..."
}
```

동시에 `refresh_token` 쿠키가 설정된다. 현재 로그인 가능한 role은 `ADMIN`이다.

### 토큰 재발급

- Method: `POST`
- Path: `/api/auth/refresh`
- 성공: `200 OK`
- 실패: `403 Forbidden`

요청 body는 없다. 서버는 `refresh_token` 쿠키를 읽고 다음 순서로 검증한다.

1. JWT 형식과 서명
2. 만료 시간
3. `sub`의 사용자 존재 여부
4. 쿠키 refresh token hash와 DB의 `REFRESH_TOKEN` hash 일치 여부

성공하면 access token과 refresh token을 모두 재발급한다. 이전 refresh token으로
다시 요청하면 `403`을 반환하고 DB refresh token과 쿠키를 제거한다.

### 로그아웃

- Method: `POST`
- Path: `/api/auth/logout`
- 성공: `200 OK`
- 인증: access token 필요

로그아웃은 현재 사용자의 `REFRESH_TOKEN`을 `NULL`로 바꾸고 refresh cookie를
제거한다.

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

단건 객체, 배열, 또는 `facilities` 배열을 가진 객체를 받을 수 있다. 같은
`name`이 이미 있으면 새 레코드를 만들지 않고 갱신한다.

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
        "swmm_id": "CB_1",
        "anomaly_threshold": 15
      }
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `name` | string | 예 | 공백 불가, 전체 시설에서 유일 |
| `facility_type` | string | 아니요 | 기본 `OTHER` |
| `location` | string | 아니요 | 위치 설명 |
| `normal_value` | number | 아니요 | 기본 `0.0` |
| `unit` | string | 아니요 | 단위 |
| `metadata` | object | 아니요 | 확장 데이터 |

허용 시설 유형은 `DRAINAGE_PIPE`, `CATCH_BASIN`, `MANHOLE`, `PUMP`,
`OTHER`이다.

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

### 시설 삭제

- Method: `DELETE`
- Path: `/api/facilities/{facility_id}/`
- 성공: `200 OK`
- 실패: `404 Not Found`

현재 삭제는 hard delete다.

## 시나리오 API

시나리오는 React 편집모드에서 저장한 layout JSON의 서버 측 source data다.

### 시나리오 목록 조회

- Method: `GET`
- Path: `/api/scenarios`
- Query: `includeInactive=false`
- 성공: `200 OK`

```json
{
  "ok": true,
  "scenarios": []
}
```

`includeInactive=true`이면 soft delete된 시나리오도 포함한다.

### 시나리오 생성

- Method: `POST`
- Path: `/api/scenarios`
- 성공: `200 OK`
- 실패: `400 Bad Request`, `422 Unprocessable Entity`

```json
{
  "title": "기본 배수도",
  "description": "편집모드에서 저장한 기본 시나리오",
  "layoutJson": {
    "version": 1,
    "nodes": [],
    "links": []
  }
}
```

| 필드 | 타입 | 필수 | 설명 |
| --- | --- | --- | --- |
| `title` | string | 예 | 1~100자, trim 후 공백 불가 |
| `description` | string | 아니요 | 기본 빈 문자열 |
| `layoutJson` | object | 예 | React editor layout JSON |

### 시나리오 상세 조회

- Method: `GET`
- Path: `/api/scenarios/{scenario_id}`
- 성공: `200 OK`
- 실패: `404 Not Found`

### 시나리오 수정

- Method: `PUT`
- Path: `/api/scenarios/{scenario_id}`
- 성공: `200 OK`
- 실패: `400 Bad Request`, `404 Not Found`, `422 Unprocessable Entity`

```json
{
  "title": "수정된 배수도",
  "description": "설명",
  "layoutJson": {
    "version": 1,
    "nodes": [],
    "links": []
  },
  "isActive": true
}
```

`layoutJson`이 변경되면 `version`이 1 증가한다.

### 시나리오 삭제

- Method: `DELETE`
- Path: `/api/scenarios/{scenario_id}`
- 성공: `200 OK`
- 실패: `404 Not Found`

현재 삭제는 `is_active=false`로 바꾸는 soft delete다.

## SWMM 엔진 API

### 헬스 체크

- Method: `GET`
- Path: `/api/engine/health`
- 성공: `200 OK`

```json
{
  "ok": true,
  "engine": "django-swmm-engine"
}
```

### 엔진 상태 조회

- Method: `GET`
- Path: `/api/engine/status`
- 성공: `200 OK`

주요 응답 필드는 다음과 같다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `ok` | boolean | 응답 성공 여부 |
| `running` | boolean | tick loop 실행 여부 |
| `paused` | boolean | 세션이 일시정지 상태인지 여부 |
| `hasSession` | boolean | SWMM 세션 존재 여부 |
| `stepIndex` | integer | 현재 tick 번호 |
| `stepSeconds` | integer | SWMM step 간격 |
| `modelTime` | string, null | SWMM 모델 시각 |
| `control` | object | 현재 강수, 막힘, 배속 제어값 |
| `websocketClients` | integer | 연결된 WebSocket 클라이언트 수 |
| `lastError` | string, null | 마지막 런타임 오류 |

### 엔진 시작

- Method: `POST`
- Path: `/api/engine/start`
- 성공: `200 OK`
- 실패: `400 Bad Request`, `422 Unprocessable Entity`, `503 Service Unavailable`

```json
{
  "layout": {
    "version": 1,
    "groundSurfaceY": 330,
    "nodes": [],
    "links": []
  },
  "stepSeconds": 1,
  "maxRainfallMmPerHour": 100,
  "control": {
    "rainfallRatio": 0,
    "blockagesById": {},
    "speedMultiplier": 1
  }
}
```

`layout`은 필수다. 엔진은 layout을 SWMM INP로 변환해 임시 모델 파일을 만들고
PySWMM 세션을 시작한다. 변환 오류가 있으면 `422`를 반환한다.

성공 응답은 다음 필드를 포함한다.

| 필드 | 설명 |
| --- | --- |
| `ok` | 성공 여부 |
| `running` | 시작 직후 실행 여부 |
| `status` | 현재 엔진 상태 |
| `report` | layout 변환 리포트 |
| `mapping` | React editor 객체와 SWMM 객체 매핑 |
| `snapshot` | 시작 직후 snapshot |

### 엔진 리셋

- Method: `POST`
- Path: `/api/engine/reset`
- 성공: `200 OK`

요청 형식은 엔진 시작과 같다. payload가 비어 있고 마지막 시작 payload가 있으면
마지막 payload로 다시 시작한다. 둘 다 없으면 stop과 같은 상태 응답을 반환한다.

### 제어값 변경

- Method: `POST`
- Path: `/api/engine/control`
- 성공: `200 OK`
- 실패: `400 Bad Request`, `409 Conflict`

```json
{
  "rainfallRatio": 0.5,
  "maxRainfallMmPerHour": 100,
  "speedMultiplier": 2,
  "blockagesById": {
    "pipe_free_1781771017429": 0.3
  },
  "exceptions": []
}
```

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `rainfall` | number | `rainfallRatio`와 같은 입력으로 처리 |
| `rainfallRatio` | number | 0~1000 범위로 제한, 1 초과 값은 percent로 간주해 /100 |
| `rainfallPercent` | number | DTO에서 허용하지만 현재 엔진 제어에는 직접 사용하지 않음 |
| `maxRainfallMmPerHour` | number | DTO에서 허용, 시작 payload의 최대 강수량과 함께 사용 |
| `speedMultiplier` | number | 1~10 범위 |
| `blockagesById` | object | SWMM link 또는 node ID별 막힘 비율 |
| `exceptions` | array | `{ blockage, swmmLinks }` 형태의 예외 막힘 입력 |

실행 중인 세션이 없으면 `409 Conflict`를 반환한다.

### 엔진 정지, 일시정지, 재개

| 기능 | Method | Path | 주요 실패 |
| --- | --- | --- | --- |
| 정지 | `POST` | `/api/engine/stop` | - |
| 일시정지 | `POST` | `/api/engine/pause` | `409 Conflict` |
| 재개 | `POST` | `/api/engine/resume` | `409 Conflict` |

`pause`는 PySWMM 세션을 닫지 않고 tick loop만 멈춘다. `resume`은 같은 세션에서
계산을 이어간다. `stop`은 세션을 닫고 다음 시작 시 처음부터 실행한다.

## 에디터 변환 API

### 변환 검증

- Method: `POST`
- Path: `/api/editor/convert/validate`
- 성공: `200 OK`
- 실패: `400 Bad Request`, `422 Unprocessable Entity`

```json
{
  "title": "React editor SWMM model",
  "filename": "model.inp",
  "layout": {
    "version": 1,
    "nodes": [],
    "links": []
  }
}
```

성공 응답은 `ok`, `inpText`, `report`, `mapping`을 포함한다.

### INP 다운로드

- Method: `POST`
- Path: `/api/editor/export-inp`
- 성공: `200 OK`
- Content-Type: `text/plain; charset=utf-8`

응답 본문은 생성된 INP 텍스트다. `Content-Disposition` 헤더로 파일명을 전달한다.

### 변환 결과 ZIP 다운로드

- Method: `POST`
- Path: `/api/editor/convert/download`
- 성공: `200 OK`
- Content-Type: `application/zip`

ZIP에는 다음 파일이 포함된다.

| 파일 | 설명 |
| --- | --- |
| `generated_from_editor.inp` | 생성된 SWMM INP |
| `conversion-report.json` | 변환 리포트 |
| `mapping.json` | React editor와 SWMM 객체 매핑 |

## Legacy API

`legacy/apps_simulation_legacy` 아래에는 예전 `/api/simulations/` 흐름과 테스트가
남아 있다. 기본 설정에서는 URL에 등록되지 않으므로 현재 공개 API 명세에 포함하지
않는다.
