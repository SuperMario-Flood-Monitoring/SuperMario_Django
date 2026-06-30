# 웹소켓 명세서

## 문서 정보

- 기준일: 2026-06-23
- 기준 구현: `apps/simulation/routing.py`, `apps/simulation/consumers.py`, `apps/simulation/state.py`

## 연결 정보

| 항목           | 값                                          |
| -------------- | ------------------------------------------- |
| 개발 URL       | `ws://127.0.0.1:8000/api/ws/simulation`     |
| 허용 경로      | `/api/ws/simulation`, `/api/ws/simulation/` |
| 운영 URL       | `wss://{host}/api/ws/simulation`            |
| 프로토콜       | JSON text frame                             |
| 인증           | 현재 없음                                   |
| Channels group | `simulation`                                |

클라이언트가 별도 구독 메시지를 보낼 필요는 없다. 연결된 모든 클라이언트는 같은
`simulation` group에 참여한다.

## 연결 직후 메시지

서버는 연결을 수락한 뒤 다음 중 하나를 즉시 보낸다.

1. 엔진 세션에 최신 snapshot이 있으면 해당 snapshot.
2. 최신 snapshot이 없으면 엔진 status payload.

현재 구현은 공통 `{ code, message, status, data }` 래퍼를 사용하지 않고 payload를
그대로 보낸다.

### Status payload 예시

```json
{
  "ok": true,
  "running": false,
  "paused": false,
  "hasSession": false,
  "stepIndex": 0,
  "stepSeconds": 1,
  "modelTime": null,
  "control": {
    "rainfallRatio": 0.0,
    "rainfallPercent": 0.0,
    "blockagesById": {},
    "maxRainfallMmPerHour": 100.0,
    "speedMultiplier": 1.0
  },
  "lastError": null,
  "runId": null,
  "tickLogPath": null,
  "lastLogError": null,
  "websocketClients": 1
}
```

## Snapshot 이벤트

`POST /api/engine/start`, `/api/engine/control`, `/api/engine/pause`,
`/api/engine/resume`, runtime tick loop가 snapshot 또는 status를 broadcast한다.

주요 snapshot type은 다음과 같다.

| `type`    | 발생 시점           |
| --------- | ------------------- |
| `started` | 엔진 시작 직후      |
| `tick`    | PySWMM step 진행 후 |
| `control` | 제어값 변경 직후    |
| `paused`  | 일시정지 직후       |
| `resumed` | 재개 직후           |

### Snapshot 예시

```json
{
  "type": "tick",
  "ok": true,
  "sourceOfTruth": "SWMM",
  "runId": "20260623-120000-ab12cd34",
  "tickLogPath": "C:\\path\\to\\swmm-runtime-20260623-120000-ab12cd34.jsonl",
  "source": "react-editor-json",
  "modelPath": "C:\\Temp\\swmm-django-runtime-...\\react_editor_runtime.inp",
  "runtimeModelPath": "C:\\Temp\\swmm-django-runtime-...\\react_editor_runtime.runtime.inp",
  "modelTime": "2026-06-16T00:00:01",
  "stepSeconds": 1,
  "stepIndex": 1,
  "control": {
    "rainfallRatio": 0.5,
    "rainfallPercent": 50.0,
    "blockagesById": {},
    "maxRainfallMmPerHour": 100.0,
    "speedMultiplier": 1.0
  },
  "nodes": {},
  "links": {},
  "editorObjects": {},
  "summary": {
    "nodeCount": 0,
    "linkCount": 0,
    "rainfallTargetCount": 0,
    "blockageTargetCount": 0,
    "activeBlockageCount": 0
  },
  "risk": {
    "ok": true,
    "highestSeverity": "NORMAL",
    "events": [],
    "summary": {},
    "validation": {},
    "counters": {},
    "policy": {
      "level": "balanced"
    }
  },
  "llmTrigger": {
    "shouldTrigger": false,
    "reason": null,
    "contextLevel": "optimal",
    "triggeredIssues": [],
    "newIssueCount": 0,
    "escalatedIssueCount": 0,
    "activeIssueCount": 0,
    "resolvedIssues": []
  }
}
```

## Node 상태

`nodes`는 SWMM node ID를 key로 하는 객체다. 주요 필드는 다음과 같다.

| 필드                 | 단위  | 설명                        |
| -------------------- | ----- | --------------------------- |
| `id`                 | -     | SWMM node ID                |
| `sourceEditorId`     | -     | 원본 React editor 객체 ID   |
| `sourceEditorType`   | -     | 원본 React editor 객체 type |
| `sourceEditorName`   | -     | 원본 표시 이름              |
| `depthM`             | m     | 수심                        |
| `headM`              | m     | 수두                        |
| `invertElevationM`   | m     | 관저고                      |
| `maxDepthM`          | m     | 표시 기준 최대 수심         |
| `hydraulicMaxDepthM` | m     | SWMM 계산 기준 최대 수심    |
| `depthRatio`         | ratio | 수심 / 최대 수심            |
| `totalInflowCms`     | m3/s  | 총 유입량                   |
| `floodingCms`        | m3/s  | 월류량                      |

