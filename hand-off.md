# Hand-off

## 1. 프로젝트 목적
- Telegram 기반의 대화형 개인 assistant MVP를 완성한다.
- 핵심 기능은 자연어 대화, reminder 생성/조회/삭제/알림, Google Calendar 조회/생성/수정이다.
- 운영은 단일 EC2에서 수행하고, 이후 Discord와 TTS로 확장할 수 있는 구조를 확보한다.

## 2. 현재 상태 요약
- 2026-04-18 기준 Phase 1부터 Phase 5까지 완료
- FastAPI app skeleton, Telegram webhook endpoint, OpenAI Responses function-calling loop 동작
- Google OAuth 연결이 `signed one-time connect token` 기반으로 전환됨
- Postgres 런타임 정리가 반영되어 `psycopg`, baseline schema migration 기록, pre-ping, compose healthcheck/readiness가 포함됨
- reminder CRUD, polling worker, 3분 간격 최대 3회 재시도 정책이 반영됨
- 하드코딩 system prompt가 persona profile 설정 계층으로 분리되었고 기본 프로필은 `config/persona/default.yaml`로 관리됨
- `PERSONA_PROFILE_PATH`로 persona 교체 가능
- `operations.md`, `docker-compose.prod.yml`, `nginx/nginx.conf` 기준의 운영 배포 문서가 추가됨
- Certbot 발급/갱신, Telegram `setWebhook`, healthz/webhook/OAuth 스모크 테스트 절차가 문서화됨
- `scripts/ops/`에 Certbot renew, Telegram webhook, smoke test 자동화 스크립트가 추가됨
- `deploy/systemd/`에 Certbot renew service/timer 템플릿이 추가됨
- pytest 기반 회귀 테스트가 OAuth connect token, Postgres runtime, retry policy, persona 교체 시나리오까지 포함하도록 확장됨
- Dockerfile, docker-compose, nginx 템플릿 존재

## 3. 이번 턴까지 완료된 작업

### 완료. OAuth 연결 보안
- `oauth_connect_tokens` 테이블 및 signed one-time connect token 생성/검증 로직 반영
- Google 시작 URL이 `connect_token` 기반으로 동작하도록 전환
- 만료 / 재사용 / 위조 토큰 테스트 추가

### 완료. Postgres 런타임 정리
- `psycopg` 의존성 추가
- DB 초기화 시 baseline schema migration 기록 추가
- Postgres 연결 시 engine pre-ping 설정 반영
- `docker-compose`에서 Postgres healthcheck 및 app/worker readiness 반영
- Postgres 관련 회귀 테스트와 compose 스모크 테스트 기준 반영

### 완료. Reminder Retry Policy
- `attempt_count`, `last_error`, `next_attempt_at`, `max_attempts` 필드 반영
- worker 상태 전이를 `scheduled/pending -> processing -> sent/failed`로 정리
- 3분 간격 3회 재시도 테스트 추가

### 완료. Persona Layer
- 하드코딩 system prompt를 persona profile 로더로 분리
- 기본 persona 파일 `config/persona/default.yaml` 추가
- `PERSONA_PROFILE_PATH` 환경 변수 경로 교체 기반 persona 변경 지원
- persona 교체 테스트 추가

### 완료. 운영 문서 정리
- `operations.md`에 EC2 배포, nginx, Certbot, Telegram `setWebhook`, 스모크 테스트 절차 추가
- `docker-compose.prod.yml`에 운영용 app/worker/postgres/nginx 스택 예시 추가
- `nginx/nginx.conf`를 운영 HTTPS reverse proxy 기준으로 정리
- `README.md`에 로컬 compose와 운영 compose 사용 경로를 분리해 안내

### 완료. 운영 자동화 보강
- `scripts/ops/certbot-renew.sh`로 nginx stop/start를 포함한 Certbot renew 자동화 추가
- `scripts/ops/install-certbot-timer.sh`와 `deploy/systemd/` 템플릿으로 systemd timer 설치 경로 추가
- `scripts/ops/telegram-webhook.sh`로 Telegram webhook 등록/검증 자동화 추가
- `scripts/ops/smoke-test.sh`로 healthz/webhook/OAuth 스모크 테스트 자동화 추가
- 운영 자동화 스크립트 회귀 테스트 추가

