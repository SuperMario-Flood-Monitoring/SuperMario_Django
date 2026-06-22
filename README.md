# SuperMario SWMM Django Backend

도시 배수도 시나리오를 저장하고, React 클라이언트에서 전달한 배수도 JSON을 SWMM 런타임으로 실행하기 위한 Django 백엔드입니다.

이 레포는 **백엔드 서버**입니다. React 작업장에서 생성한 시나리오를 SQLite DB에 저장하고, Django Ninja API와 WebSocket을 통해 SWMM 엔진 상태와 1초 tick snapshot을 프론트엔드로 전달합니다.

## 핵심 기능

- 시나리오 관리
  - React 편집모드에서 전달한 제목, 설명, 배수도 layout JSON 저장
  - 저장된 시나리오 목록 조회
  - 시나리오 상세 조회, 수정, 삭제
  - soft delete 기반 비활성 시나리오 관리

- SWMM 엔진 실행
  - React layout JSON을 SWMM INP 모델로 변환
  - `stepSeconds: 1` 기준 실시간 엔진 세션 실행
  - 엔진 시작, 일시정지, 재개, 정지, 리셋
  - 강수량, 배속, 객체별 막힘 제어값 반영

- 실시간 전송
  - Channels WebSocket 기반 snapshot broadcast
  - 접속 직후 최신 snapshot 또는 현재 엔진 상태 전송
  - 엔진 loop가 갱신한 마지막 snapshot을 클라이언트에게 주기적으로 전달

- 에디터 변환 API
  - 배수도 JSON 변환 검증
  - INP 단일 파일 다운로드
  - INP, conversion report, mapping JSON ZIP 다운로드

## 현재 시스템 흐름

```text
React 편집모드
  -> title, description, layout_json 전송
  -> Django Scenario DB 저장
  -> React가 시나리오 목록/상세 조회

React 시뮬레이션모드
  -> 선택한 시나리오 layout + control 전송
  -> Django가 layout JSON을 SWMM 모델로 변환
  -> SWMM 런타임 세션 시작
  -> 백그라운드 run loop에서 1초 tick 진행
  -> 최신 snapshot 저장
  -> WebSocket /ws/simulation 으로 snapshot broadcast
```

SWMM 계산 결과의 source of truth는 서버의 `swmm_engine`입니다. React는 강수량, 막힘, 배속 같은 제어값을 전달하고, 서버 snapshot을 화면에 렌더링합니다.

## 기술 스택

| 영역 | 기술 |
| --- | --- |
| Framework | Django 6 |
| API | Django Ninja |
| Realtime | Django Channels, Daphne |
| Database | SQLite |
| SWMM Runtime | PySWMM |
| CORS | django-cors-headers |

## 실행 방법

### 0. Python 버전 확인

이 프로젝트는 `Django==6.0.6`을 사용하므로 Python 3.12 이상이 필요합니다.

```bash
python3 --version
```

### 1. 백엔드 폴더로 이동

```bash
cd backend
```

### 2. 가상환경 생성 및 활성화

```bash
python3.12 -m venv .venv
source .venv/bin/activate
```

이미 `.venv`가 준비되어 있다면 활성화만 하면 됩니다.

### 3. 의존성 설치

```bash
python -m pip install -r requirements.txt
```

### 4. DB 마이그레이션

```bash
python manage.py migrate
```

### 5. Django 개발 서버 실행

```bash
python manage.py runserver 127.0.0.1:8000
```

서버 주소:

```text
http://127.0.0.1:8000/
```

### 6. 서버 상태 확인

```bash
curl http://127.0.0.1:8000/engine/status
```

## API

### Engine API

기본 prefix는 `/engine/`입니다.

| 기능 | Method | Endpoint |
| --- | --- | --- |
| 헬스 체크 | GET | `/engine/health` |
| 엔진 상태 | GET | `/engine/status` |
| 엔진 시작 | POST | `/engine/start` |
| 엔진 리셋 | POST | `/engine/reset` |
| 제어값 변경 | POST | `/engine/control` |
| 엔진 정지 | POST | `/engine/stop` |
| 엔진 일시정지 | POST | `/engine/pause` |
| 엔진 재개 | POST | `/engine/resume` |

### Editor API

기본 prefix는 `/editor/`입니다.

| 기능 | Method | Endpoint |
| --- | --- | --- |
| 변환 검증 | POST | `/editor/convert/validate` |
| INP 다운로드 | POST | `/editor/export-inp` |
| INP/report/mapping ZIP 다운로드 | POST | `/editor/convert/download` |

