# SuperMario SWMM Django 백엔드

도시 배수도 시나리오를 저장하고, React 클라이언트에서 전달한 배수도 JSON을 SWMM 런타임으로 실행하기 위한 Django 백엔드입니다.

이 레포는 **백엔드 서버**입니다. React 작업장에서 생성한 시나리오를 DB에 저장하고, Django Ninja API와 WebSocket을 통해 SWMM 엔진 상태와 1초 tick snapshot을 프론트엔드로 전달합니다. 로컬 Docker와 운영 배포는 PostgreSQL을 사용하고, Python 단독 실행은 별도 환경변수가 없으면 SQLite fallback으로 동작합니다.

## 핵심 기능

- 시나리오 관리
  - React 편집모드에서 전달한 제목, 설명, 배수도 layout JSON 저장
  - 저장된 시나리오 목록 조회
  - 시나리오 상세 조회, 수정, 삭제
  - soft delete 기반 비활성 시나리오 관리

- SWMM 엔진 실행
  - React layout JSON을 SWMM INP 모델로 변환
  - `stepSeconds: 1` 기준 실시간 엔진 세션 실행
  - 엔진 시작, 일시정지, 재개, 정지, 리셋
  - 강수량, 배속, 객체별 막힘 제어값 반영

- 실시간 전송
  - Channels WebSocket 기반 snapshot broadcast
  - 접속 직후 최신 snapshot 또는 현재 엔진 상태 전송
  - 엔진 loop가 갱신한 마지막 snapshot을 클라이언트에게 주기적으로 전달

- 에디터 변환 API
  - 배수도 JSON 변환 검증
  - INP 단일 파일 다운로드
  - INP, conversion report, mapping JSON ZIP 다운로드

## 현재 시스템 흐름

```text
React 편집모드
  -> title, description, layout_json 전송
  -> Django Scenario DB 저장
  -> React가 시나리오 목록/상세 조회

React 시뮬레이션모드
  -> 선택한 시나리오 layout + control 전송
  -> Django가 layout JSON을 SWMM 모델로 변환
  -> SWMM 런타임 세션 시작
  -> 백그라운드 run loop에서 1초 tick 진행
  -> 최신 snapshot 저장
  -> WebSocket /api/ws/simulation 으로 snapshot broadcast
```

SWMM 계산 결과의 source of truth는 서버의 `swmm_engine`입니다. React는 강수량, 막힘, 배속 같은 제어값을 전달하고, 서버 snapshot을 화면에 렌더링합니다.

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| Framework | Django 6 |
| API | Django Ninja |
| Realtime | Django Channels, Daphne |
| Database | PostgreSQL, SQLite fallback |
| SWMM Runtime | PySWMM |
| CORS | django-cors-headers |

## 실행 방법

### 0. Python 버전 확인

이 프로젝트는 `Django==6.0.6`을 사용하므로 Python 3.12 이상이 필요합니다.

```bash
python3 --version
```

### 1. 백엔드 폴더로 이동

```bash
cd backend
```

### 2. 가상환경 생성 및 활성화

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

이미 `.venv`가 준비되어 있다면 활성화만 하면 됩니다.

### 3. 의존성 설치

```bash
python -m pip install -r requirements.txt
```

### macOS에서 PySWMM import가 `Killed: 9`로 죽는 경우

macOS 로컬 가상환경에서 `swmm-toolkit`의 네이티브 라이브러리 서명/xattr이 깨지면 `from pyswmm import Simulation` 단계에서 Python 프로세스가 바로 종료될 수 있습니다. 이 경우 서버가 `/api/engine/start` 처리 중 끊기면서 브라우저에는 `Failed to fetch`처럼 보입니다.

아래 복구 스크립트로 현재 `.venv` 안의 SWMM 네이티브 파일 metadata와 ad-hoc 서명을 다시 정리합니다.

```bash
bash scripts/repair-macos-swmm-toolkit.sh
```

가상환경 경로가 다르면 인자로 넘길 수 있습니다.

```bash
bash scripts/repair-macos-swmm-toolkit.sh /path/to/.venv
```

