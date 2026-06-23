# 웹소켓 명세서

## 연결 정보

- 개발 URL: `ws://localhost:8000/api/ws/simulation/`
- 운영 URL: `wss://{host}/api/ws/simulation/`
- 프로토콜: JSON text frame
- 인증: 현재 없음
- 서버 그룹명: `simulation`

## 연결 절차

클라이언트가 연결하면 서버는 연결을 수락하고 다음 메시지를 즉시 보낸다.

```json
{
  "code": 200,
  "message": "Simulation socket connected.",
  "status": "OK",
  "data": null
}
```

클라이언트가 별도 구독 메시지를 보낼 필요는 없다. 연결된 모든 클라이언트는 같은
`simulation` 그룹에 참여한다.

## 시뮬레이션 단계 이벤트

PySWMM의 각 계산 간격마다 다음 이벤트를 방송한다.

```json
{
  "code": 200,
  "message": "Simulation step.",
  "status": "OK",
  "data": {
    "schema_version": "2026-06-15-swmm-output-v1",
    "event": "simulation.step",
    "sequence": 1,
    "simulated_at": "2026-01-01T00:00:01",
    "generated_at": "2026-06-15T18:00:00+09:00",
    "interval_seconds": 1,
    "percent_complete": 10.0,
    "rainfall": {
      "status": "HEAVY_RAIN",
      "intensity": 80.0,
      "unit": "mm/hour"
    },
    "facilities": [],
    "nodes": [],
    "links": [],
    "anomalies": []
  }
}
```

LEVEL 4 데모는 SWMM 계산 간격과 실제 방송 간격을 모두 1초로 설정한다. 각 시설은
`water_level_percent`, `blockage_percent`, `status`, `has_failure`를 포함한다.
상세 필드와 상태 판정은 `docs/swmm-spec.md`를 따른다.

LEVEL 5부터 데모 실행 시간은 최대 30초이며 관로 상태에는
`obstruction_type`이 추가된다.

## 시뮬레이션 최종 이벤트

`POST /api/engine/start`가 성공하면 서버가 모든 구독자에게 결과를 방송한다.

```json
{
  "code": 200,
  "message": "Simulation completed.",
  "status": "OK",
  "data": {
    "simulation_id": 1,
    "rainfall_status": "HEAVY_RAIN",
    "rainfall_amount": 80.0,
    "duration_minutes": 30,
    "nodes": [
      {
        "facility_id": 1,
        "name": "catch-basin-1",
        "facility_type": "CATCH_BASIN",
        "depth": 0.14426,
        "flooding": 0.0,
        "depth_ratio": 0.120217,
        "is_anomaly": false
      }
    ],
    "links": [],
    "anomalies": [],
    "has_anomaly": false,
    "engine": "pyswmm",
    "engine_version": "2.1.0"
  }
}
```

## 연결 종료

클라이언트 또는 서버가 연결을 종료하면 해당 채널을 `simulation` 그룹에서
제거한다. 현재 재연결, heartbeat, 메시지 재전송은 클라이언트 책임이다.

## 제한 사항

- 클라이언트 발신 메시지는 현재 처리하지 않는다.
- 실행 이력 재전송 기능은 없다. 누락된 최종 결과는 REST 목록 API로 확인한다.
- In-memory Channel Layer를 사용하므로 서버 프로세스 간 방송을 공유하지 않는다.
- 메시지 버전 필드와 이벤트 타입 필드는 아직 없다.

다중 서버 운영 시 Redis Channel Layer, 인증, heartbeat, 이벤트 버전 관리 정책을
추가해야 한다.
