# 운영 정책

## 문서 정보

- 기준일: 2026-06-29
- 기준 구현: `config/settings.py`, `apps/auth`, `apps/monitoring`, `apps/notification`, `swmm_engine/risk`, `swmm_engine/llm_dispatcher.py`
- 작성 기준: 기존 문자 발송 정책을 포함해 프로젝트 전체 정책으로 확장

이 문서는 현재 Django 백엔드에서 직접 판단하거나 강제하는 정책만 정리한다. React
클라이언트와 FastAPI/LangChain 서버의 내부 정책은 이 저장소에서 구현하지 않는다.

## 인증과 인가 정책

HTTP `/api` endpoint는 기본적으로 ADMIN access token을 요구한다. 예외는 로그인,
refresh, engine health API다.

| 항목 | 정책 |
| --- | --- |
| Access token | `Authorization: Bearer {token}` 헤더로 전달 |
| Refresh token | `refresh_token` HttpOnly cookie로 전달 |
| Refresh rotation | 재발급 시 access/refresh token을 모두 새로 발급 |
| 재사용 refresh token | DB hash와 일치하지 않으면 403 처리 후 refresh 제거 |
| CSRF | JWT Authorization 기반 API는 CSRF token 없이 동작 |

Refresh cookie 설정은 `config/settings.py`의
`SUPERMARIO_REFRESH_COOKIE_SAMESITE`, `SUPERMARIO_REFRESH_COOKIE_SECURE`로
조정한다.

## 위험 판정 정책

위험 판정은 `swmm_engine/risk/risk_context.py`의 deterministic rule을 기준으로
한다. 정책 레벨은 `SUPERMARIO_RISK_POLICY_LEVEL` 환경변수로 선택하며 기본값은
`balanced`다.

| 정책 레벨 | 용도 |
| --- | --- |
| `sensitive` | 작은 이상도 빠르게 표시하는 개발/민감 모드 |
| `balanced` | 기본 운영 기준 |
| `strict` | 과탐지를 줄이는 보수 모드 |

snapshot의 `risk.events`는 `NORMAL`, `WATCH`, `WARNING`, `CRITICAL` 중 하나의
severity를 가진다. DB 위험 로그와 LLM 문자 발송 후보는 `CRITICAL` 이벤트만
대상으로 한다.

## 10분 예측 정책

10분 뒤 위험 예측은 DB나 JSONL 로그가 아니라 현재 Django 프로세스의 runtime
state 메모리 buffer를 사용한다. 구현 위치는
`apps/monitoring/services/forecast_state.py`다.

| 환경변수 | 기본값 | 의미 |
| --- | ---: | --- |
| `SUPERMARIO_FORECAST_MINUTES` | 10 | 기본 예측 horizon |
| `SUPERMARIO_FORECAST_WINDOW_SECONDS` | 120 | slope 계산에 사용할 최근 관측 구간 |
| `SUPERMARIO_FORECAST_BUFFER_SECONDS` | 900 | 메모리에 유지할 snapshot 샘플 기간 |
| `SUPERMARIO_FORECAST_MIN_OBSERVATION_SECONDS` | 15 | 변화량 기반 예측에 필요한 최소 관측 시간 |

변화량 기반 metric은 최소 관측 시간과 현재값 최소 조건을 만족해야 위험 이벤트가
된다. 반면 `blockageRatio`는 React 제어에서 직접 들어오는 현재 상태이므로
관측 안정화 시간 전에도 위험 원인으로 남긴다.

| 조건 | 처리 |
| --- | --- |
| `blockageRatio >= 1.0` | `PREDICTED_BLOCKAGE_CLOSED`, `CRITICAL` |
| `0.8 <= blockageRatio < 1.0` | `PREDICTED_BLOCKAGE_HIGH`, `WARNING` |
| 변화량 기반 예측 관측 부족 | forecast는 `NORMAL`, LLM payload 미생성 |
| 현재값이 강수별 최소값 미만 | 예측값이 높아도 문자 발송 후보 제외 |

## 현장 대처 우선순위 정책

우선순위 점수는 `swmm_engine/risk/priority.py`에서 계산한다. 결과는
`priorityScore`, `priorityBand`, `priorityReasons`로 표현되며 위험 로그 API,
forecast API, LLM 분석 payload, maintenance log payload에 포함된다.

| 우선 기준 | 설명 |
| --- | --- |
| 현재 침수/월류 | `FLOODING`, `NODE_FLOODING`, `floodingCms > 0`은 최상위 |
| 역류 | `REVERSE_FLOW`, `direction=reverse`, `flowCms < 0`은 높은 우선순위 |
| 100% 막힘 | `blockageRatio >= 1.0`은 현재 수위와 별개로 높은 우선순위 |
| node 위험 | link보다 조금 높게 반영 |
| 수치 초과량 | `depthRatio`, `fullness`, `capacityRatio`, `floodingCms`, `blockageRatio` 반영 |
| 예측 속도 | `slopePerSecond`, `predictedValue - currentValue` 반영 |

`GET /api/hazards` 목록은 같은 상태 안에서 `priorityScore` 내림차순으로 정렬한다.

## 문자 발송 정책

