# 기능 문서

## 문서 정보

- 기준일: 2026-06-29
- 기준 구현: `apps/*`, `swmm_engine/*`

이 문서는 현재 Django 백엔드에서 구현된 기능을 기준으로 정리한다. React
클라이언트와 FastAPI/LangChain 서버는 외부 시스템이며, 이 저장소에서는 해당
시스템으로 보낼 데이터와 연동 지점만 구현한다.

## 인증

`apps/auth`는 custom `users` 테이블 기반 JWT 인증을 제공한다.

- `POST /api/auth/login`: ADMIN 사용자 로그인
- `POST /api/auth/refresh`: refresh cookie 기반 access/refresh token rotation
- `POST /api/auth/logout`: DB refresh token 제거와 refresh cookie 삭제
- `ensure_admin_user`: 초기 ADMIN 계정 생성 또는 기본 admin row 갱신

HTTP `/api` 보호 범위는 `ApiJwtAuthenticationMiddleware`가 담당한다.

## 시나리오 CRUD

`apps/scenarios`는 React 편집모드에서 만든 배수도 layout JSON을 저장한다.

- 시나리오 생성, 목록, 상세, 수정, 삭제
- 삭제는 `is_active=false` soft delete
- `includeInactive=true`로 비활성 시나리오 포함 조회
- layout 변경 시 version 증가

시나리오 데이터는 SWMM 실행을 위한 원본 layout source data로 사용된다.

## 시설 기준값 관리

`apps/facilities`는 초기화 API에서 받은 시설 기준값을 저장한다.

- 시설 목록, 생성/초기화, 상세, 수정, 삭제
- 같은 `name`이면 `update_or_create`로 갱신
- `metadata`에 SWMM ID나 확장 기준값을 담을 수 있음

현재 SWMM runtime 시작 경로는 시설 DB를 필수 입력으로 요구하지 않는다.

## 에디터 변환

`apps/simulation`과 `swmm_engine/converter`는 React layout JSON을 SWMM INP로
변환한다.

- 변환 검증
- INP 단일 파일 다운로드
- INP, conversion report, mapping JSON ZIP 다운로드
- React editor 객체와 SWMM node/link mapping 생성

변환 실패는 `422`로 반환된다.

## SWMM 엔진 실행

`swmm_engine/engine`은 PySWMM 기반 runtime session을 실행한다.

- 엔진 시작, 리셋, 정지
- 일시정지와 재개
- 강수량, 배속, 객체별 막힘 제어 적용
- tick마다 node/link/editor object snapshot 생성
- `sourceOfTruth=SWMM` 기준 snapshot broadcast

엔진은 `swmm_engine.interface`를 통해 Django API와 연결된다.

## WebSocket 실시간 전송

`apps/simulation/consumers.py`와 `state.py`는 Channels group `simulation`으로
snapshot을 broadcast한다.

- 연결 직후 최신 snapshot 또는 status payload 전송
- runtime tick, control, pause, resume 이벤트 전송
- 현재 인증 없는 단일 전역 simulation group 사용

다중 인스턴스 운영 전에는 Redis Channel Layer와 세션 분리 설계가 필요하다.

## 위험 감지

`swmm_engine/risk`는 snapshot에서 deterministic 위험 이벤트를 만든다.

- node 수심, 월류
- link 충만도, 용량 초과
- 막힘
- 역류
- policy level별 민감도 조정

`severity=CRITICAL` 이벤트는 `apps.monitoring`으로 전달되어 `HazardEvent`로
저장된다.

## 10분 위험 예측

`apps/monitoring/services/forecast_state.py`는 최근 snapshot metric을 runtime
메모리 buffer에 저장하고 기본 10분 뒤 상태를 예측한다.

- `links.fullness`
- `links.capacityRatio`
- `links.blockageRatio`
- `nodes.depthRatio`
- `nodes.floodingCms`

변화량 기반 metric은 최소 관측 시간과 현재값 최소 조건을 만족해야 한다.
`blockageRatio`는 사용자가 직접 바꾸는 제어 상태이므로 안정화 시간 전에도
forecast 위험 이벤트로 만들 수 있다.

## 위험 로그와 조치 흐름

`apps/monitoring`은 위험 로그 조회와 관리자 조치 이력을 제공한다.

- `GET /api/hazards`: 위험 로그 목록 조회
- `GET /api/hazards/{id}`: 위험 로그 상세 조회
- `POST /api/hazards/{id}/actions`: 조치 시작
- `PATCH /api/hazards/{id}/actions/{action_id}`: 조치 완료
- `GET /api/hazards/forecast`: runtime buffer 기반 미래 위험 조회

조치 시작은 DB에만 저장하고 LLM maintenance endpoint로 보내지 않는다. 조치 완료
시점에만 결과와 재발 참고사항을 합쳐 전송한다.

## 우선순위 점수

`swmm_engine/risk/priority.py`는 여러 위험이 동시에 발생했을 때 현장 대처
우선순위를 계산한다.

- `priorityScore`
- `priorityBand`
- `priorityReasons`

위험 로그 목록과 forecast 이벤트는 이 값을 포함하며, LLM 서버로 전달되는 분석
payload와 maintenance payload에도 포함된다.

## 알림 수신자 관리

`apps/notification`은 Telegram 발송에 필요한 DB 값을 관리한다.

- `BotToken`: 운영자가 수동으로 넣는 Telegram bot token
- `NotificationRecipient`: 직원 이름과 chat ID
- 수신자 생성, 조회, 삭제 API
- LLM 분석 payload용 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` 생성

Django는 Telegram API를 직접 호출하지 않는다.

## LLM 분석 연동

`swmm_engine/llm_dispatcher.py`는 위험 context를 FastAPI/LangChain 서버로
전송한다.

- 일반 CRITICAL 위험 aggregation
- 일반 발송 cooldown
- runtime 막힘/역류 emergency aggregation
- LLM dispatch JSONL 로그 기록
- 로컬 파일 경로와 디버그 metadata 제거 후 전송

전송 대상 URL은 `SUPERMARIO_LLM_ANALYZE_URL`이다.

## 과거 조치 이력 임베딩 연동

조치 완료 시 `apps/monitoring/services/maintenance_dispatcher.py`가 구조화 payload를
FastAPI/LangChain maintenance log endpoint로 보낸다.

payload는 `event`, `metrics`, `action` 객체를 포함한다. 이 데이터는 외부 LLM
서버에서 보고서 문장으로 포맷하거나 embedding하여 VectorDB에 저장하는 입력값으로
사용된다.

현재 Django는 실제 Chroma VectorDB에 직접 연결하지 않는다. Django DB에는
`HazardCaseEmbedding` 이력과 FastAPI가 반환한 `vector_id`만 보존한다.
