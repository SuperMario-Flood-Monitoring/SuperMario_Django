# 테스트 결과 보고서

## 실행 정보

- 실행일: 2026-06-24
- 운영체제: Windows
- Python: 3.14.4
- 가상환경: `backend/.venv`
- Django: 6.0.6
- 명령어:
  - `.\backend\.venv\Scripts\python.exe backend\manage.py check`
  - `.\backend\.venv\Scripts\python.exe backend\manage.py test apps.simulation -v 2`
  - `.\.venv\Scripts\python.exe manage.py test apps.auth apps.facilities apps.simulation -v 2` (`backend` 디렉터리에서 실행)
  - `.\.venv\Scripts\python.exe manage.py test -v 2` (`backend` 디렉터리에서 실행)
  - `.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run` (`backend` 디렉터리에서 실행)

## 결과 요약

| 항목                                   | 결과      |
| -------------------------------------- | --------- |
| Django system check                    | 통과      |
| 직접 통합테스트                        | 통과      |
| migration 변경 여부 확인               | 변경 없음 |
| 활성 auth/facilities/monitoring/notification/simulation 테스트 | 47개 통과 |
| 전체 테스트 발견 수                    | 47        |
| 전체 테스트 성공                       | 47        |
| 전체 테스트 실패                       | 0         |
| 전체 테스트 오류                       | 0         |
| 전체 테스트 최종 결과                  | 통과      |

`manage.py check`는 `System check identified no issues (0 silenced).`로 통과했다.

notification app이 추가되기 전 기준으로 사용자가 직접 수행한 통합테스트는 정상
동작을 확인했다. 이 통합테스트는 React 클라이언트와 Django 백엔드의 실제 연동
흐름을 기준으로 수행한 결과이며, notification app 추가 이후의 알림 수신자 API와
LLM notification payload는 아래 자동 테스트 결과로 별도 검증했다.

`apps.auth apps.facilities apps.monitoring apps.notification apps.simulation` 테스트는 47개 모두
통과했다.
`apps.simulation`에는 LangChain payload의 `id`를 React 강수 preset 기준으로
`맑음`, `약한비`, `폭우`로 정규화하는 LEVEL 13 회귀 테스트와 LLM 발송
쿨다운을 검증하는 LEVEL 14 회귀 테스트, LLM 응답 timeout을
`dispatch_failed`로 분류하지 않는 회귀 테스트가 포함된다. LEVEL 15 기준으로
쿨다운 중인 trigger는 LangChain 요청을 만들지 않지만 후보 로그는
`cooldown_skipped` 상태로 남기는 것도 같은 테스트에서 검증한다. LEVEL 16 기준으로
위험 이슈가 유지시간을 채운 뒤에만 LLM trigger가 열리고, 계속 유지되면 다음
유지시간 이후 다시 trigger되는 lifecycle도 검증한다.

LEVEL 18 기준으로 `apps.monitoring`에는 현재 프로젝트의 실제 SWMM risk event
구조(`eventType`, `source`, `sourceId`, `severity`)를 기준으로 CRITICAL 위험
로그를 저장하는 테스트, event_key 중복 방지 테스트, REST 목록/상세/조치 완료와
embedding 이력 생성 테스트, 미완료 조치가 논리 삭제와 embedding 이력을 만들지
않는 테스트가 포함된다.

LEVEL 19 기준으로 `apps.monitoring`에는 React에서 받은 장애 조치 원문을
FastAPI maintenance log endpoint로 `{"sourceId": "...", "action_details": "..."}`
형식으로 전달하는 테스트, FastAPI 응답의 `vector_id` 저장 테스트, FastAPI 요청
실패 시에도 조치 저장과 완료 처리가 유지되고 `fastapi_sync_status=FAILED`로
기록되는 테스트가 포함된다.

LEVEL 20 기준으로 `apps.monitoring`에는 runtime state 메모리 buffer를 이용해
기본 10분 뒤 위험을 예측하는 테스트, forecast API 테스트, CRITICAL 예측일 때만
LLM forecast payload를 생성하는 테스트가 포함된다. LLM 분석 요청 기준은 현재
위험 trigger가 아니라 forecast CRITICAL 이벤트로 변경했다.
LLM 요청 payload의 Telegram 정보는 최상위 `TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID` 키로 전달하도록 검증했다.