문자 발송 요청은 `swmm_engine/llm_dispatcher.py`가 담당한다. Django는 Telegram을
직접 호출하지 않고, `SUPERMARIO_LLM_ANALYZE_URL`로 위험 context와 Telegram
전송 정보를 전달한다.

| 환경변수 | 기본값 | 의미 |
| --- | ---: | --- |
| `SUPERMARIO_LLM_DISPATCH_COOLDOWN_SECONDS` | 300 | 일반 문자 발송 후 다음 일반 발송까지 cooldown window |
| `SUPERMARIO_LLM_AGGREGATION_SECONDS` | 60 | 일반 CRITICAL 위험을 첫 발송 전에 묶는 window |
| `SUPERMARIO_LLM_EMERGENCY_AGGREGATION_SECONDS` | 30 | runtime 막힘/역류 위험을 묶는 emergency window |

일반 발송 대상은 `severity=CRITICAL`인 위험으로 제한한다. `WARNING`, `WATCH`는
화면 표시 또는 로그 기록 대상이다. 같은 위험은 아래 조합으로 식별한다.

```text
eventType + source + sourceId
```

일반 CRITICAL 위험은 aggregation window 동안 묶어 1회 전송한다. 전송 요청이
발생하면 cooldown window가 시작되며, cooldown 동안 생긴 일반 CRITICAL 위험은
pending queue에 누적한다. cooldown 종료 시 pending queue가 비어 있지 않으면
다시 하나의 요청으로 묶어 보낸다.

runtime 기준 `BLOCKAGE_CLOSED`, `REVERSE_FLOW`는 일반 cooldown 예외다. 이 둘은
emergency aggregation window에 묶어 전송한다. forecast 기준
`PREDICTED_BLOCKAGE_CLOSED`는 일반 CRITICAL forecast 위험으로 처리한다.

예약된 dispatch task는 실제 `SUPERMARIO_LLM_ANALYZE_URL` POST 직전에 현재 SWMM
엔진 상태를 다시 확인한다. 엔진이 정지되어 session이 없거나, 일시정지 상태이거나,
예약 당시 `runId`와 현재 실행 중인 `runId`가 다르면 LLM/Telegram 요청을 보내지
않고 `engine_state_skipped`로 JSONL 결과 로그에 남긴다.
상태 확인을 통과한 dispatch는 LLM POST를 보내기 직전에 현재 엔진을 자동
일시정지하고, 같은 WebSocket status payload로 React에 `paused=true` 상태를
전파한다. 이 자동 일시정지는 이미 확정된 현재 LLM 요청을 취소하지 않는다.

## LLM 분석 payload 정책

Django가 LLM 분석 서버로 보내는 기본 형태는 다음과 같다.

```json
{
  "id": "폭우",
  "swmm_raw_data": "{...sanitized context json...}",
  "TELEGRAM_BOT_TOKEN": "...",
  "TELEGRAM_CHAT_ID": ["..."]
}
```

`swmm_raw_data`는 Django가 만든 context를 JSON 문자열로 직렬화한 값이다. 전송
직전 `sanitize_llm_context()`가 로컬 파일 경로, export 경로, 디버그용 원본
snapshot 참조를 제거한다. Telegram bot token은 Django 컨테이너 `.env`의
`TELEGRAM_BOT_TOKEN`에서 읽고, chat ID 목록은 React/관리 API로 저장한
`NotificationRecipient` DB row에서 읽어 리스트로 보낸다.

## 위험 조치 이력 정책

위험 조치 흐름은 시작과 완료가 분리된다.

| 단계 | API | 처리 |
| --- | --- | --- |
| 조치 시작 | `POST /api/hazards/{hazard_id}/actions` | `HazardAction` 생성, event `IN_PROGRESS`, LLM maintenance 미전송 |
| 조치 완료 | `PATCH /api/hazards/{hazard_id}/actions/{action_id}` | 결과 저장, event `RESOLVED`, 논리 삭제, maintenance payload 전송 |

조치 완료 시점에만 `HazardCaseEmbedding` row를 만들고 FastAPI/LangChain
maintenance log endpoint로 구조화 payload를 보낸다. FastAPI 요청 실패는
조치 저장과 완료 처리를 롤백하지 않고 `fastapi_sync_status=FAILED`로 기록한다.

## 데이터 보존 정책

| 데이터 | 저장 위치 | 정책 |
| --- | --- | --- |
| 시나리오 layout | DB `Scenario.layout_json` | soft delete |
| 시설 기준값 | DB `Facility` | 현재 삭제 API는 hard delete |
| 위험 로그 | DB `HazardEvent` | 조치 완료 시 `is_deleted=true` 논리 삭제 |
| 조치 이력 | DB `HazardAction` | 위험 이벤트 FK로 보존 |
| embedding 이력 | DB `HazardCaseEmbedding` | MVP에서는 임시 `vector_id` 보존 |
| runtime tick log | `swmm_engine/logs/runtime-tick-logs/*.jsonl` | 파일 로그, Git 제외 |
| LLM dispatch log | `swmm_engine/logs/llm-dispatch.jsonl` | 파일 로그, Git 제외 |

현재 aggregation, cooldown, forecast buffer, WebSocket channel layer는 프로세스
메모리 기반이다. 컨테이너나 프로세스를 재시작하면 해당 runtime state는 초기화된다.
