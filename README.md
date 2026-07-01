# SuperMario Django 백엔드

SuperMario Django 백엔드는 지능형 도시침수 관리 및 모니터링을 위한 서버입니다.
React 클라이언트가 저장한 배수도 시나리오를 DB에 보관하고, 해당 layout을 SWMM
모델로 변환해 실시간 simulation snapshot을 WebSocket으로 broadcast합니다.

이 저장소는 백엔드만 포함합니다. React 클라이언트와 FastAPI/LangChain 서버는
외부 시스템이며, Django는 두 시스템과 통신하기 위한 API, WebSocket, DB 저장,
SWMM runtime, LLM 요청 payload 생성을 담당합니다.

## 현재 핵심 기능

- JWT 기반 ADMIN 로그인, refresh token rotation, logout
- React editor layout JSON 시나리오 CRUD
- 시설 초기 기준값 저장 API
- React layout JSON -> SWMM INP 변환, 검증, 다운로드
- PySWMM 기반 runtime engine 시작, 정지, 일시정지, 재개, 제어값 변경
- Channels WebSocket 기반 simulation snapshot broadcast
- deterministic 위험 감지와 CRITICAL 위험 로그 DB 저장
- runtime memory buffer 기반 10분 뒤 위험 예측
- 침수, 역류, 막힘, node/link 상태 기반 현장 대처 우선순위 점수 계산
- LLM 서버 분석 요청 payload 생성과 문자 발송 aggregation/cooldown 정책
- Telegram bot token과 chat ID 수신자 DB 관리
- 관리자 조치 시작/완료 분리 저장
- 조치 완료 시 FastAPI/LangChain maintenance log endpoint로 구조화 payload 전송

## 시스템 흐름

```text
React
  -> Django API: 시나리오 저장, 엔진 시작, 제어값 변경, 위험 조치 입력
  -> Django WebSocket 구독

Django
  -> React layout JSON을 SWMM INP로 변환
  -> PySWMM runtime tick 실행
  -> snapshot을 WebSocket으로 broadcast
  -> CRITICAL 위험을 HazardEvent로 저장
  -> 10분 forecast와 우선순위 점수 계산
  -> LLM 분석 서버로 위험 context POST
  -> 조치 완료 시 maintenance payload POST

FastAPI/LangChain
  -> 위험 분석, 문자 발송, 조치 사례 embedding, VectorDB 저장 담당
```

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| Framework | Django 6 |
| API | Django Ninja, Django class-based view |
| Realtime | Django Channels, Daphne |
| Database | PostgreSQL, SQLite fallback |
| SWMM Runtime | PySWMM |
| Auth | 자체 JWT access/refresh token |
| 배포 | Docker Compose, Nginx |

## 프로젝트 구조

```text
SuperMario_Django/
├── backend/
│   ├── apps/
│   │   ├── auth/          # JWT 인증
│   │   ├── facilities/    # 시설 기준값 API
│   │   ├── monitoring/    # 위험 로그, forecast, 조치 이력
│   │   ├── notification/  # Telegram bot token/chat ID
│   │   ├── scenarios/     # React layout 시나리오 CRUD
│   │   └── simulation/    # 엔진 API, WebSocket, editor 변환
│   ├── config/            # Django settings, urls, asgi
│   ├── docs/              # 현재 구현 기준 문서
│   ├── swmm_engine/       # SWMM 변환, runtime, risk, LLM dispatcher
│   ├── manage.py
│   └── requirements.txt
├── docker-compose.yml
└── README.md
```

## 문서

현재 구현 기준 문서는 `backend/docs`에 있다.

| 문서 | 내용 |
| --- | --- |
| `backend/docs/api-spec.md` | HTTP API 계약 |
| `backend/docs/websocket-spec.md` | WebSocket 연결과 snapshot 구조 |
| `backend/docs/architecture.md` | 전체 시스템 구조와 통신 흐름 |
| `backend/docs/policy.md` | 인증, 위험, forecast, 우선순위, 문자 발송, 조치 정책 |
| `backend/docs/features.md` | 기능별 구현 현황 |
| `backend/docs/technology.md` | 기술 스택과 외부 연동 |
| `backend/docs/data-model.md` | DB 모델과 VectorDB 논리 모델 |
| `backend/docs/db-design.md` | Django DB 테이블 상세 |
| `backend/docs/swmm-spec.md` | SWMM 입출력, snapshot, risk 구조 |
| `backend/docs/test-result.md` | 테스트 결과와 미검증 범위 |

## 실행 방법

### Docker Compose

```bash
docker compose up --build
```

구형 환경에서는 다음 명령을 사용할 수 있다.

```bash
docker-compose up --build
```

Docker Compose는 PostgreSQL을 함께 실행하고, Django 컨테이너 시작 시 migration과
초기 ADMIN 확인을 수행한다.

