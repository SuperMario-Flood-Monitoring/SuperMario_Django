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

## 결과 요약

| 항목                                   | 결과      |
| -------------------------------------- | --------- |
| Django system check                    | 통과      |
| 활성 auth/facilities/simulation 테스트 | 20개 통과 |
| 전체 테스트 발견 수                    | 20        |
| 전체 테스트 성공                       | 20        |
| 전체 테스트 실패                       | 0         |
| 전체 테스트 오류                       | 0         |
| 전체 테스트 최종 결과                  | 통과      |

`manage.py check`는 `System check identified no issues (0 silenced).`로 통과했다.

`apps.auth apps.facilities apps.simulation` 테스트는 20개 모두 통과했다.
`apps.simulation`에는 LangChain payload의 `id`를 React 강수 preset 기준으로
`맑음`, `약한비`, `폭우`로 정규화하는 LEVEL 13 회귀 테스트와 LLM 발송
쿨다운을 검증하는 LEVEL 14 회귀 테스트, LLM 응답 timeout을
`dispatch_failed`로 분류하지 않는 회귀 테스트가 포함된다.

현재 `manage.py test -v 2`의 전체 discovery 대상 20개 테스트는 모두 통과한다.

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
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_allows_dispatch_after_cooldown`                          | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_builds_langchain_request_payload_shape`                  | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_normalizes_react_rainfall_preset_labels`                 | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_normalizes_react_rainfall_preset_values`                 | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_response_timeout_is_not_classified_as_dispatch_failed`    | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_skips_dispatch_during_cooldown`                          | 통과 |
| `apps.simulation.tests.LangChainDispatchPayloadTests.test_uses_runtime_rainfall_ratio_when_explicit_id_is_missing` | 통과 |

## 통과한 주요 테스트

| 범위                                       | 테스트 수 | 결과 |
| ------------------------------------------ | --------: | ---- |
| auth API 및 admin 생성 command             |        10 | 통과 |
| facilities API                             |         3 | 통과 |
| LLM dispatcher                             |         7 | 통과 |

## 남은 검증 범위

- 시나리오 CRUD API 테스트
- 에디터 변환 API 테스트
- 현재 `/api/engine/start`, `/api/engine/control`, `/api/engine/pause`,
  `/api/engine/resume`, `/api/engine/stop` 테스트
- 현재 `/api/ws/simulation` 연결 및 snapshot broadcast 테스트
- middleware 적용 후 전체 현재 API의 인증/인가 통합 테스트
- PySWMM 네이티브 런타임이 Windows/Python 3.14.4 환경에서 안정적으로 동작하는지
  확인
- LLM dispatcher retry, 실패 결과 저장 정책, 실제 LangChain 서버와의 통합 테스트
- 다중 클라이언트와 다중 프로세스 Channel Layer 검증
