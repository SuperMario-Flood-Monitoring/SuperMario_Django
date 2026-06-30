# 기술 문서

## 문서 정보

- 기준일: 2026-06-29
- 기준 구현: `requirements.txt`, `config`, `apps`, `swmm_engine`

## 기술 스택

| 영역 | 기술 | 현재 역할 |
| --- | --- | --- |
| Web framework | Django 6 | HTTP API, ORM, middleware, management command |
| API framework | Django Ninja | auth, notification, scenario, engine/editor 일부 API |
| Realtime | Django Channels, Daphne | WebSocket snapshot broadcast |
| Database | PostgreSQL, SQLite | Docker/운영 PostgreSQL, 로컬 단독 실행 SQLite fallback |
| SWMM runtime | PySWMM | SWMM 모델 실행과 tick 계산 |
| HTTP client | Python 표준 `urllib.request` | LLM/FastAPI 서버 POST |
| Auth | 자체 JWT 구현 | access/refresh token 발급과 검증 |
| Container | Docker Compose | Django, PostgreSQL, Nginx 배포 구성 |

## Django

이 저장소의 실제 Django 프로젝트 루트는 `backend`다. 주요 설정은
`backend/config/settings.py`, URL 라우팅은 `backend/config/urls.py`, ASGI 설정은
`backend/config/asgi.py`에 있다.

`INSTALLED_APPS`에는 인증, 시설, 시뮬레이션, 시나리오, 모니터링, 알림 앱이
포함된다. DB 스키마는 Django ORM 모델과 migration으로 관리한다.

## Django Ninja와 class-based view

현재 API는 Django Ninja와 class-based view가 함께 사용된다.

| 구현 방식 | 사용 영역 |
| --- | --- |
| Django Ninja | auth, notification, scenarios, engine/editor 일부 |
| class-based view | facilities, hazards |

응답 형식은 앱별 과거 계약을 유지한다. notification API는 `success`,
`httpStatus`, `status`, `message`, `data` 구조를 유지한다.

## Django Channels

WebSocket은 Channels를 사용한다.

- 경로: `/api/ws/simulation`
- group: `simulation`
- consumer: `apps/simulation/consumers.py`
- broadcast 상태 관리: `apps/simulation/state.py`

현재 Channel Layer는 in-memory 기반이다. 다중 프로세스 또는 다중 컨테이너 운영
전에는 Redis Channel Layer로 교체해야 한다.

## PySWMM

SWMM 실행은 `swmm_engine/engine/runtime_engine.py`가 담당한다. React layout JSON은
먼저 `swmm_engine/converter`에서 INP 파일로 변환되고, runtime engine이 임시 모델
파일을 열어 tick을 진행한다.

PySWMM 계산 결과는 Django가 표준 snapshot 형태로 정규화한다. 외부 레이어는
가능하면 `swmm_engine.interface`만 호출하도록 구성한다.

## FastAPI/LangChain 연동

FastAPI/LangChain 서버는 이 저장소 외부 시스템이다. Django가 호출하는 endpoint는
두 종류다.

| 용도 | 설정값 | 기본 URL |
| --- | --- | --- |
| 위험 분석과 문자 발송 요청 | `SUPERMARIO_LLM_ANALYZE_URL` | `SUPERMARIO_LLM_BASE_URL/analyze` |
| 조치 이력 embedding 요청 | `SUPERMARIO_LLM_MAINTENANCE_LOG_URL` | `SUPERMARIO_LLM_BASE_URL/maintenance/log/` |

Django는 LLM 분석, 보고서 문장 생성, VectorDB 저장을 직접 수행하지 않는다. 현재
역할은 SWMM 기반 데이터를 정리해 외부 서버로 POST하고, 응답 또는 실패 상태를
기록하는 것이다.

## PostgreSQL과 SQLite

`DATABASE_ENGINE=postgres`이면 PostgreSQL을 사용한다. 그 외에는 SQLite를 사용한다.

Docker Compose와 운영 배포는 PostgreSQL을 기본으로 보고, 로컬 Python 단독 실행은
환경변수가 없으면 `backend/db.sqlite3`를 사용한다.

## 로그

| 로그 | 위치 | 설명 |
| --- | --- | --- |
| runtime tick log | `swmm_engine/logs/runtime-tick-logs/*.jsonl` | tick snapshot 로그 |
| LLM dispatch log | `swmm_engine/logs/llm-dispatch.jsonl` | LLM 요청 예약/성공/실패 로그 |
| risk context export | `swmm_engine/logs/risk-context-exports/` | 디버깅 옵션 활성화 시 생성 |

로그 디렉터리는 실행 중 필요할 때 자동 생성된다. 로그 파일은 Git 추적 대상이
아니다.

## 환경변수 그룹

| 그룹 | 주요 변수 |
| --- | --- |
| Django | `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` |
| CORS | `CORS_ALLOWED_ORIGINS` |
| JWT | `SUPERMARIO_JWT_SECRET_KEY`, `SUPERMARIO_REFRESH_COOKIE_*` |
| DB | `DATABASE_ENGINE`, `POSTGRES_*`, `SQLITE_PATH` |
| LLM | `SUPERMARIO_LLM_BASE_URL`, `SUPERMARIO_LLM_ANALYZE_URL`, `SUPERMARIO_LLM_MAINTENANCE_LOG_URL` |
| 문자 정책 | `SUPERMARIO_LLM_DISPATCH_*` |
| forecast | `SUPERMARIO_FORECAST_*` |
| risk | `SUPERMARIO_RISK_*` |

비밀값은 문서와 Git에 기록하지 않고 환경변수, DB 수동 주입, 배포 Secret 등으로
관리한다.