### 4. 로컬 Docker Compose로 실행

로컬에서 Docker를 사용할 경우 backend와 PostgreSQL을 함께 실행합니다. `docker-compose.yml`은 `postgres:16-alpine` 이미지를 자동으로 pull하고, DB healthcheck가 통과한 뒤 Django가 `migrate`, 초기 ADMIN 확인 후 Daphne을 시작합니다.

```bash
docker compose up --build
```

구형 환경에서는 다음 명령을 사용할 수 있습니다.

```bash
docker-compose up --build
```

로컬 Docker 기본 DB 접속값:

```text
host: 127.0.0.1
port: 5432
database: supermario
user: supermario
password: supermario_local_password
```

호스트의 5432 포트가 이미 사용 중이면 `POSTGRES_HOST_PORT`를 바꿔 실행합니다.

```bash
POSTGRES_HOST_PORT=15432 docker compose up --build
```

### 5. Python 단독 실행 시 DB 마이그레이션

```bash
python manage.py migrate
```

Python 단독 실행은 환경변수가 없으면 `backend/db.sqlite3`를 사용합니다. 단독 실행에서도 PostgreSQL을 쓰려면 `DATABASE_ENGINE=postgres`와 `POSTGRES_*` 환경변수를 먼저 지정하세요.

### 6. Django 개발 서버 실행

```bash
python manage.py runserver 127.0.0.1:8000
```

서버 주소:

```text
http://127.0.0.1:8000/
```

### 7. 서버 상태 확인

```bash
curl http://127.0.0.1:8000/api/engine/status
```

## API

### 엔진 API

기본 공개 prefix는 `/api/engine/`입니다.

| 기능 | Method | Endpoint |
| --- | --- | --- |
| 헬스 체크 | GET | `/api/engine/health` |
| 엔진 상태 | GET | `/api/engine/status` |
| 엔진 시작 | POST | `/api/engine/start` |
| 엔진 리셋 | POST | `/api/engine/reset` |
| 제어값 변경 | POST | `/api/engine/control` |
| 엔진 정지 | POST | `/api/engine/stop` |
| 엔진 일시정지 | POST | `/api/engine/pause` |
| 엔진 재개 | POST | `/api/engine/resume` |

### 에디터 API

기본 공개 prefix는 `/api/editor/`입니다.

| 기능 | Method | Endpoint |
| --- | --- | --- |
| 변환 검증 | POST | `/api/editor/convert/validate` |
| INP 다운로드 | POST | `/api/editor/export-inp` |
| INP/report/mapping ZIP 다운로드 | POST | `/api/editor/convert/download` |

### 시나리오 API

기본 prefix는 `/api/`입니다.

| 기능 | Method | Endpoint |
| --- | --- | --- |
| 시나리오 목록 | GET | `/api/scenarios` |
| 시나리오 생성 | POST | `/api/scenarios` |
| 시나리오 상세 | GET | `/api/scenarios/{id}` |
| 시나리오 수정 | PUT | `/api/scenarios/{id}` |
| 시나리오 삭제 | DELETE | `/api/scenarios/{id}` |

비활성 시나리오까지 조회하려면 `includeInactive=true` query parameter를 사용합니다.

```text
GET /api/scenarios?includeInactive=true
```

### 웹소켓

```text
ws://127.0.0.1:8000/api/ws/simulation
```

클라이언트가 연결되면 현재 엔진 snapshot이 있으면 snapshot을, 없으면 엔진 상태 payload를 즉시 전송합니다.

1초 snapshot에는 `risk`와 `llmTrigger`가 optional로 포함됩니다. `risk.policy.level`은 `SUPERMARIO_RISK_POLICY_LEVEL` 값이며, 기본 `balanced`는 시작 직후 30 tick 동안 미세 역류를 안정화 구간으로 보고, 역류 유량/지속시간 기준을 만족한 뒤에만 WARNING/CRITICAL 위험으로 확정합니다. `llmTrigger.shouldTrigger`가 `true`인 snapshot은 `apps/simulation/state.py`의 `broadcast()`에서 `swmm_engine.llm_dispatcher.schedule_llm_analysis_dispatch()`로 전달됩니다. dispatcher는 `SUPERMARIO_LLM_ANALYZE_URL`로 `{"id": "...", "swmm_raw_data": "..."}` payload를 POST하고 `swmm_engine/logs/llm-dispatch.jsonl`에 예약/결과를 기록합니다.

