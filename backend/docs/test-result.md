# 테스트 결과 보고서

## 실행 정보

- 실행일: 2026-06-16
- 운영체제: Windows
- Python: 3.14.3
- Django: 6.0.6
- Channels: 4.3.2
- PySWMM: 2.1.0
- SWMM Toolkit: 0.16.2, SWMM 5.2.4
- 명령어: `..\.venv\Scripts\python.exe manage.py test -v 2`

## 결과 요약

| 항목 | 결과 |
| --- | --- |
| 발견된 테스트 | 16 |
| 성공 | 16 |
| 실패 | 0 |
| 오류 | 0 |
| 실행 시간 | 0.355초 |
| Django 시스템 체크 | 문제 없음 |

최종 결과: `OK`

## 테스트 목록

| 테스트 | 검증 내용 | 결과 |
| --- | --- | --- |
| `test_initialization_updates_facility_with_same_name` | 같은 이름의 시설을 중복 생성하지 않고 갱신 | 통과 |
| `test_initializes_multiple_facilities` | 복수 시설 초기 상태 저장 | 통과 |
| `test_rejects_unknown_facility_type` | 허용되지 않은 시설 유형 거부 | 통과 |
| `test_requires_initialized_facilities` | 시설 없이 시뮬레이션 실행 시 409 반환 | 통과 |
| `test_runs_pyswmm_engine` | 실제 PySWMM 실행, 단계 계산, 결과 저장 | 통과 |
| `test_rejects_model_with_unknown_link_node` | 잘못된 모델 참조 거부 | 통과 |
| `test_demo_page_is_available` | 브라우저 테스트 화면 렌더링 | 통과 |
| `test_connects_and_receives_ready_message` | WebSocket 연결 및 준비 메시지 수신 | 통과 |
| `test_demo_facilities_prevent_initialization_conflict` | 더미 시설 초기화 후 409 없이 실행 | 통과 |
| `test_high_blockage_is_reported_as_failure` | 85% 폐색 관로를 CRITICAL 장애로 판정 | 통과 |
| `test_csv_utility_round_trip` | 시설 목록 JSON/CSV 상호 변환 | 통과 |
| `test_receives_one_second_swmm_step_from_api` | 구독 중 API 실행 시 1초 SWMM 단계 메시지 수신 | 통과 |
| `test_initial_water_is_applied_to_first_swmm_step` | 시설 초기 수위가 첫 SWMM 단계에 반영 | 통과 |
| `test_rejects_duration_over_thirty_seconds` | 30초 초과 요청 거부 | 통과 |
| `test_normalizes_swmm_section_model` | SWMM 섹션형 모델을 내부 그래프 계약으로 정규화 | 통과 |
| `test_runs_swmm_section_model_from_api` | SWMM 섹션형 모델로 시뮬레이션 API 실행 | 통과 |

## 추가 검증

- `python manage.py check`: 통과
- `python manage.py makemigrations --check --dry-run`: 변경 없음
- `docker compose config`: Compose 구조 유효
- 실제 SQLite에 초기 마이그레이션 적용 완료
- 샘플 모델 직접 실행: 9단계, 노드 2개, 관로 2개, SWMM 경고 0건
- 실제 시간 간격 측정: 1.003초, 1.005초
- 4초 짧은 실행: 3단계, 총 3.068초
- 30초 LEVEL 5 실행: 28단계, 총 28.345초
- 30초 실행 메시지 간격: 최소 1.000초, 최대 1.015초
- 첫 단계 빗물받이 수위: 31.581%
- 빗물받이 낙엽 장애 반영: 연결 관로 폐색 60%, `LEAVES`
- 30초 실행 SWMM 경고: 0건

`docker compose config` 실행 시 사용자 홈의 Docker 설정 파일 접근 경고가
발생했으나 Compose 문법과 서비스 해석은 성공했다.

## 남은 테스트 범위

- 시설 상세 조회, 수정, 삭제 API
- 시뮬레이션 목록 및 중지 API
- HTTP 실행 결과의 WebSocket 실시간 방송 통합 테스트
- 엔진 예외 발생 시 500 응답
- 실행 중 중지 요청의 별도 연결 통합 테스트
- LangChain 서버 전송 및 실패 재시도
- 인증, 부하, 다중 프로세스 Channel Layer 테스트
