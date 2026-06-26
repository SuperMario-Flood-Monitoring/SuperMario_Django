# 데이터베이스 구조

## 문서 정보

- DBMS: PostgreSQL for Docker/production, SQLite fallback for bare local Python
- Django ORM: Django 6.0.6
- Python 단독 실행 기본 DB: `db.sqlite3`
- Docker/production DB: PostgreSQL `postgres_data` volume
- 시간대: `Asia/Seoul`

## 데이터베이스 설정

`DATABASE_ENGINE=postgres`이면 PostgreSQL을 사용하고, 그 외에는 SQLite를 사용한다.

| 환경 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `DATABASE_ENGINE` | `sqlite` | `sqlite` 또는 `postgres` |
| `SQLITE_PATH` | `BASE_DIR / db.sqlite3` | SQLite 파일 경로 |
| `POSTGRES_DB` | `supermario` | PostgreSQL DB 이름 |
| `POSTGRES_USER` | `supermario` | PostgreSQL 사용자 |
| `POSTGRES_PASSWORD` | 빈 문자열 | PostgreSQL 비밀번호 |
| `POSTGRES_HOST` | `postgres` | PostgreSQL 호스트 |
| `POSTGRES_PORT` | `5432` | PostgreSQL 포트 |

## ERD

```mermaid
erDiagram
    FACILITY {
        bigint id PK
        varchar name UK
        varchar facility_type
        varchar location
        float normal_value
        varchar unit
        json metadata
        boolean is_active
        datetime created_at
        datetime updated_at
    }

    SCENARIO {
        bigint id PK
        varchar title
        text description
        json layout_json
        integer version
        boolean is_active
        datetime created_at
        datetime updated_at
    }

    USERS {
        bigint USER_ID PK
        varchar ROLE
        varchar USERNAME UK
        varchar PASSWORD
        varchar REFRESH_TOKEN
    }

    NOTIFICATIONS {
        bigint TOKEN_ID PK
        varchar NAME UK
        varchar TELEGRAM_TOKEN
    }

    BOT_TOKEN {
        bigint id PK
        varchar bot_token
    }

    NOTIFICATION_RECIPIENTS {
        bigint id PK
        varchar employee_name
        varchar chat_id
    }
```

현재 두 테이블 사이에 외래 키는 없다. SWMM 런타임 snapshot과 tick log는 DB가
아니라 `swmm_engine/logs/runtime-tick-logs/*.jsonl` 파일에 기록된다.

## Facility

클라이언트 초기화 API에서 전달받은 시설 기준값과 확장 metadata를 저장한다.

| 컬럼 | Django 타입 | Null | 기본값 | 제약/설명 |
| --- | --- | --- | --- | --- |
| `id` | BigAutoField | 아니요 | 자동 증가 | PK |
| `name` | CharField(100) | 아니요 | 없음 | Unique |
| `facility_type` | CharField(30) | 아니요 | `OTHER` | 시설 유형 선택값 |
| `location` | CharField(255) | 아니요 | 빈 문자열 | 위치 설명 |
| `normal_value` | FloatField | 아니요 | `0.0` | 정상 상태 기준값 |
| `unit` | CharField(20) | 아니요 | 빈 문자열 | 기준값 단위 |
| `metadata` | JSONField | 아니요 | `{}` | SWMM ID, 임계값 등 확장 데이터 |
| `is_active` | BooleanField | 아니요 | `true` | 활성 여부 |
| `created_at` | DateTimeField | 아니요 | 생성 시각 | 자동 기록 |
| `updated_at` | DateTimeField | 아니요 | 수정 시각 | 자동 갱신 |

허용 `facility_type` 값은 다음과 같다.

| 값 | 설명 |
| --- | --- |
| `DRAINAGE_PIPE` | 배수관 |
| `CATCH_BASIN` | 빗물받이 |
| `MANHOLE` | 맨홀 |
| `PUMP` | 펌프 |
| `OTHER` | 기타 |

기본 조회 순서는 `id` 오름차순이다. 시설 삭제 API는 현재 hard delete를 수행한다.

## Scenario

React 편집모드에서 저장한 배수도 layout JSON을 보관한다.

