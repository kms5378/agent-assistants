# Implementation Checklist

## Phase 1. Security Foundation
- [x] Google OAuth 연결 방식을 `user_id` 직접 노출 구조에서 제거한다.
- [x] `signed one-time connect token` 또는 동등한 보안 구조를 설계한다.
- [x] `oauth_connect_tokens` 테이블 또는 동등한 저장 구조를 추가한다.
- [x] connect token에 `user_id`, `platform`, `expires_at`, `used_at`를 포함한다.
- [x] 만료된 token 요청을 차단한다.
- [x] 이미 사용된 token 재사용을 차단한다.
- [x] 위조되거나 변조된 token 요청을 차단한다.
- [x] Google OAuth start/callback 테스트 케이스를 추가한다.
- [x] 완료 기준: 링크 유출 또는 재사용으로 다른 사용자 계정 연결이 불가능하다.

## Phase 2. Runtime / DB Foundation
- [ ] Postgres 드라이버를 프로젝트 의존성에 추가한다.
- [ ] 로컬/운영 DB URL 기준을 문서화한다.
- [ ] migration baseline 전략을 정한다.
- [ ] schema 초기화 방식과 migration 방식의 역할을 분리한다.
- [ ] `docker compose` 기준 app가 Postgres에 연결되는지 확인한다.
- [ ] `docker compose` 기준 worker가 Postgres에 연결되는지 확인한다.
- [ ] DB readiness 또는 startup 의존 조건을 정리한다.
- [ ] 완료 기준: `docker compose up --build` 후 app/worker가 정상 부팅한다.

## Phase 3. Reminder Reliability
- [ ] reminder 재시도 정책을 `3분 간격, 최대 3회`로 고정한다.
- [ ] `attempt_count` 저장 필드를 추가한다.
- [ ] `last_error` 저장 필드를 확인 또는 확장한다.
- [ ] `next_attempt_at` 필드를 추가한다.
- [ ] 필요 시 `max_attempts` 필드를 추가한다.
- [ ] worker 조회 조건에 retry 대상과 재시도 시각을 반영한다.
- [ ] 실패 시 `pending`으로 되돌리고 다음 시각을 3분 뒤로 설정한다.
- [ ] 3회 초과 시 최종 상태를 `failed`로 전이한다.
- [ ] 발송 성공 시 recurring reminder 재스케줄링이 유지되는지 확인한다.
- [ ] 재시도 관련 테스트를 추가한다.
- [ ] 완료 기준: 일시적 발송 실패는 자동 재시도되고, 최종 실패만 `failed`로 남는다.

## Phase 4. Telegram MVP Stabilization
- [ ] Telegram webhook secret path 검증을 유지한다.
- [ ] `X-Telegram-Bot-Api-Secret-Token` 검증을 유지한다.
- [ ] duplicate webhook replay 방지가 유지되는지 확인한다.
- [ ] 일반 멀티턴 대화 응답 흐름을 점검한다.
- [ ] reminder 생성/조회/삭제/알림 흐름을 점검한다.
- [ ] recurring reminder 삭제 확인 흐름을 점검한다.
- [ ] Google 미연결 상태에서 `oauth_required` 응답이 나가는지 확인한다.
- [ ] Google Calendar 조회/생성/수정 흐름을 점검한다.
- [ ] summary 생성과 recent message window 동작을 점검한다.
- [ ] 완료 기준: Telegram 중심 MVP 기능이 end-to-end로 안정적으로 동작한다.

## Phase 5. Persona Layer
- [ ] 하드코딩 system prompt를 `persona profile` 설정 계층으로 분리한다.
- [ ] persona 설정 파일 위치를 확정한다.
- [ ] 최소 persona 필드 `name`, `tone_rules`, `style_examples`, `response_length_rules`, `disallowed_phrases`, `safety_disclaimer`를 정의한다.
- [ ] ConversationService가 persona profile을 읽어 prompt를 구성하도록 변경한다.
- [ ] 코드 수정 없이 profile만 바꿔 응답 스타일을 바꿀 수 있게 한다.
- [ ] persona 관련 테스트를 추가한다.
- [ ] 완료 기준: profile 교체만으로 응답 스타일이 바뀐다.

## Phase 6. Operations Hardening
- [ ] EC2 인스턴스를 준비한다.
- [ ] Elastic IP를 연결한다.
- [ ] 도메인 DNS를 EC2로 연결한다.
- [ ] nginx reverse proxy 설정을 실제 도메인 기준으로 수정한다.
- [ ] Certbot 인증서를 발급한다.
- [ ] Certbot 자동 갱신을 검증한다.
- [ ] Telegram `setWebhook`를 등록한다.
- [ ] Google OAuth redirect URI를 운영 도메인 기준으로 설정한다.
- [ ] `.env` 운영 secrets를 채운다.
- [ ] `healthz`, webhook, OAuth redirect smoke test를 수행한다.
- [ ] 완료 기준: 실도메인과 HTTPS가 연결된 상태로 Telegram webhook이 정상 수신된다.

## Phase 7. TTS Abstraction
- [ ] TTS는 마지막 단계에서만 구현한다.
- [ ] provider-agnostic interface를 먼저 정의한다.
- [ ] TTS는 `global single profile` 구조로 설계한다.
- [ ] `voice preset`, `reference samples`, `synthesis settings`를 분리 저장한다.
- [ ] `render_text -> normalize_for_tts -> synthesize_audio` 파이프라인을 설계한다.
- [ ] profile과 sample 교체가 대화 로직 수정 없이 가능한지 확인한다.
- [ ] 이후 실제 TTS provider를 연결한다.
- [ ] TTS 관련 테스트를 추가한다.
- [ ] 완료 기준: provider를 바꿔도 conversation 로직은 수정하지 않는다.

## Cross-cutting Validation
- [ ] Telegram adapter 바깥 서비스 레이어가 채널 독립적으로 유지되는지 확인한다.
- [ ] Discord adapter 추가 여지가 구조적으로 보장되는지 확인한다.
- [ ] DB source of truth 원칙이 유지되는지 확인한다.
- [ ] OpenAI에는 전체 로그가 아니라 summary + recent turns만 전달되는지 확인한다.
- [ ] README, `AGENTS.md`, `api-spec.md`, `workflow.md`, `hand-off.md`가 최신 상태인지 확인한다.
