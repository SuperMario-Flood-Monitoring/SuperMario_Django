# 문자 발송 정책

## 문서 정보

- 기준일: 2026-06-26
- 기준 구현: `swmm_engine/llm_dispatcher.py`, `config/settings.py`
- 시간 설정 위치: `config/settings.py`
- Docker 기본값 위치: `docker-compose.yml`

## 시간 설정값

| 환경변수                                       | 기본값 | 의미                                                        |
| ---------------------------------------------- | -----: | ----------------------------------------------------------- |
| `SUPERMARIO_LLM_DISPATCH_COOLDOWN_SECONDS`     |    300 | 일반 문자 발송 후 다음 일반 문자 발송까지의 cooldown window |
| `SUPERMARIO_LLM_AGGREGATION_SECONDS`           |     60 | 일반 CRITICAL 위험을 첫 발송 전에 묶는 aggregation window   |
| `SUPERMARIO_LLM_EMERGENCY_AGGREGATION_SECONDS` |     30 | runtime 막힘/역류 위험을 묶는 emergency aggregation window  |

위 값은 Django 설정에서 읽어 `swmm_engine/llm_dispatcher.py`의
`LLM_DISPATCH_COOLDOWN_SECONDS`, `LLM_DISPATCH_AGGREGATION_SECONDS`,
`LLM_DISPATCH_EMERGENCY_AGGREGATION_SECONDS`로 사용한다.

## 발송 대상

문자 발송 대상은 `severity=CRITICAL`인 위험 이벤트로 제한한다.
`WARNING`, `WATCH` 이벤트는 문자 발송 대상이 아니며 화면 표시 또는 로그 기록
대상으로만 남긴다.

같은 위험은 아래 세 필드 조합으로 식별한다.

```text
eventType + source + sourceId
```

## 일반 위험 발송 흐름

활성 문자 묶음이 없고 cooldown window도 없을 때 첫 일반 CRITICAL 위험이
발생하면 aggregation window를 시작한다. aggregation window 동안 들어온 일반
CRITICAL 위험은 즉시 LLM 서버로 보내지 않고 batch에 누적한다.

aggregation window가 끝나면 batch에 누적된 위험을 하나의 LLM 요청으로 묶어
`SUPERMARIO_LLM_ANALYZE_URL`에 POST한다. 이 시점을 문자 발송 요청 발생 시점으로
보고 cooldown window를 시작한다.

cooldown window가 활성화된 동안 발생한 일반 CRITICAL 위험은 즉시 발송하지 않고
pending queue에 누적한다. cooldown window가 끝났을 때 pending queue에 위험이
남아 있으면 하나의 LLM 요청으로 묶어 발송하고, 다시 cooldown window를 시작한다.

cooldown window가 활성화된 상태에서는 새 aggregation window를 열지 않는다.

## 중복 누적 제한

같은 aggregation batch 안에서는 같은 위험 식별자를 중복 누적하지 않는다.

cooldown 중 pending queue에도 같은 위험 식별자를 중복 누적하지 않는다. 또한 직전
pending queue에 이미 포함되어 발송된 위험 식별자는 바로 다음 pending queue에
다시 누적하지 않는다. 같은 위험이 계속 유지되는 경우 매 cooldown마다 반복 발송되는
것을 줄이기 위한 1차 정책이다.

## 예외 위험

runtime risk 기준의 막힘/역류는 일반 aggregation window와 cooldown window를
적용하지 않는다. 대신 emergency aggregation window에 누적한 뒤 하나의 LLM 요청으로
묶어 발송한다.

현재 emergency 예외 event type은 다음과 같다.

```text
BLOCKAGE_CLOSED
REVERSE_FLOW
```

forecast 기준의 `PREDICTED_BLOCKAGE_CLOSED`는 emergency 예외로 보지 않고 일반
CRITICAL forecast 위험처럼 aggregation, cooldown, pending queue 정책을 따른다.
현재 forecast에는 역류 예측 이벤트가 없으므로 역류 예외는 runtime risk의
`REVERSE_FLOW`에 한정한다.

## 로그

`swmm_engine/logs/llm-dispatch.jsonl`에는 발송 후보와 결과가 JSONL로 남는다.
주요 상태값은 다음과 같다.

| 상태                            | 의미                                                       |
| ------------------------------- | ---------------------------------------------------------- |
| `aggregation_queued`            | 일반 CRITICAL 위험이 aggregation batch에 누적됨            |
| `aggregation_duplicate_skipped` | 같은 aggregation batch에 이미 있는 위험이라 중복 제외됨    |
| `pending_queued`                | cooldown 중 일반 CRITICAL 위험이 pending queue에 누적됨    |
| `pending_duplicate_skipped`     | pending queue 또는 직전 pending queue 기준 중복이라 제외됨 |
| `emergency_queued`              | runtime 막힘/역류 위험이 emergency batch에 누적됨          |
| `emergency_duplicate_skipped`   | emergency batch에 이미 있는 위험이라 중복 제외됨           |
| `severity_skipped`              | CRITICAL 이벤트가 없어 문자 발송 대상에서 제외됨           |
| `scheduled`                     | LLM 서버 POST가 background task로 예약됨                   |
| `sent`                          | LLM 서버 요청이 성공 응답을 반환함                         |
| `http_error`                    | LLM 서버가 HTTP 오류를 반환함                              |
| `response_timeout`              | LLM 서버 응답 대기 시간이 초과됨                           |
| `dispatch_failed`               | 네트워크 오류 등으로 요청 자체가 실패함                    |

## 제한 사항

현재 정책 상태는 Django 프로세스 메모리에 저장된다. 컨테이너 또는 프로세스가
재시작되면 aggregation batch, cooldown 상태, pending queue가 초기화된다.