### Python 단독 실행

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 127.0.0.1:8000
```

Python 단독 실행은 별도 DB 환경변수가 없으면 `backend/db.sqlite3`를 사용한다.

## 주요 API

| 기능 | Endpoint |
| --- | --- |
| 로그인 | `POST /api/auth/login` |
| 토큰 재발급 | `POST /api/auth/refresh` |
| 시나리오 CRUD | `/api/scenarios` |
| 시설 API | `/api/facilities/` |
| 엔진 API | `/api/engine/` |
| 에디터 변환 API | `/api/editor/` |
| 위험 로그 | `/api/hazards` |
| 10분 위험 예측 | `GET /api/hazards/forecast` |
| 알림 수신자 | `/api/notification/` |
| WebSocket | `/api/ws/simulation` |

로그인, refresh, `/api/engine/health`를 제외한 HTTP `/api` endpoint는
`Authorization: Bearer {accessToken}` 헤더가 필요하다.

## 주요 환경변수

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `DATABASE_ENGINE` | `sqlite` | `sqlite` 또는 `postgres` |
| `POSTGRES_DB` | `supermario` | PostgreSQL DB 이름 |
| `POSTGRES_USER` | `supermario` | PostgreSQL 사용자 |
| `POSTGRES_PASSWORD` | 없음 | PostgreSQL 비밀번호. 운영에서는 반드시 Secret으로 관리 |
| `POSTGRES_HOST` | `postgres` | PostgreSQL host. Docker Compose에서는 service 이름 |
| `POSTGRES_PORT` | `5432` | PostgreSQL container 내부 포트 |
| `POSTGRES_HOST_PORT` | `5432` | 로컬 Docker Compose가 host에 노출할 PostgreSQL 포트 |
| `ENABLE_LEGACY_SIMULATION_API` | `false` | 이전 simulation API 활성화 여부 |
| `SUPERMARIO_LLM_BASE_URL` | local `http://127.0.0.1:8001/llm` | LLM/FastAPI 서버 base URL |
| `SUPERMARIO_LLM_ANALYZE_URL` | `SUPERMARIO_LLM_BASE_URL/analyze` | 위험 context 분석 요청 URL |
| `SUPERMARIO_LLM_MAINTENANCE_LOG_URL` | `SUPERMARIO_LLM_BASE_URL/maintenance/log/` | 관리자 장애 조치 원문 전달 URL |
| `SUPERMARIO_LLM_MAINTENANCE_LOG_TIMEOUT_SECONDS` | `10` | 장애 조치 전달 요청 timeout 초 |
| `TELEGRAM_BOT_TOKEN` | 없음 | LLM 분석 결과 Telegram 발송용 bot token |
| `SUPERMARIO_FORECAST_MINUTES` | `10` | runtime state 기반 미래 위험 예측 horizon 분 |
| `SUPERMARIO_FORECAST_WINDOW_SECONDS` | `120` | 예측 증가율 계산에 사용할 최근 관측 구간 초 |
| `SUPERMARIO_FORECAST_BUFFER_SECONDS` | `900` | 메모리에 유지할 예측용 snapshot 샘플 기간 초 |
| `SUPERMARIO_SWMM_RUNTIME_DURATION_SECONDS` | `31536000` | React editor layout에서 생성하는 SWMM 런타임 모델의 실행 길이. 기본값은 365일로 1초 tick 기준 31,536,000 tick |
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
| `POSTGRES_PASSWORD` | 없음 | PostgreSQL 비밀번호 |
| `SUPERMARIO_JWT_SECRET_KEY` | `DJANGO_SECRET_KEY` | JWT 서명 키 |
| `SUPERMARIO_LLM_BASE_URL` | local `http://127.0.0.1:8001/llm` | LLM 서버 base URL |
| `SUPERMARIO_LLM_ANALYZE_URL` | `{BASE}/analyze` | 위험 분석 요청 URL |
| `SUPERMARIO_LLM_MAINTENANCE_LOG_URL` | `{BASE}/maintenance/log/` | 조치 이력 전송 URL |
| `SUPERMARIO_LLM_DISPATCH_COOLDOWN_SECONDS` | `300` | 문자 발송 cooldown |
| `SUPERMARIO_LLM_AGGREGATION_SECONDS` | `60` | 일반 위험 aggregation window |
| `SUPERMARIO_LLM_EMERGENCY_AGGREGATION_SECONDS` | `30` | 막힘/역류 emergency aggregation |
| `SUPERMARIO_FORECAST_MINUTES` | `10` | 기본 forecast horizon |
| `SUPERMARIO_FORECAST_MIN_OBSERVATION_SECONDS` | `15` | 변화량 기반 forecast 최소 관측 시간 |
| `SUPERMARIO_RISK_POLICY_LEVEL` | `balanced` | risk 감지 정책 레벨 |

비밀키, Telegram bot token, 운영 DB 비밀번호는 Git에 기록하지 않는다.

## 검증

최근 전체 테스트 기준:

```bash
cd backend
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test -v 2
```

최근 기록된 결과는 활성 테스트 56개 통과다. 상세는
`backend/docs/test-result.md`를 확인한다.
