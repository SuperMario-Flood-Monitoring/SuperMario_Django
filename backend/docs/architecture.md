# 전체 시스템 구조

## 문서 정보

- 기준일: 2026-06-23
- 기준 구현: `config`, `apps`, `swmm_engine`

## 범위

이 저장소는 지능형 도시침수 관리 시스템의 Django 백엔드다. React 클라이언트에서
저장한 배수도 layout JSON을 DB에 보관하고, 선택한 layout을 SWMM INP로 변환해
PySWMM 런타임 세션으로 실행한다. 엔진 snapshot은 HTTP 응답과 Channels
WebSocket으로 전달된다.

React 클라이언트와 FastAPI LangChain 서버는 외부 시스템이며 이 저장소에서
구현하지 않는다. LangChain 호출은 현재 `swmm_engine/llm_dispatcher.py`의
placeholder hook으로 남아 있고, 실제 HTTP 호출은 아직 연결되지 않았다.

## 구성도

```mermaid
flowchart LR
    Client[Vite + React Client]
    HTTP[Django HTTP / Ninja APIs]
    WS[Django Channels Consumer]
    FacilityDB[(Django ORM Facility)]
    RunDB[(Django ORM SimulationRun)]
    Engine[SWMM Engine Interface]
    PySWMM[PySWMM 2.1.0]
    INP[Generated SWMM INP]
    LangChain[FastAPI + LangChain]

    Client -->|HTTP JSON| HTTP
    Client <-->|WebSocket JSON| WS
    HTTP --> ScenarioDB
    HTTP --> FacilityDB
    HTTP --> Interface
    Interface --> Converter
    Converter --> Runtime
    Runtime --> PySWMM
    Runtime --> Risk
    Runtime --> Logs
    Runtime -->|snapshot broadcast| WS
    Risk -.->|향후 분석 요청| LLM
```

## 모듈 책임

| 경로 | 책임 |
| --- | --- |
| `config` | Django 설정, HTTP URL, ASGI HTTP/WebSocket 라우팅 |
| `apps/common` | dataclass 기반 공통 DTO |
| `apps/auth` | JWT 로그인, refresh rotation, `/api` 보호 middleware, custom users table |
| `apps/facilities` | 시설 기준값 저장용 class-based view와 모델 |
| `apps/scenarios` | React editor layout JSON 시나리오 CRUD |
| `apps/simulation` | SWMM 엔진 API, 에디터 변환 API, WebSocket consumer, 전역 엔진 상태 |
| `swmm_engine/converter` | React editor layout JSON을 SWMM INP/report/mapping으로 변환 |
| `swmm_engine/engine` | PySWMM 세션 생성, tick loop, pause/resume/stop/control 처리 |
| `swmm_engine/risk` | snapshot 구조 검증, deterministic 위험 이벤트 판정, LLM context 생성 |
| `swmm_engine/llm_dispatcher.py` | 위험 snapshot을 외부 LLM 서버로 보낼 future hook |
| `legacy` | 예전 `/api/simulations/` 흐름과 테스트 보관 |
| `backend/docs` | 현재 구현 기준 기술 문서 |

## 주요 처리 흐름

### 인증

1. 클라이언트가 `POST /api/auth/login`으로 `username`, `password`를 보낸다.
2. 서버는 custom `users` 테이블에서 ADMIN 사용자를 조회하고 Django password
   hasher로 비밀번호를 검증한다.
3. 성공 시 access token을 body로, refresh token을 `refresh_token` HttpOnly
   Secure SameSite 쿠키로 반환한다.
4. `ApiJwtAuthenticationMiddleware`는 로그인, refresh, health를 제외한 모든
   HTTP `/api` 요청에서 access token을 검증한다.
5. `POST /api/auth/refresh`는 refresh cookie와 DB에 저장된 hash를 비교하고,
   성공 시 access/refresh token을 모두 rotation한다.

### 시나리오 저장

1. React 편집모드가 `POST /api/scenarios`로 `title`, `description`,
   `layoutJson`을 보낸다.
2. `apps/scenarios`가 `Scenario` row를 생성한다.
3. 목록, 상세, 수정, 삭제 API는 같은 `layout_json`을 source data로 사용한다.
4. 삭제는 `is_active=false` soft delete다.

### 시설 초기값 저장

