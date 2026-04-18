# Checklist

## Phase 1. OAuth 연결 보안
- [ ] `oauth_connect_tokens` 테이블 추가
- [ ] signed one-time connect token 생성/검증 로직 추가
- [ ] Google 시작 URL을 `connect_token` 기반으로 전환
- [ ] 만료/재사용/위조 토큰 테스트 추가

## Phase 2. Postgres 런타임 정리
- [x] `psycopg` 의존성 추가
- [x] DB 초기화 시 baseline schema migration 기록 추가
- [x] Postgres 연결 시 engine pre-ping 설정
- [x] `docker-compose`에서 Postgres healthcheck 및 app/worker readiness 반영
- [x] Postgres 관련 회귀 테스트 추가
- [ ] `docker compose up --build` 전체 스모크 테스트

## Phase 3. Reminder Retry Policy
- [ ] `attempt_count` 필드 반영
- [ ] `last_error` 필드 반영
- [ ] `next_attempt_at` 필드 반영
- [ ] `max_attempts` 필드 반영
- [ ] worker 상태 전이 `scheduled/pending -> processing -> sent/failed` 정리
- [ ] 3분 간격 3회 재시도 테스트 추가

## Phase 4. Persona Layer
- [ ] `config/persona/default.yaml` 추가
- [ ] system prompt를 persona profile 로더로 분리
- [ ] persona 교체 테스트 추가

## Phase 5. 운영 문서 정리
- [ ] nginx 운영 설정 정리
- [ ] Certbot 인증서 발급/갱신 절차 문서화
- [ ] Telegram `setWebhook` 등록 절차 문서화
- [ ] healthz/webhook/OAuth 스모크 테스트 절차 문서화

## Phase 6. TTS Abstraction
- [ ] provider-agnostic TTS interface 설계
- [ ] global single TTS profile 설정 경로 추가
- [ ] conversation 후처리 연결 지점 정의
