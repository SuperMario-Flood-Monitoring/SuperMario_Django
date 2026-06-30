# SWMM 입출력 데이터 명세

## 문서 정보

- 기준일: 2026-06-23
- 기준 구현: `swmm_engine/converter`, `swmm_engine/engine`, `swmm_engine/risk`
- 출력 형식: JSON
- 엔진: PySWMM 2.1.0 / EPA SWMM 5.2.4
- 단위계: SI
- 기준 데이터: SWMM 런타임 snapshot

## 입력 원칙

현재 공개 런타임 API의 입력 source data는 React editor layout JSON이다.
`/api/engine/start`는 반드시 `layout` 객체를 받아야 하며, 예전
`swmm-section-v1` 직접 입력은 현재 공개 엔진 시작 경로의 기준 계약이 아니다.

에디터 layout은 converter에서 SWMM 수리 객체로 변환된다. Django 계층은
`swmm_engine.interface.convert_layout_to_inp()` 또는 `start_engine()`을 통해
변환 결과를 사용한다.

## React Editor Layout 최소 구조

```json
{
  "version": 1,
  "groundSurfaceY": 330,
  "nodes": [
    {
      "id": "catchBasin_1",
      "swmmId": "catchBasin_1",
      "name": "빗물받이 1",
      "type": "catchBasin",
      "x": 100,
      "y": 300,
      "width": 170,
      "height": 110,
      "ports": [
        { "id": "top", "side": "top" },
        { "id": "right", "side": "right" },
        { "id": "bottom", "side": "bottom" },
        { "id": "left", "side": "left" }
      ],
      "props": {}
    },
    {
      "id": "outfall_1",
      "swmmId": "outfall_1",
      "name": "방류구 1",
      "type": "outfall",
      "x": 600,
      "y": 500,
      "width": 120,
      "height": 80,
      "ports": [],
      "props": {}
    }
  ],
  "links": [
    {
      "id": "link_1",
      "swmmId": "link_1",
      "name": "관로 1",
      "type": "relation",
      "from": { "nodeId": "catchBasin_1", "portId": "right" },
      "to": { "nodeId": "outfall_1", "portId": "left" },
      "size": "medium",
      "props": {
        "route": "elbow",
        "slope": 0.03,
        "blockage": 0
      }
    }
  ]
}
```

## SWMM 변환 결과

converter는 layout을 다음 묶음으로 변환한다.

| 필드      | 설명                                               |
| --------- | -------------------------------------------------- |
| `inpText` | PySWMM/SWMM GUI가 읽을 수 있는 INP 텍스트          |
| `report`  | 변환 성공 여부, 카운트, 경고, 오류, 동적 제어 대상 |
| `mapping` | React editor 객체와 SWMM node/link 사이의 매핑     |

### 생성되는 주요 SWMM 섹션

| SWMM 섹션                   | 역할                                            |
| --------------------------- | ----------------------------------------------- |
| `[OPTIONS]`                 | CMS, DYNWAVE, 1초 report/routing step 기본 설정 |
| `[JUNCTIONS]`               | 일반 접합 노드                                  |
| `[STORAGE]`                 | 빗물받이, 일부 시설형 객체                      |
| `[OUTFALLS]`                | 방류구                                          |
| `[PUMPS]`                   | 펌프형 연결                                     |
| `[WEIRS]`                   | 월류형 연결                                     |
| `[CONDUITS]`                | 관로                                            |
| `[XSECTIONS]`               | 원형 관경 또는 위어 단면                        |
| `[LOSSES]`                  | 관로 손실계수                                   |
| `[INFLOWS]`, `[TIMESERIES]` | 기본 강우/건천 유입 시계열                      |
| `[CONTROLS]`                | 초기 폐쇄 링크 제어                             |
| `[REPORT]`                  | node/link report 설정                           |
| `[MAP]`, `[COORDINATES]`    | 좌표                                            |

현재 관로 기본 단면은 원형 `CIRCULAR`이다. `size`는 다음 기본 관경으로 변환된다.

| size     | 관경   |
| -------- | ------ |
| `small`  | 0.30 m |
| `medium` | 0.60 m |
| `large`  | 1.00 m |

## 런타임 제어값

엔진 시작 payload 또는 `/api/engine/control`로 제어값을 전달한다.

```json
{
  "rainfallRatio": 0.5,
  "maxRainfallMmPerHour": 100,
  "speedMultiplier": 2,
  "blockagesById": {
    "link_1": 0.3
  }
}
```

| 필드                            | 처리                                                      |
| ------------------------------- | --------------------------------------------------------- |
| `rainfallRatio` 또는 `rainfall` | 0~1000으로 제한, 1 초과 값은 percent로 간주               |
| `maxRainfallMmPerHour`          | 강수량 비율의 최대 기준값                                 |
| `speedMultiplier`               | 1~10으로 제한                                             |
| `blockagesById`                 | link 또는 node ID별 막힘 비율, 1 초과 값은 percent로 간주 |
| `exceptions`                    | `{ blockage, swmmLinks }` 배열로 여러 link 막힘 지정      |