## 4. 최우선 후속 작업

### P1. 실서버 배포 리허설
- EC2 실제 경로 기준으로 systemd timer 설치
- 운영 `.env`와 도메인으로 webhook 등록/검증 실행
- 운영 URL 대상으로 smoke test 실행
- 완료 기준:
  - 문서와 스크립트만으로 신규 서버에서 재현 가능한 배포가 검증되어야 함

### P2. TTS Abstraction
- 구현은 마지막 단계
- 먼저 provider-agnostic interface만 설계
- 운영 방식:
  - global single profile
  - swappable reference samples
- 완료 기준:
  - provider를 바꿔도 conversation 로직 변경 없음

## 5. 남은 구현 순서
1. EC2 실제 경로 기준 systemd timer 설치 및 dry-run 검증
2. 운영 URL로 Telegram webhook 등록/검증 실행
3. 운영 URL로 smoke test 실행
4. TTS interface 설계
5. global single TTS profile 경로 추가
6. conversation 후처리 연결 지점 정의
7. 마지막 단계에서 실제 TTS provider 연결

## 6. 테스트 체크리스트
- webhook secret/path 검증
- duplicate Telegram update replay 방지
- reminder create / search / delete / list
- recurring reminder single vs series 삭제
- due reminder 발송 성공
- due reminder 발송 실패 후 3분 3회 재시도
- Google 미연결 상태에서 oauth_required 반환
- connect token 만료 / 재사용 / 위조 케이스
- persona profile 교체 후 응답 스타일 변경
- Discord adapter가 없어도 service layer가 channel-agnostic 유지

## 7. 운영 체크리스트
- EC2 인스턴스 준비
- Elastic IP 연결
- 도메인 DNS 레코드 연결
- nginx reverse proxy 구성
- Certbot 인증서 발급 및 자동 갱신 검증
- Telegram `setWebhook` 등록
- Google OAuth redirect URI 설정
- `.env` secrets 입력
- healthz / webhook / OAuth smoke test

## 8. 환경 변수 기준
- `APP_BASE_URL`
- `DATABASE_URL`
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_WEBHOOK_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI`
- `APP_ENCRYPTION_KEY`
- target-state 추가:
  - `PERSONA_PROFILE_PATH`
  - `TTS_PROFILE_PATH`
  - `TTS_PROVIDER`

## 9. 의사결정 로그
- runtime: `Python 3.11 + FastAPI`
- ingress: Telegram webhook only
- infra: single EC2 + nginx + postgres + worker
- HTTPS: `nginx + Let's Encrypt/Certbot`
- model API: `OpenAI Responses API`
- state: DB source of truth
- TTS: last phase, provider-agnostic, global single profile
- retry policy: 3 minutes, 3 attempts, then failed
- Discord: adapter-ready only, not in MVP

## 10. 인수인계 시 주의점
- OAuth 시작 진입점은 이미 `connect_token` 기반이므로, 이후 수정 시 `user_id` 직접 노출 방식으로 되돌리지 않도록 주의
- reminder worker는 3분 간격 최대 3회 재시도 정책이 반영되어 있으므로, 상태 전이와 `next_attempt_at` 처리 규칙을 함께 유지해야 함
- persona는 코드 상수 수정이 아니라 `config/persona/*.yaml`과 `PERSONA_PROFILE_PATH`로 교체하는 구조를 유지해야 함
- 로컬 compose는 `docker-compose.yml` + `nginx/docker-compose.conf`, 운영 compose는 `docker-compose.prod.yml` + `nginx/nginx.conf` 조합을 유지해야 함
- 운영 자동화 수정 시 `scripts/ops/`, `deploy/systemd/`, `operations.md`를 함께 갱신해야 함
- TTS는 마지막 단계로 유지하고, 그 전에는 실서버 배포 리허설과 운영 검증을 먼저 끝내는 순서를 유지해야 함
