# 테스트 결과 보고서

## 실행 정보

- 실행일: 2026-06-23
- 운영체제: Windows
- Python: 3.14.4
- 가상환경: `backend/.venv`
- Django: 6.0.6
- 명령어:
  - `.\backend\.venv\Scripts\python.exe backend\manage.py check`
  - `.\backend\.venv\Scripts\python.exe backend\manage.py test apps.auth apps.facilities -v 2`
  - `.\.venv\Scripts\python.exe manage.py test -v 2` (`backend` 디렉터리에서 실행)

## 결과 요약

| 항목 | 결과 |
| --- | --- |
| Django system check | 통과 |
| 활성 auth/facilities 테스트 | 9개 통과 |
| 전체 테스트 발견 수 | 22 |
| 전체 테스트 성공 | 12 |
| 전체 테스트 실패 | 1 |
| 전체 테스트 오류 | 9 |
| 전체 테스트 최종 결과 | 실패 |

`manage.py check`는 `System check identified no issues (0 silenced).`로 통과했다.

`apps.auth apps.facilities` 테스트는 9개 모두 통과했다.

전체 테스트는 legacy 테스트가 현재 기본 URL 라우팅과 인증 정책에 맞지 않아
실패했다. 실패는 PySWMM 계산 실패가 아니라, 대부분 제거된 예전 namespace/path를
테스트가 기대하거나 auth middleware 적용 후 인증 없이 `/api`를 호출하기 때문이다.

## 통과한 테스트

| 테스트 | 결과 |
| --- | --- |
| `apps.auth.tests.AuthApiTests.test_login_issues_access_token_and_refresh_cookie` | 통과 |
| `apps.auth.tests.AuthApiTests.test_login_rejects_wrong_password` | 통과 |
| `apps.auth.tests.AuthApiTests.test_protected_api_accepts_admin_access_token` | 통과 |
| `apps.auth.tests.AuthApiTests.test_protected_api_requires_access_token` | 통과 |
| `apps.auth.tests.AuthApiTests.test_refresh_rejects_reused_refresh_token` | 통과 |
| `apps.auth.tests.AuthApiTests.test_refresh_rotates_tokens` | 통과 |
| `apps.facilities.tests.FacilitiesViewTests.test_initialization_updates_facility_with_same_name` | 통과 |
| `apps.facilities.tests.FacilitiesViewTests.test_initializes_multiple_facilities` | 통과 |
| `apps.facilities.tests.FacilitiesViewTests.test_rejects_unknown_facility_type` | 통과 |
| `legacy.apps_simulation_legacy.tests.SimulationViewTests.test_csv_utility_round_trip` | 통과 |
| `legacy.apps_simulation_legacy.tests.SimulationViewTests.test_initial_water_is_applied_to_first_swmm_step` | 통과 |
| `legacy.apps_simulation_legacy.tests.SimulationViewTests.test_normalizes_swmm_section_model` | 통과 |

## 실패한 테스트

| 테스트 | 실패 원인 |
| --- | --- |
| `test_demo_facilities_prevent_initialization_conflict` | auth middleware 적용 후 인증 없는 `/api/facilities/` 호출이 `401` 반환 |
| `test_demo_page_is_available` | `simulation` namespace 미등록 |
| `test_high_blockage_is_reported_as_failure` | `simulation` namespace 미등록 |
| `test_rejects_duration_over_thirty_seconds` | `simulation` namespace 미등록 |
| `test_rejects_model_with_unknown_link_node` | `simulation` namespace 미등록 |
| `test_requires_initialized_facilities` | `simulation` namespace 미등록 |
| `test_runs_pyswmm_engine` | `simulation` namespace 미등록 |
| `test_runs_swmm_section_model_from_api` | `simulation` namespace 미등록 |
| `test_connects_and_receives_ready_message` | `ws/simulation/` 라우트 없음 |
| `test_receives_one_second_swmm_step_from_api` | `ws/simulation/` 라우트 없음 |

대표 오류는 다음과 같다.

```text
django.urls.exceptions.NoReverseMatch: 'simulation' is not a registered namespace
ValueError: No route found for path 'ws/simulation/'.
```

현재 공개 WebSocket 경로는 `/api/ws/simulation`과 `/api/ws/simulation/`이다.

## 해석

현재 기본 공개 API는 `/api/engine/`, `/api/editor/`, `/api/scenarios`,
`/api/facilities/`, `/api/ws/simulation`이다. legacy 테스트는 예전
`/api/simulations/` API와 `ws/simulation/` 경로를 기준으로 작성되어 있어 전체
테스트에서 실패한다.

현재 구현을 기준으로 테스트를 정상화하려면 다음 중 하나를 선택해야 한다.

1. legacy 테스트를 기본 테스트 대상에서 제외한다.
2. legacy URL을 `ENABLE_LEGACY_SIMULATION_API=true` 환경에서만 별도로 테스트한다.
3. 현재 `/api/engine`, `/api/editor`, `/api/scenarios`, `/api/ws/simulation` 계약을
   검증하는 새 테스트로 교체한다.

## 남은 검증 범위

- 시나리오 CRUD API 테스트
- 에디터 변환 API 테스트
- 현재 `/api/engine/start`, `/api/engine/control`, `/api/engine/pause`,
  `/api/engine/resume`, `/api/engine/stop` 테스트
- 현재 `/api/ws/simulation` 연결 및 snapshot broadcast 테스트
- middleware 적용 후 전체 현재 API의 인증/인가 통합 테스트
- PySWMM 네이티브 런타임이 Windows/Python 3.14.4 환경에서 안정적으로 동작하는지
  확인
- LLM dispatcher 실제 HTTP 연동, retry, 실패 로그 정책
- 다중 클라이언트와 다중 프로세스 Channel Layer 검증