강수 입력은 converter report의 `dynamicControls.rainfallTargets`에 직접 유입량으로
적용된다. 막힘 입력은 link target/current setting 또는 flow limit에 반영된다.

## Snapshot 공통 구조

엔진 snapshot은 HTTP start/control 응답과 웹소켓 broadcast에 사용된다.

```json
{
  "type": "tick",
  "ok": true,
  "sourceOfTruth": "SWMM",
  "runId": "20260623-120000-ab12cd34",
  "tickLogPath": "swmm_engine/logs/runtime-tick-logs/swmm-runtime-....jsonl",
  "source": "react-editor-json",
  "modelPath": "react_editor_runtime.inp",
  "runtimeModelPath": "react_editor_runtime.runtime.inp",
  "modelTime": "2026-06-16T00:00:01",
  "stepSeconds": 1,
  "stepIndex": 1,
  "control": {},
  "nodes": {},
  "links": {},
  "editorObjects": {},
  "summary": {},
  "risk": {},
  "llmTrigger": {}
}
```

## Node 상태

```json
{
  "CB_1": {
    "id": "CB_1",
    "sourceEditorId": "catchBasin_1",
    "sourceEditorType": "catchBasin",
    "sourceEditorName": "빗물받이 1",
    "depthM": 0.12,
    "headM": 100.12,
    "invertElevationM": 100.0,
    "maxDepthM": 1.2,
    "hydraulicMaxDepthM": 1.2,
    "depthRatio": 0.1,
    "totalInflowCms": 0.005,
    "floodingCms": 0.0
  }
}
```

| 필드                 | 단위  | 설명                     |
| -------------------- | ----- | ------------------------ |
| `depthM`             | m     | 노드 수심                |
| `headM`              | m     | 수두                     |
| `invertElevationM`   | m     | 관저고                   |
| `maxDepthM`          | m     | 표시 기준 최대 수심      |
| `hydraulicMaxDepthM` | m     | SWMM 계산 기준 최대 수심 |
| `depthRatio`         | ratio | `depthM / maxDepthM`     |
| `totalInflowCms`     | m3/s  | 총 유입량                |
| `floodingCms`        | m3/s  | 월류량                   |

## Link 상태

```json
{
  "P_1": {
    "id": "P_1",
    "sourceEditorId": "link_1",
    "sourceEditorType": "relation",
    "sourceEditorName": "관로 1",
    "fromNode": "CB_1",
    "toNode": "OUT_1",
    "flowCms": 0.006,
    "velocityMps": 0.2,
    "depthM": 0.08,
    "fullness": 0.13,
    "capacityCms": 0.3,
    "capacityRatio": 0.02,
    "targetSetting": 1.0,
    "currentSetting": 1.0,
    "blockageRatio": 0.0,
    "direction": "forward"
  }
}
```

| 필드             | 단위   | 설명                        |
| ---------------- | ------ | --------------------------- |
| `flowCms`        | m3/s   | 관로 유량                   |
| `velocityMps`    | m/s    | 표시용 속도                 |
| `depthM`         | m      | 관로 수심                   |
| `fullness`       | ratio  | 관로 수심 / 관경            |
| `capacityCms`    | m3/s   | Manning 기반 추정 만관 용량 |
| `capacityRatio`  | ratio  | 유량 / 용량                 |
| `targetSetting`  | ratio  | 제어 목표 개도              |
| `currentSetting` | ratio  | 현재 개도                   |
| `blockageRatio`  | ratio  | 막힘 비율                   |
| `direction`      | string | `forward` 또는 `reverse`    |

## Editor Object 상태

React editor 객체 하나가 여러 SWMM 객체로 분해될 수 있어 editor 객체별 집계값을
제공한다.

```json
{
  "catchBasin_1": {
    "maxDepthRatio": 0.1,
    "maxFullness": 0.13,
    "maxCapacityRatio": 0.02,
    "maxBlockageRatio": 0.0,
    "maxFloodingCms": 0.0,
    "flowCms": 0.006,
    "maxVelocityMps": 0.2,
    "totalInflowCms": 0.005
  }
}
```

## 위험 판정

위험 판정은 `swmm_engine/risk/risk_context.py`에서 deterministic rule로 수행한다.
기본 정책 레벨은 `balanced`이며 `SUPERMARIO_RISK_POLICY_LEVEL`로
`sensitive`, `balanced`, `strict`를 선택할 수 있다.

주요 기준값은 다음과 같다.