LLM 전송용 context에는 `modelPath`, `runtimeModelPath`, `tickLogPath`, `rawSnapshotRef` 같은 로컬 파일 경로와 `contextExports`, `manifestPath`, `directory`, `exportKey` 같은 디버그 export 메타데이터를 포함하지 않습니다. `llm_dispatcher.sanitize_llm_context()`가 실제 전송 직전에 한 번 더 제거합니다.

### 위험 발생 순간 context 파일 저장

팀원에게 전달할 분석용 파일이 필요하면 아래 옵션을 켠 상태로 Django 서버를 실행합니다.

```bash
SUPERMARIO_RISK_PAUSE_ON_TRIGGER=true \
SUPERMARIO_RISK_EXPORT_CONTEXT_ON_TRIGGER=true \
.venv/bin/python manage.py runserver 127.0.0.1:8000 --verbosity 3
```

`llmTrigger.shouldTrigger=true`가 되는 tick에서 엔진은 자동 일시정지되고, 기본 경로 `backend/swmm_engine/logs/risk-context-exports/{runId}/step-000000-{reason}/` 아래에 다음 파일을 한 번만 저장합니다.

| 파일 | 용도 |
| --- | --- |
| `manifest.json` | runId, stepIndex, 발생 사유, 생성된 파일 목록 |
| `context-optimal.json` | LLM 알림/요약용 최소 context |
| `context-medium.json` | 주요 이상 객체와 관련 상태를 포함한 중간 크기 context |
| `context-full.json` | raw snapshot까지 포함한 디버깅용 전체 context |
| `websocket-payload.json` | React WebSocket으로 나가는 실제 snapshot payload |

저장 위치를 바꾸려면 `SUPERMARIO_RISK_CONTEXT_EXPORT_DIR=/absolute/path`를 지정합니다.

## 주요 요청 예시

### 시나리오 생성

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

### 엔진 시작