### Scenario API

기본 prefix는 `/api/`입니다.

| 기능 | Method | Endpoint |
| --- | --- | --- |
| 시나리오 목록 | GET | `/api/scenarios` |
| 시나리오 생성 | POST | `/api/scenarios` |
| 시나리오 상세 | GET | `/api/scenarios/{id}` |
| 시나리오 수정 | PUT | `/api/scenarios/{id}` |
| 시나리오 삭제 | DELETE | `/api/scenarios/{id}` |

비활성 시나리오까지 조회하려면 `includeInactive=true` query parameter를 사용합니다.

```text
GET /api/scenarios?includeInactive=true
```

### WebSocket

```text
ws://127.0.0.1:8000/ws/simulation
```

클라이언트가 연결되면 현재 엔진 snapshot이 있으면 snapshot을, 없으면 엔진 상태 payload를 즉시 전송합니다.

## 주요 요청 예시

### 시나리오 생성

```json
{
  "title": "기본 배수도",
  "description": "편집모드에서 저장한 기본 시나리오",
  "layoutJson": {
    "version": 1,
    "nodes": [],
    "links": []
  }
}
```

### 엔진 시작

```json
{
  "layout": {
    "version": 1,
    "nodes": [],
    "links": []
  },
  "stepSeconds": 1,
  "maxRainfallMmPerHour": 100,
  "control": {
    "rainfallRatio": 0,
    "blockagesById": {},
    "speedMultiplier": 1
  }
}
```

### 제어값 변경

```json
{
  "rainfallRatio": 0.5,
  "blockagesById": {
    "pipe_free_1781771017429": 0.3
  },
  "speedMultiplier": 2
}
```

## 프로젝트 구조

```text
SuperMario_Django/
├── backend/
│   ├── manage.py
│   ├── config/
│   │   ├── settings.py
│   │   ├── urls.py
│   │   └── asgi.py
│   ├── apps/
│   │   ├── scenarios/       # 시나리오 DB 모델, API, service
│   │   ├── simulation/      # 엔진 API, 에디터 변환 API, WebSocket
│   │   ├── facilities/      # 기존 시설 API
│   │   └── common/          # 공통 DTO
│   ├── swmm_engine/
│   │   ├── converter/       # React layout -> SWMM INP 변환
│   │   ├── engine/          # 실시간 SWMM runtime session
│   │   ├── interface.py     # Django에서 호출하는 엔진 interface
│   │   └── logs/            # runtime tick log
│   ├── legacy/              # 팀원 테스트/레거시 코드
│   ├── db.sqlite3
│   └── requirements.txt
└── README.md
```

## 환경 변수

| 이름 | 기본값 | 설명 |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | `django-insecure-development-only` | 개발용 기본 secret key |
| `DJANGO_DEBUG` | `true` | Django debug 모드 |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | 허용 host 목록 |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | React 개발 서버 CORS 허용 origin |
| `SQLITE_PATH` | `backend/db.sqlite3` | SQLite DB 파일 경로 |
| `ENABLE_LEGACY_SIMULATION_API` | `false` | legacy simulation API 활성화 여부 |

> macOS 기본 `python3`가 3.9 계열이면 `Django==6.0.6` 설치가 실패합니다. 이 경우 Homebrew, pyenv 등으로 Python 3.12 이상을 준비한 뒤 가상환경을 생성해야 합니다.

## 개발 기준

- Scenario는 React에서 저장한 배수도 JSON의 서버 측 source data입니다.
- SWMM 계산 결과는 `swmm_engine` runtime snapshot을 기준으로 합니다.
- `stepSeconds: 1` 실시간 계약을 기본값으로 유지합니다.
- React layout 객체 ID와 SWMM 변환 mapping을 임의로 분리하지 않습니다.
- 엔진은 Django view에서 직접 계산하지 않고 `swmm_engine.interface`를 통해 실행합니다.
- WebSocket broadcast는 Channels group event를 사용하며, `swmm.message` event는 consumer의 `swmm_message` handler로 전달됩니다.

## 검증 명령

```bash
python manage.py check
python manage.py migrate --check
```

API smoke test:

```bash
curl http://127.0.0.1:8000/engine/status
curl http://127.0.0.1:8000/api/scenarios
```

## 프론트엔드 연결

React 클라이언트는 별도 레포에서 실행합니다.

```text
/Users/onseoktae/Documents/Team_Supermario/SuperMario_React
```

React의 기본 백엔드 주소는 `http://127.0.0.1:8000`입니다.