LEVEL 21 기준으로 시작 직후 관측 시간이 부족할 때 forecast CRITICAL을 만들지
않는 테스트, 맑음에서는 현재 수위 기준을 더 보수적으로 적용하는 테스트, 폭우는
더 낮은 현재 수위에서도 forecast 위험을 허용하는 테스트를 추가했다. 또한
시뮬레이션 도중 관 막힘이 100%로 변경되면 관측 안정화 시간 전에도
`PREDICTED_BLOCKAGE_CLOSED` CRITICAL forecast event와 LLM forecast payload가
생성되는지 검증한다.

현재 `manage.py test -v 2`의 전체 discovery 대상 47개 테스트는 모두 통과한다.

## 통과한 테스트

| 테스트                                                                                                             | 결과 |
| ------------------------------------------------------------------------------------------------------------------ | ---- |
| `apps.auth.tests.AuthApiTests.test_login_issues_access_token_and_refresh_cookie`                                   | 통과 |
| `apps.auth.tests.AuthApiTests.test_login_rejects_wrong_password`                                                   | 통과 |
| `apps.auth.tests.AuthApiTests.test_protected_api_accepts_admin_access_token`                                       | 통과 |
| `apps.auth.tests.AuthApiTests.test_protected_api_requires_access_token`                                            | 통과 |
| `apps.auth.tests.AuthApiTests.test_refresh_rejects_reused_refresh_token`                                           | 통과 |
| `apps.auth.tests.AuthApiTests.test_refresh_rotates_tokens`                                                         | 통과 |
| `apps.auth.tests.EnsureAdminUserCommandTests.test_creates_default_admin_when_missing`                              | 통과 |
| `apps.auth.tests.EnsureAdminUserCommandTests.test_recreates_default_admin_even_when_other_admin_exists`             | 통과 |
| `apps.auth.tests.EnsureAdminUserCommandTests.test_recreates_existing_default_admin`                                 | 통과 |
| `apps.auth.tests.EnsureAdminUserCommandTests.test_skips_default_admin_when_admin_exists`                            | 통과 |
| `apps.facilities.tests.FacilitiesViewTests.test_initialization_updates_facility_with_same_name`                    | 통과 |
| `apps.facilities.tests.FacilitiesViewTests.test_initializes_multiple_facilities`                                   | 통과 |
| `apps.facilities.tests.FacilitiesViewTests.test_rejects_unknown_facility_type`                                     | 통과 |
| `apps.monitoring.tests.ForecastStateTests.test_builds_forecast_llm_payload_only_for_critical_predictions`         | 통과 |
| `apps.monitoring.tests.ForecastStateTests.test_blockage_control_creates_critical_forecast_event_during_startup_window` | 통과 |
| `apps.monitoring.tests.ForecastStateTests.test_clear_weather_requires_higher_current_level_for_forecast_event`    | 통과 |
| `apps.monitoring.tests.ForecastStateTests.test_does_not_build_llm_payload_without_critical_prediction`            | 통과 |
| `apps.monitoring.tests.ForecastStateTests.test_does_not_predict_from_short_startup_window`                       | 통과 |
| `apps.monitoring.tests.ForecastStateTests.test_heavy_rain_allows_forecast_event_with_lower_current_level`        | 통과 |
| `apps.monitoring.tests.ForecastStateTests.test_predicts_10_minute_future_risk_from_runtime_buffer`                | 통과 |
| `apps.monitoring.tests.HazardApiTests.test_action_resolves_event_logically_and_creates_embedding_record`          | 통과 |
| `apps.monitoring.tests.HazardApiTests.test_action_saves_even_when_fastapi_dispatch_fails`                        | 통과 |
| `apps.monitoring.tests.HazardApiTests.test_detail_includes_metrics_snapshot`                                      | 통과 |
| `apps.monitoring.tests.HazardApiTests.test_forecast_api_returns_runtime_buffer_prediction`                        | 통과 |
| `apps.monitoring.tests.HazardApiTests.test_incomplete_action_does_not_resolve_or_create_embedding_record`         | 통과 |
| `apps.monitoring.tests.HazardApiTests.test_lists_open_hazard_rows_without_metrics_snapshot`                       | 통과 |
| `apps.monitoring.tests.HazardApiTests.test_rejects_hazard_api_without_admin_token`                                | 통과 |
| `apps.monitoring.tests.HazardEventServiceTests.test_creates_critical_hazard_event_from_current_swmm_risk_event_shape` | 통과 |
| `apps.monitoring.tests.HazardEventServiceTests.test_ignores_non_critical_events`                                  | 통과 |
| `apps.monitoring.tests.HazardEventServiceTests.test_prevents_duplicate_events_by_event_key`                       | 통과 |
| `apps.monitoring.tests.MaintenanceDispatcherTests.test_builds_fastapi_payload_with_prompt_level_19_keys`          | 통과 |
| `apps.monitoring.tests.MaintenanceDispatcherTests.test_dispatch_records_vector_id_from_fastapi_response`          | 통과 |
| `apps.notification.tests.NotificationPayloadTests.test_builds_empty_notification_payload_without_rows`             | 통과 |
| `apps.notification.tests.NotificationPayloadTests.test_builds_notification_payload_for_langchain`                  | 통과 |
| `apps.notification.tests.NotificationRecipientApiTests.test_creates_notification_recipient`                        | 통과 |
| `apps.notification.tests.NotificationRecipientApiTests.test_deletes_notification_recipient`                        | 통과 |
| `apps.notification.tests.NotificationRecipientApiTests.test_lists_notification_recipients`                         | 통과 |
| `apps.notification.tests.NotificationRecipientApiTests.test_rejects_notification_api_without_admin_token`          | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_allows_dispatch_after_cooldown`                          | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_builds_langchain_request_payload_shape`                  | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_normalizes_react_rainfall_preset_labels`                 | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_normalizes_react_rainfall_preset_values`                 | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_response_timeout_is_not_classified_as_dispatch_failed`    | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_skips_dispatch_during_cooldown`                          | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_uses_runtime_rainfall_ratio_when_explicit_id_is_missing` | 통과 |
| `apps.simulation.tests.RiskLifecycleTriggerTests.test_retriggers_when_risk_remains_sustained_after_next_delay`     | 통과 |
| `apps.simulation.tests.RiskLifecycleTriggerTests.test_triggers_after_risk_is_sustained_for_delay`                  | 통과 |

## 통과한 주요 테스트

| 범위                                       | 테스트 수 | 결과 |
| ------------------------------------------ | --------: | ---- |
| 직접 통합테스트                            |         - | 통과 |
| auth API 및 admin 생성 command             |        10 | 통과 |
| facilities API                             |         3 | 통과 |
| monitoring 위험 로그 API, 예측, 저장 서비스, FastAPI maintenance 연동 |        16 | 통과 |
| notification API 및 LLM notification payload |      6 | 통과 |
| LLM dispatcher                             |         7 | 통과 |
| risk lifecycle                             |         2 | 통과 |

## 남은 검증 범위

- 시나리오 CRUD API 테스트
- 에디터 변환 API 테스트
- 현재 `/api/engine/start`, `/api/engine/control`, `/api/engine/pause`,
  `/api/engine/resume`, `/api/engine/stop` 테스트
- 현재 `/api/ws/simulation` 연결 및 snapshot broadcast 테스트
- middleware 적용 후 전체 현재 API의 인증/인가 통합 테스트. 단, monitoring,
  notification, auth 일부 보호 API는 단위 테스트로 검증했다.
- PySWMM 네이티브 런타임이 Windows/Python 3.14.4 환경에서 안정적으로 동작하는지
  확인
- LLM dispatcher retry, 실패 결과 저장 정책, 실제 LangChain 서버와의 통합 테스트
- 다중 클라이언트와 다중 프로세스 Channel Layer 검증
- 실제 React 클라이언트의 위험 로그 Grid, row 클릭, 조치 입력 모달 연동 테스트