```json
{
  "layout": {
    "version": 1,
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

### 제어값 변경

```json
{
  "rainfallRatio": 0.5,
  "blockagesById": {
    "pipe_free_1781771017429": 0.3
  },
  "speedMultiplier": 2
}
```

## 프로젝트 구조

```text
SuperMario_Django/
├── backend/
│   ├── manage.py
│   ├── config/
│   │   ├── settings.py
│   │   ├── urls.py
│   │   └── asgi.py
│   ├── apps/
│   │   ├── scenarios/       # 시나리오 DB 모델, API, service
│   │   ├── simulation/      # 엔진 API, 에디터 변환 API, 웹소켓
│   │   ├── facilities/      # 기존 시설 API
│   │   └── common/          # 공통 DTO
│   ├── swmm_engine/
│   │   ├── converter/       # React layout -> SWMM INP 변환
│   │   ├── engine/          # 실시간 SWMM 런타임 세션
│   │   ├── risk/            # snapshot 이상상황 감지와 LLM context 생성
│   │   ├── llm_dispatcher.py # SuperMario_LLM 호출 hook
│   │   ├── interface.py     # Django에서 호출하는 엔진 interface
│   │   └── logs/            # runtime tick log
│   ├── legacy/              # 이전 임시 코드
│   ├── db.sqlite3
│   └── requirements.txt
└── README.md
```

## 환경 변수

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | `django-insecure-development-only` | 개발용 기본 secret key |
| `DJANGO_DEBUG` | `true` | Django debug 모드 |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | 허용 host 목록 |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | React 개발 서버 CORS 허용 origin |
| `SUPERMARIO_JWT_SECRET_KEY` | `DJANGO_SECRET_KEY` | access/refresh JWT 서명 키 |
| `SUPERMARIO_REFRESH_COOKIE_SAMESITE` | `Lax` | refresh cookie SameSite 정책 |
| `SUPERMARIO_REFRESH_COOKIE_SECURE` | local `false`, prod `true` | refresh cookie Secure 설정 |
| `SUPERMARIO_INITIAL_ADMIN_USERNAME` | `admin` | ADMIN이 없을 때 자동 생성할 초기 관리자 ID |
| `SUPERMARIO_INITIAL_ADMIN_PASSWORD` | `supermario4` | ADMIN이 없을 때 자동 생성할 초기 관리자 비밀번호 |
| `DATABASE_ENGINE` | `sqlite` | DB 엔진. `sqlite`, `postgres` 지원 |
| `SQLITE_PATH` | `backend/db.sqlite3` | SQLite DB 파일 경로 |
| `POSTGRES_DB` | `supermario` | PostgreSQL database 이름 |
| `POSTGRES_USER` | `supermario` | PostgreSQL 사용자 |
| `POSTGRES_PASSWORD` | 없음 | PostgreSQL 비밀번호. 운영에서는 반드시 Secret으로 관리 |
| `POSTGRES_HOST` | `postgres` | PostgreSQL host. Docker Compose에서는 service 이름 |
| `POSTGRES_PORT` | `5432` | PostgreSQL container 내부 포트 |
| `POSTGRES_HOST_PORT` | `5432` | 로컬 Docker Compose가 host에 노출할 PostgreSQL 포트 |
| `ENABLE_LEGACY_SIMULATION_API` | `false` | 이전 simulation API 활성화 여부 |
| `SUPERMARIO_RISK_POLICY_LEVEL` | `balanced` | 이상상황 확정 기준 레벨. `sensitive`, `balanced`, `strict` 지원 |
| `SUPERMARIO_RISK_CONTEXT_LEVEL` | `optimal` | LLM trigger payload에 직접 붙일 context 크기. `optimal`, `medium`, `full` 지원 |
| `SUPERMARIO_RISK_PAUSE_ON_TRIGGER` | `false` | 디버깅용. `true`이면 LLM trigger 발생 tick에서 엔진을 자동 일시정지 |
| `SUPERMARIO_RISK_EXPORT_CONTEXT_ON_TRIGGER` | `false` | 디버깅용. `true`이면 LLM trigger 발생 tick에서 context 파일을 레벨별로 저장 |
| `SUPERMARIO_RISK_CONTEXT_EXPORT_DIR` | `backend/swmm_engine/logs/risk-context-exports` | context 파일 저장 경로 |

> macOS 기본 `python3`가 3.9 계열이면 `Django==6.0.6` 설치가 실패합니다. 이 경우 Homebrew, pyenv 등으로 Python 3.12 이상을 준비한 뒤 가상환경을 생성해야 합니다.

## 개발 기준

- Scenario는 React에서 저장한 배수도 JSON의 서버 측 source data입니다.
- SWMM 계산 결과는 `swmm_engine` runtime snapshot을 기준으로 합니다.
- `stepSeconds: 1` 실시간 계약을 기본값으로 유지합니다.
- React layout 객체 ID와 SWMM 변환 mapping을 임의로 분리하지 않습니다.
- 엔진은 Django view에서 직접 계산하지 않고 `swmm_engine.interface`를 통해 실행합니다.
- 웹소켓 broadcast는 Channels group event를 사용하며, `swmm.message` event는 consumer의 `swmm_message` handler로 전달됩니다.
- LLM 서버 호출은 SWMM 엔진 내부가 아니라 `swmm_engine.llm_dispatcher.dispatch_llm_analysis()`에서 연결합니다.

## 검증 명령

```bash
python manage.py check
python manage.py migrate --check
```

API smoke test:

```bash
curl http://127.0.0.1:8000/api/engine/status
curl http://127.0.0.1:8000/api/scenarios
```

## 프론트엔드 연결

React 클라이언트는 별도 레포에서 실행합니다.

```text
/Users/onseoktae/Documents/Team_Supermario/SuperMario_React
```

React local 개발 기본 백엔드 주소는 `http://127.0.0.1:8000/api`입니다.