| 컬럼 | Django 타입 | Null | 기본값 | 제약/설명 |
| --- | --- | --- | --- | --- |
| `id` | BigAutoField | 아니요 | 자동 증가 | PK |
| `title` | CharField(100) | 아니요 | 없음 | 시나리오 제목 |
| `description` | TextField | 아니요 | 빈 문자열 | 시나리오 설명 |
| `layout_json` | JSONField | 아니요 | 없음 | React editor layout JSON |
| `version` | PositiveIntegerField | 아니요 | `1` | layout 변경 시 1 증가 |
| `is_active` | BooleanField | 아니요 | `true` | soft delete 여부 |
| `created_at` | DateTimeField | 아니요 | 생성 시각 | 자동 기록 |
| `updated_at` | DateTimeField | 아니요 | 수정 시각 | 자동 갱신 |

기본 조회 순서는 `updated_at` 내림차순, `id` 내림차순이다. 삭제 API는
`is_active=false`로 변경하는 soft delete를 수행한다.

## 마이그레이션

현재 마이그레이션 파일은 다음과 같다.

| 앱 | 파일 |
| --- | --- |
| `custom_auth` | `apps/auth/migrations/0001_initial.py` |
| `facilities` | `apps/facilities/migrations/0001_initial.py` |
| `notification` | `apps/notification/migrations/0001_initial.py` |
| `scenarios` | `apps/scenarios/migrations/0001_initial.py` |

`apps/simulation`에는 현재 활성 모델과 마이그레이션이 없다. 예전
`SimulationRun` 모델은 legacy 코드에 남아 있으나 기본 앱 라우팅과 DB 설계의
현재 기준에는 포함하지 않는다.

스키마를 바꾸는 작업은 모델 수정, `makemigrations`, `migrate` 또는
`migrate --check`, 관련 문서 갱신을 같은 작업 단위로 처리한다.

## Users

JWT 로그인용 커스텀 사용자 테이블이다. Django 기본 `auth_user`는 사용하지 않는다.

| 컬럼 | Django 타입 | Null | 기본값 | 제약/설명 |
| --- | --- | --- | --- | --- |
| `USER_ID` | BigAutoField | 아니요 | 자동 증가 | PK |
| `ROLE` | CharField(20) | 아니요 | 없음 | `ADMIN`, `MEMBER` |
| `USERNAME` | CharField(150) | 아니요 | 없음 | Unique, 로그인 ID |
| `PASSWORD` | CharField(128) | 아니요 | 없음 | Django password hasher 결과 |
| `REFRESH_TOKEN` | CharField(128) | 예 | `NULL` | refresh token HMAC-SHA256 hash |

초기 ADMIN 계정은 management command로 생성한다. Docker 서버 시작 시에는
`migrate` 이후 `ensure_admin_user --only-if-no-admin`가 자동 실행된다. 기본
`admin` row가 이미 있으면 기본 비밀번호 `supermario4`로 갱신한다.
다른 ADMIN 사용자가 이미 있고 기본 `admin` row가 없으면 생성을 건너뛴다.

```bash
python manage.py ensure_admin_user --username admin --password "<password>"
python manage.py ensure_admin_user --only-if-no-admin
```

## Notifications

`apps.auth.models.Notification`에 남아 있는 이전 Telegram token 테이블이다.
현재 LEVEL 17 알림 전송 기준은 아래 `bot_token`, `notification_recipients`
테이블이다.

| 컬럼 | Django 타입 | Null | 기본값 | 제약/설명 |
| --- | --- | --- | --- | --- |
| `TOKEN_ID` | BigAutoField | 아니요 | 자동 증가 | PK |
| `NAME` | CharField(150) | 아니요 | 없음 | Unique |
| `TELEGRAM_TOKEN` | CharField(255) | 아니요 | 없음 | Telegram token |

## Bot Token

문자를 발송하는 Telegram bot token을 원문으로 저장한다. 운영자가 직접 1개 row를
삽입해 사용한다.

| 컬럼 | Django 타입 | Null | 기본값 | 제약/설명 |
| --- | --- | --- | --- | --- |
| `id` | BigAutoField | 아니요 | 자동 증가 | PK |
| `bot_token` | CharField(255) | 아니요 | 없음 | Telegram bot token 원문 |

## Notification Recipients

Telegram 알림을 받을 인원을 저장한다. ADMIN 사용자는 API로 생성, 전체 조회,
삭제할 수 있다.

| 컬럼 | Django 타입 | Null | 기본값 | 제약/설명 |
| --- | --- | --- | --- | --- |
| `id` | BigAutoField | 아니요 | 자동 증가 | PK |
| `employee_name` | CharField(100) | 아니요 | 없음 | 수신자 이름 |
| `chat_id` | CharField(100) | 아니요 | 없음 | Telegram chat ID 원문 |
