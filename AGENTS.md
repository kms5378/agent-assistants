# AGENTS

## Start Here
- 이 저장소에서 작업을 시작할 때는 먼저 이 문서를 읽는다.
- 구현, 수정, 리뷰 전에는 아래 문서를 추가로 확인한다.
- `api-spec.md`: 외부/내부 API와 target-state 계약
- `workflow.md`: 런타임 흐름, 구현 순서, 운영 배포 흐름
- `hand-off.md`: 우선순위, 남은 작업, 인수인계 기준
- `checklist.md`: phase별 실행 체크리스트
- 구현 중 현재 코드와 문서가 다르면, 임의로 해석하지 말고 `현재 구현`과 `target-state`를 구분해서 반영한다.
- 새로운 작업을 받을 때 별도 지시가 없어도 본 문서와 연결 문서를 source of truth로 따른다.
- 작업이 끝나면 `checklist.md`에 문서 기준으로 완료된 항목만 즉시 체크한다.

## 목적
- 이 문서는 대화형 멀티채널 Assistant MVP의 주요 에이전트와 서비스 경계를 정의한다.
- 범위는 Telegram 중심 MVP, Google Calendar 연동, reminder worker, persona/TTS 확장 구조를 포함한다.
- Discord는 이번 단계에서 구현하지 않지만 adapter 추가만으로 확장 가능해야 한다.

## 공통 원칙
- 모든 에이전트는 내부 `user`를 기준으로 동작하고, 채널 계정은 `channel_accounts`로 매핑한다.
- 대화의 시스템 원본은 DB이며, 모델에는 `최근 대화 + 요약 + 현재 요청 + tool schema`만 전달한다.
- 외부 API 실패는 에이전트 내부에서 삼키지 말고 구조화된 오류 결과로 반환한다.
- 채널별 포맷 차이는 adapter가 흡수하고, conversation/tool/reminder 로직은 채널 SDK에 직접 의존하지 않는다.
- persona와 TTS는 코드 하드코딩이 아니라 설정 계층으로 관리한다.
- 체크리스트 갱신은 구현 완료 절차의 일부로 간주한다.

## Git Commit Rules
- 이 저장소의 커밋 메시지는 항상 한국어로 작성한다.
- 기본 형식은 `타입: 변경 내용`을 사용한다.
- 커밋 메시지는 변경 내용을 짧고 명확하게 설명한다.
- 한 커밋에는 하나의 작업만 담는 것을 원칙으로 한다.
- 의미가 불분명한 메시지는 사용하지 않는다.
- 예: `update code`, `fix bug` 같은 모호한 메시지는 사용하지 않는다.

### Commit 타입
- `feat`: 새로운 기능 추가
- `fix`: 버그 수정
- `docs`: 문서 수정
- `style`: 코드 스타일 수정, 포맷 변경, 기능 변경 없음
- `refactor`: 기능 변화 없는 구조 개선
- `test`: 테스트 추가 또는 수정
- `chore`: 빌드, 설정, 기타 유지보수 작업

### Commit 메시지 예시
- `feat: 텔레그램 리마인더 생성 기능 추가`
- `fix: 구글 OAuth 상태 검증 오류 수정`
- `docs: API 명세서 문구 정리`
- `refactor: 대화 서비스 도구 라우팅 구조 개선`
- `test: 리마인더 재시도 시나리오 테스트 추가`

## Agent Catalog

### 1. Channel Adapter Agent
- 책임: 외부 채널 payload를 내부 `InboundEvent`로 변환하고, 내부 `OutboundMessage`를 채널 API 호출로 전송한다.
- 현재 구현 대상: `TelegramAdapter`
- 미래 확장: `DiscordAdapter`
- 입력: Telegram webhook payload
- 출력: `InboundEvent`, 채널 발신 결과
- 소유 규칙:
  - 채널별 인증 헤더 검증
  - 채널 메시지 포맷 파싱
  - 채널별 message length limit, reply thread, mention 포맷 흡수
- 비책임:
  - reminder 비즈니스 로직
  - Google OAuth 토큰 관리
  - persona 문장 생성

### 2. Conversation Agent
- 책임: 자연어 대화를 오케스트레이션하고 model/tool loop를 실행해 최종 응답을 만든다.
- 구현 중심: `ConversationService`
- 입력: `InboundEvent`
- 출력: `OutboundMessage[]`
- 처리 단계:
  - 내부 사용자 식별 또는 생성
  - inbound message 저장 및 idempotency 검사
  - 최근 대화와 summary 조합
  - Responses API 호출
  - tool call이 있으면 Tool Router로 위임
  - tool output을 다시 모델에 전달
  - final assistant message 저장 후 반환
- 정책:
  - 한국어 우선, 영어 보조
  - 시간 표현이 모호하면 tool 호출 전에 후속 질문
  - 삭제 대상이 여러 개면 추측하지 않고 후보 확인
  - Google 미연결 상태면 연결 링크 안내