1. 클라이언트가 `POST /api/facilities/`로 시설 단건 또는 배열을 보낸다.
2. 서버는 `name`, `facility_type`, `normal_value`, `metadata`를 검증한다.
3. 같은 `name`이 있으면 `update_or_create`로 갱신한다.
4. 현재 SWMM 런타임 시작 경로는 시설 DB를 필수 입력으로 요구하지 않는다.

### 에디터 변환

1. 클라이언트가 `/api/editor/convert/validate` 또는 다운로드 API에 layout을 보낸다.
2. `swmm_engine.interface.convert_layout_to_inp()`가 converter를 호출한다.
3. 결과는 INP 텍스트, conversion report, React editor와 SWMM 객체 mapping이다.
4. 변환 오류가 있으면 `422`로 반환된다.

### 엔진 실행과 방송

1. 클라이언트가 `/api/ws/simulation`에 연결한다.
2. 서버는 최신 snapshot이 있으면 snapshot을, 없으면 status payload를 즉시 보낸다.
3. 클라이언트가 `POST /api/engine/start`로 `layout`, `stepSeconds`, `control`을 보낸다.
4. 런타임은 layout을 SWMM INP로 변환하고 임시 파일로 PySWMM 세션을 연다.
5. `SwmmRuntimeEngine.run_loop()`가 `stepSeconds / speedMultiplier` 간격으로 tick을 진행한다.
6. 각 tick에서 강수/막힘 제어를 적용하고 `nodes`, `links`, `editorObjects`,
   `summary`, `risk`, `llmTrigger`를 포함한 snapshot을 만든다.
7. `apps/simulation/state.py`가 snapshot을 Channels group `simulation`으로 broadcast한다.
8. snapshot은 JSONL tick log에도 기록된다.

## SWMM 교체 지점

Django 계층은 가능하면 `swmm_engine.interface`만 import한다.

| 공개 함수 | 역할 |
| --- | --- |
| `convert_layout_to_inp()` | React layout을 INP/report/mapping으로 변환 |
| `create_engine_session()` | `SwmmRuntimeEngine` 생성 |
| `start_engine()` | 새 runtime 세션 시작 |
| `apply_controls()` | 강수, 막힘, 배속 제어 변경 |
| `pause_engine()` / `resume_engine()` | tick loop 일시정지와 재개 |
| `stop_engine()` | 세션 종료 |
| `validate_snapshot()` | snapshot 구조 검증 |
| `detect_risks()` | 위험 이벤트 판정 |
| `build_llm_context()` | LLM 분석용 context 생성 |

향후 실제 SWMM 엔진 인터페이스가 별도 제공되면 `swmm_engine.interface` 뒤쪽 구현을
교체하고, Django API와 WebSocket 계약은 유지하는 방향이 적합하다.

## 런타임 상태와 제한

- 현재 엔진 세션은 `apps/simulation/state.py`의 프로세스 전역 객체 하나다.
- 다중 사용자/다중 시나리오 동시 실행을 분리하는 세션 registry는 아직 없다.
- Channel Layer는 `InMemoryChannelLayer`이므로 단일 프로세스용이다.
- 다중 인스턴스 배포 전에는 Redis Channel Layer와 세션 저장소가 필요하다.
- tick log는 `swmm_engine/logs/runtime-tick-logs/*.jsonl`에 기록되고 Git에는 포함하지 않는다.

## 배포 구조

Daphne가 ASGI 애플리케이션을 실행하며 HTTP와 WebSocket을 모두 처리한다.
Docker Compose는 PostgreSQL을 함께 실행하고 `postgres_data` 볼륨에 DB 데이터를 보존한다. Python 단독 실행은 별도 DB 환경변수가 없으면 SQLite fallback을 사용한다.

현재 채널 계층은 프로세스 메모리 기반이므로 단일 프로세스용이다. 다중 인스턴스
배포 전에는 Redis 기반 Channel Layer로 교체해야 한다.

LEVEL 5 데모는 `stepSeconds=1`, `durationSeconds=30`, `realtime=true`,
`broadcastIntervalSeconds=1`을 사용한다. SWMM 계산이 완료된 뒤 무제한으로
빠르게 방송하지 않고 중지 이벤트를 기다리는 방식으로 실제 1초 간격을 유지한다.

Docker 이미지는 PySWMM 지원 범위에 맞춰 Python 3.12 slim을 사용한다.