## Link 상태

`links`는 SWMM link ID를 key로 하는 객체다.

| 필드               | 단위   | 설명                        |
| ------------------ | ------ | --------------------------- |
| `id`               | -      | SWMM link ID                |
| `sourceEditorId`   | -      | 원본 React editor 객체 ID   |
| `sourceEditorType` | -      | 원본 React editor 객체 type |
| `sourceEditorName` | -      | 원본 표시 이름              |
| `fromNode`         | -      | 시작 SWMM node              |
| `toNode`           | -      | 종료 SWMM node              |
| `flowCms`          | m3/s   | 유량                        |
| `velocityMps`      | m/s    | 속도                        |
| `depthM`           | m      | 관로 수심                   |
| `fullness`         | ratio  | 관로 충만도                 |
| `capacityCms`      | m3/s   | 추정 만관 용량              |
| `capacityRatio`    | ratio  | 유량 / 용량                 |
| `targetSetting`    | ratio  | 제어 목표 개도              |
| `currentSetting`   | ratio  | 현재 개도                   |
| `blockageRatio`    | ratio  | 막힘 비율                   |
| `direction`        | string | `forward` 또는 `reverse`    |

## Editor Object 상태

`editorObjects`는 React editor 객체 ID를 key로 한다. 하나의 editor 객체가 여러
SWMM node/link로 변환될 수 있으므로 최대 수위, 최대 충만도, 최대 막힘 등을
집계한다.

| 필드               | 설명                               |
| ------------------ | ---------------------------------- |
| `maxDepthRatio`    | 연결 node의 최대 수심 비율         |
| `maxFullness`      | 연결 link의 최대 충만도            |
| `maxCapacityRatio` | 연결 link의 최대 용량 비율         |
| `maxBlockageRatio` | 연결 link의 최대 막힘 비율         |
| `maxFloodingCms`   | 연결 node의 최대 월류량            |
| `flowCms`          | 연결 link 중 절댓값이 가장 큰 유량 |
| `maxVelocityMps`   | 연결 link 중 절댓값이 가장 큰 속도 |
| `totalInflowCms`   | 연결 node의 최대 유입량            |

## Risk와 LLM Trigger

모든 snapshot에는 `risk`와 `llmTrigger`가 포함된다.

- `risk.highestSeverity`: `NORMAL`, `WATCH`, `WARNING`, `CRITICAL`
- `risk.events`: deterministic rule로 감지한 위험 이벤트 목록
- `risk.policy.level`: `SUPERMARIO_RISK_POLICY_LEVEL` 값, 기본 `balanced`
- `llmTrigger.shouldTrigger`: 새 위험 이슈 또는 심각도 상승으로 LLM 분석이 필요한지 여부
- `llmTrigger.context`: trigger가 true일 때만 포함되는 LLM 분석 context

dispatcher는 trigger context를 정리한 뒤 `SUPERMARIO_LLM_ANALYZE_URL`로
`{"id": "...", "swmm_raw_data": "..."}` 형태의 JSON을 POST한다.
`id`는 React 강수 preset `0/10/100/300` 또는 라벨
`맑음/우천/호우/폭우`를 LangChain 계약의 동일한 4단계 값으로
정규화한 값이다.
짧은 시간에 위험 trigger가 연속 발생해도 Telegram/SNS 알림이 반복 발송되지
않도록 dispatcher는 일반 CRITICAL 위험을 aggregation window로 묶고, 발송 뒤에는
cooldown window 동안 새 일반 위험을 pending queue에 누적한다. runtime
`BLOCKAGE_CLOSED`, `REVERSE_FLOW`는 일반 cooldown 예외이며 emergency aggregation
window로 별도 묶음 발송한다. 상세 정책은
`backend/docs/notification-dispatch-policy.md`에 기록한다.

## 연결 종료

클라이언트 또는 서버가 연결을 종료하면 해당 channel을 `simulation` group에서
제거하고 `websocketClients` 카운터를 감소시킨다.

현재 heartbeat, 재연결, 메시지 재전송은 클라이언트 책임이다.

## 제한 사항

- 클라이언트 발신 WebSocket 메시지는 현재 처리하지 않는다.
- 모든 클라이언트가 하나의 전역 엔진 세션 snapshot을 공유한다.
- In-memory Channel Layer를 사용하므로 다중 프로세스 간 broadcast를 공유하지 않는다.
- 실행 이력 REST 재조회 API는 현재 없다. tick log는 서버 파일에만 기록된다.