### 3. Tool Router Agent
- 책임: 모델의 function call을 내부 서비스 호출로 라우팅하고, 구조화된 결과를 반환한다.
- 구현 중심: `ToolRouter`
- 입력: `ModelToolCall`, `InternalUser`, `InboundEvent`
- 출력: tool result JSON
- 현재 지원 tool:
  - `reminder_create`
  - `reminder_search`
  - `reminder_delete`
  - `reminder_list`
  - `calendar_list_events`
  - `calendar_create_event`
  - `calendar_update_event`
- 정책:
  - reminder와 calendar 결과는 모델이 후처리 가능한 구조화된 dict 형태로 반환
  - OAuth 필요 시 `oauth_required`와 connect URL을 함께 반환
  - unknown tool은 명시적 error 응답으로 종료

### 4. Reminder Scheduler Agent
- 책임: reminder 생성, 조회, 삭제, 재스케줄링, 발송 이력 기록을 담당한다.
- 구현 중심: `ReminderService`
- 입력: tool arguments 또는 worker claim 요청
- 출력: reminder result, due reminder batch
- 지원 범위:
  - one-time
  - daily
  - weekly
  - monthly
- 삭제 규칙:
  - 단일 매칭: 즉시 삭제
  - 다중 매칭: ambiguity 반환
  - 반복 reminder: `single` vs `series` 확인 필요
- 시간 규칙:
  - `due_at_local`은 ISO 8601
  - timezone 미지정 시 user timezone 사용
  - 과거 시각 1회성 reminder는 거절

### 5. Reminder Delivery Worker Agent
- 책임: due reminder를 polling하고 채널로 발송한 뒤 상태를 전이한다.
- 구현 중심: `app.worker`
- 실행 방식: 단일 프로세스 polling worker
- claim 규칙:
  - 상태가 `scheduled` 또는 `pending`
  - `next_fire_at <= now()`
  - Postgres에서는 `FOR UPDATE SKIP LOCKED` 사용
- delivery 규칙:
  - 성공: one-time은 `sent`, recurring은 다음 실행 시각 계산 후 `scheduled`
  - 실패: 3분 간격으로 최대 3회 재시도, 이후 `failed`
- 저장 대상:
  - `reminders.status`
  - `reminder_deliveries`
  - `last_error`, `attempt_count`, `delivered_at`

### 6. Google OAuth / Calendar Agent
- 책임: Google 계정 연결, refresh token 관리, Calendar API 조회/생성/수정 수행
- 구현 중심: `GoogleOAuthService`
- 입력: OAuth authorization code, user_id, calendar tool arguments
- 출력: access token, event list, event create/update result
- 보안 규칙:
  - connect URL은 `user_id` 직접 노출이 아니라 `서명된 1회용 connect token` 기반
  - refresh token은 앱 키로 암호화 저장
  - scope는 최소 권한 원칙 적용
- 기본 scope:
  - `openid`
  - `email`
  - `profile`
  - `https://www.googleapis.com/auth/calendar.events`

### 7. Persona Profile Agent
- 책임: 텍스트 페르소나 정책을 설정 기반으로 제공한다.
- 상태: 설계 확정, 구현 예정
- 입력: profile version, language, channel context
- 출력: prompt fragments, style examples, guardrails
- 포함 항목:
  - tone rules
  - response length rules
  - empathy rules
  - disallowed phrases
  - safety disclaimer
- 정책:
  - 특정 실존 인물 본인이라고 주장하지 않음
  - profile 교체 시 코드 수정 없이 반영 가능

### 8. TTS Rendering Agent
- 책임: 텍스트 응답을 TTS-friendly text로 정규화하고 선택된 공급자에 전달한다.
- 상태: 마지막 단계에서 구현
- 설계 원칙:
  - provider-agnostic interface
  - 전역 단일 TTS profile
  - audio samples, voice presets, synthesis settings 분리 관리
- 기본 파이프라인:
  - `render_text`
  - `normalize_for_tts`
  - `synthesize_audio`
- 교체 가능 항목:
  - provider
  - voice preset
  - reference samples
  - speed / pause / intonation rules

## Shared Contracts
- `InboundEvent`: 플랫폼, 사용자, 대화, 메시지, raw payload 정보를 담는 공통 입력
- `InternalUser`: 내부 user identity와 timezone을 담는 공통 사용자 컨텍스트
- `OutboundMessage`: 채널 발신용 최소 메시지 구조
- `ModelToolCall`: model이 요청한 function call 구조
- `ModelTurnResponse`: model text + tool calls 응답 구조

## 상태 소유권
- `messages`, `conversation_summaries`: Conversation Agent 소유
- `reminders`, `reminder_deliveries`: Reminder Scheduler / Worker 소유
- `oauth_accounts`, `oauth connect token/state`: Google OAuth Agent 소유
- `channel_accounts`: Channel Adapter와 Conversation Agent가 공동 갱신
- `persona profile`, `tts profile`: 설정 계층 소유, 런타임은 읽기 전용

## 확장 규칙
- Discord 추가 시 새 adapter만 만들고 Conversation Agent, Tool Router, Reminder Worker는 재사용한다.
- TTS 추가 시 기존 대화 응답 생성 경로를 깨지 않고 post-processing 계층으로 연결한다.
- 새 tool을 추가할 때는 tool schema, router execute, service, test를 항상 함께 확장한다.