| 항목           | WATCH                                 | WARNING          | CRITICAL                      |
| -------------- | ------------------------------------- | ---------------- | ----------------------------- |
| 노드/관로 fill | 0.50 이상                             | 0.70 이상 지속   | 0.90 이상 또는 surcharge 지속 |
| 관로 capacity  | -                                     | 1.00 이상 지속   | 1.25 이상 지속                |
| 막힘           | 0.50 이상                             | 0.80 이상 지속   | 1.00 이상 지속                |
| 역류           | 정책별 최소 유량과 startup grace 이후 | 정책별 지속 tick | 정책별 지속 tick              |
| 월류           | -                                     | -                | `floodingCms > 0.000001` 지속 |

`balanced` 정책은 시작 직후 30 tick 동안 미세 역류를 안정화 구간으로 보고,
역류 유량과 지속시간 기준을 만족한 뒤에만 위험도를 올린다.

SWMM 런타임 snapshot 자체는 현재 HTTP/WebSocket으로 동일한 구조를 전달하며
상세도 선택 옵션은 없다. 다만 LLM으로 넘기는 분석 context는
`RISK_CONTEXT_LEVEL`에서 `optimal`, `medium`, `full` 중 하나로 조정할 수 있다.

## LLM Context

`llmTrigger.shouldTrigger=true`이면 snapshot에 LLM 분석용 `context`가 포함된다.
context level은 현재 `optimal`이며, 위험 이벤트, 영향 객체, 전역 상태 요약,
정책, raw snapshot 참조 경로를 담는다.

`llm_dispatcher`는 `SUPERMARIO_LLM_ANALYZE_URL`로 다음 JSON을 POST한다.

```json
{
  "id": "호우",
  "swmm_raw_data": "<LLM context JSON string>",
  "TELEGRAM_BOT_TOKEN": "<bot_token row의 bot_token>",
  "TELEGRAM_CHAT_ID": ["<notification_recipients.chat_id>"]
}
```

`id`는 LangChain 서버 계약에 맞춰 `맑음`, `우천`, `호우`, `폭우` 중 하나로
정규화한다. React 강수 preset은 `0 -> 맑음`, `10 -> 우천`,
`100 -> 호우`, `300 -> 폭우`로 변환한다. 이전 라벨 `비옴`/`약한비`가
들어오면 호환을 위해 `우천`으로 변환한다.
명시 ID가 없으면 snapshot/context의 `rainfallRatio` 또는 `rainfallPercent`에서
동일한 preset을 추론한다.

`TELEGRAM_BOT_TOKEN`은 `bot_token` 테이블의 첫 row에서 원문으로 조회하고,
`TELEGRAM_CHAT_ID`는 `notification_recipients`의 모든 `chat_id`를 원문으로
조회한다. `bot_token` row가 없으면 `TELEGRAM_BOT_TOKEN`은 `null`, 대상자가
없으면 `TELEGRAM_CHAT_ID`는 빈 배열이다.

LLM 발송은 `backend/docs/notification-dispatch-policy.md`의 문자 발송 정책을
따른다. 일반 CRITICAL 위험은 `SUPERMARIO_LLM_AGGREGATION_SECONDS` 동안 묶은 뒤
1회 발송하고, 발송 요청 시점부터 `SUPERMARIO_LLM_DISPATCH_COOLDOWN_SECONDS`
동안 새 일반 위험을 pending queue에 누적한다. cooldown 이후 pending queue에
남은 위험은 하나의 묶음 요청으로 발송한다.

runtime risk 기준의 `BLOCKAGE_CLOSED`, `REVERSE_FLOW`는 일반 cooldown 예외이며
`SUPERMARIO_LLM_EMERGENCY_AGGREGATION_SECONDS` 동안 묶어 발송한다. forecast의
`PREDICTED_BLOCKAGE_CLOSED`는 일반 forecast CRITICAL 위험으로 처리한다.

LangChain 응답 대기 시간은 `LLM_DISPATCH_RESPONSE_TIMEOUT_SECONDS` 상수로
관리한다. 응답 timeout은 Telegram/SNS 발송 같은 LangChain 서버 내부 처리가 이미
진행됐을 수 있으므로 `dispatch_failed`가 아니라 `response_timeout` 결과로
기록한다.

## JSONL Tick Log

각 런타임 세션은 다음 경로에 tick log를 남긴다.

```text
backend/swmm_engine/logs/runtime-tick-logs/swmm-runtime-{runId}.jsonl
```

각 줄은 JSON 객체이며 snapshot 또는 runtime event를 담는다. 이 파일은 런타임
산출물이므로 Git 추적 대상이 아니다.

## 이전 명세

예전 문서에 있던 `swmm-section-v1`, `facilities` 배열 기반 최종 출력,
`SimulationRun` 저장 흐름은 `legacy/apps_simulation_legacy`에 남은 테스트용
흐름과 관련이 있다. 현재 공개 엔진 API의 기준 명세는 React editor layout 기반
runtime snapshot이다.
