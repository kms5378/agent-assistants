# Hand-off

## 1. 프로젝트 목적
- Telegram 기반의 대화형 개인 assistant MVP를 완성한다.
- 핵심 기능은 자연어 대화, reminder 생성/조회/삭제/알림, Google Calendar 조회/생성/수정이다.
- 운영은 단일 EC2에서 수행하고, 이후 Discord와 TTS로 확장할 수 있는 구조를 확보한다.

## 2. 현재 상태 요약
- FastAPI app skeleton 존재
- Telegram webhook endpoint 존재
- OpenAI Responses function-calling loop 존재
- reminder CRUD 및 polling worker 존재
- Google OAuth / Calendar 기본 경계 존재
- pytest 기반 기본 테스트 존재
- Dockerfile, docker-compose, nginx 템플릿 존재

## 3. 최우선 후속 작업

### P0. OAuth 연결 보안
- `user_id` 직접 노출 방식 제거
- `signed one-time connect token` 설계 및 구현
- 필요 테이블:
  - `oauth_connect_tokens`
- 필수 컬럼:
  - `token`
  - `user_id`
  - `platform`
  - `expires_at`
  - `used_at`
- 완료 기준:
  - 링크 유출 시에도 타 계정 연결 불가
  - 같은 token 재사용 불가

### P0. Postgres 런타임 정리
- `psycopg` 의존성 추가
- compose 환경에서 app/worker가 Postgres 연결 가능해야 함
- 완료 기준:
  - `docker compose up --build` 후 app/worker 정상 부팅

### P1. Reminder Retry Policy
- 현재 worker를 3분 3회 재시도 정책으로 확장
- 추천 변경:
  - `attempt_count`
  - `last_error`
  - `next_attempt_at`
  - `max_attempts`
- 상태 전이:
  - `scheduled -> processing -> sent`
  - `scheduled -> processing -> pending`
  - `pending -> processing -> failed`
- 완료 기준:
  - 일시적 실패는 자동 재시도
  - 3회 초과 시 최종 `failed`

### P1. Persona Layer
- 하드코딩 system prompt를 `persona profile`로 분리
- 추천 파일:
  - `config/persona/default.yaml`
- 최소 항목:
  - `name`
  - `tone_rules`
  - `style_examples`
  - `response_length_rules`
  - `disallowed_phrases`
  - `safety_disclaimer`
- 완료 기준:
  - 코드 수정 없이 persona 교체 가능

### P2. TTS Abstraction
- 구현은 마지막 단계
- 먼저 provider-agnostic interface만 설계
- 운영 방식:
  - global single profile
  - swappable reference samples
- 완료 기준:
  - provider를 바꿔도 conversation 로직 변경 없음

## 4. 권장 구현 순서
1. OAuth connect token 테이블과 검증 로직 추가
2. Postgres driver, migration baseline, compose smoke test 완료
3. reminder retry policy 반영
4. persona profile 설정 계층 추가
5. 운영용 nginx / certbot / webhook 등록 절차 문서화
6. TTS interface 설계
7. 마지막 단계에서 실제 TTS provider 연결

## 5. 테스트 체크리스트
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

## 6. 운영 체크리스트
- EC2 인스턴스 준비
- Elastic IP 연결
- 도메인 DNS 레코드 연결
- nginx reverse proxy 구성
- Certbot 인증서 발급 및 자동 갱신 검증
- Telegram `setWebhook` 등록
- Google OAuth redirect URI 설정
- `.env` secrets 입력
- healthz / webhook / OAuth smoke test

## 7. 환경 변수 기준
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

## 8. 의사결정 로그
- runtime: `Python 3.11 + FastAPI`
- ingress: Telegram webhook only
- infra: single EC2 + nginx + postgres + worker
- HTTPS: `nginx + Let's Encrypt/Certbot`
- model API: `OpenAI Responses API`
- state: DB source of truth
- TTS: last phase, provider-agnostic, global single profile
- retry policy: 3 minutes, 3 attempts, then failed
- Discord: adapter-ready only, not in MVP

## 9. 인수인계 시 주의점
- 현재 코드에는 `oauth_states`가 있으나 target-state는 `signed one-time connect token` 구조다
- 현재 worker는 retry policy가 아직 fully baked 상태가 아니므로 우선 수정 대상이다
- persona/TTS는 문서상 확정되었지만 코드에는 아직 반영되지 않았다
- 운영 문서는 Certbot과 Telegram webhook 등록 절차까지 반드시 포함해야 한다
