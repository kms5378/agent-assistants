# API Specification

## 1. 문서 범위
- 본 문서는 외부 HTTP API, 내부 tool API, 핵심 데이터 계약을 정의한다.
- 기준 버전은 Telegram 중심 MVP와 Google Calendar / reminder 기능을 포함한 최종 확정본 플랜이다.
- TTS 관련 API는 설계만 확정하고 구현은 마지막 단계로 미룬다.

## 2. 공통 규칙
- Base URL 예시: `https://assistant.example.com`
- 모든 시간은 저장 시 UTC 기준, 사용자 입력/응답은 user timezone 기준
- 기본 timezone fallback: `Asia/Seoul`
- 외부 API 응답은 JSON 기본, OAuth callback만 HTML 허용
- webhook은 idempotent 해야 하며 동일 `update_id`는 1회만 처리한다

## 3. External HTTP API

### 3.1 Health Check
- Method: `GET`
- Path: `/healthz`
- Auth: 없음
- Response `200`

```json
{
  "status": "ok"
}
```

### 3.2 Telegram Webhook
- Method: `POST`
- Path: `/webhooks/telegram/{webhook_key}`
- Auth:
  - path secret: `{webhook_key}`
  - header: `X-Telegram-Bot-Api-Secret-Token`
- Request Body: Telegram Update JSON
- Success Response `200`

```json
{
  "ok": true,
  "messages_sent": 1
}
```

- Ignored Response `200`

```json
{
  "ok": true,
  "ignored": true
}
```

- Error Response
  - `403`: invalid secret header
  - `404`: invalid webhook path

### 3.3 Google OAuth Start
- Method: `GET`
- Path: `/auth/google/start`
- Query:
  - `connect_token` required
- Auth: 없음. 단, `connect_token` 자체가 서명된 1회용 토큰이어야 함
- 동작:
  - 토큰 유효성 검증
  - Google authorization URL 생성
  - 사용자 브라우저 redirect
- Success Response
  - `302 Redirect` to Google OAuth consent screen
- Error Response
  - `400`: invalid / expired / already-used connect token

### 3.4 Google OAuth Callback
- Method: `GET`
- Path: `/auth/google/callback`
- Query:
  - `state` required
  - `code` required
- Success Response `200`
  - HTML page: 연결 완료 안내
- Error Response
  - `400`: invalid or expired state
  - `502`: Google token exchange failure

## 4. Internal Model Tool API

### 4.1 `reminder_create`
- 목적: 자연어에서 파싱된 reminder를 저장
- Required Fields:
  - `title: string`
  - `due_at_local: string`
  - `timezone: string`
  - `recurrence: object`
  - `delivery_channel: string`
- Optional Fields:
  - `notes: string`

#### Recurrence Schema

```json
{
  "type": "none | daily | weekly | monthly",
  "days_of_week": [0, 1, 2],
  "day_of_month": 15,
  "local_time": "09:00"
}
```

#### Success Result

```json
{
  "status": "ok",
  "reminder": {
    "id": "uuid",
    "title": "약 먹기",
    "timezone": "Asia/Seoul",
    "due_at": "2026-04-20T09:00:00+09:00",
    "next_fire_at": "2026-04-20T00:00:00+00:00",
    "status": "scheduled",
    "recurrence_type": "none",
    "recurrence_rule": {
      "type": "none",
      "days_of_week": null,
      "day_of_month": null,
      "local_time": null
    },
    "notes": ""
  }
}
```

#### Error Result

```json
{
  "status": "error",
  "message": "due_at_local must be in the future"
}
```

### 4.2 `reminder_search`
- 목적: 삭제 전 후보 탐색
- Required Fields:
  - `query: string`

#### Success Result

```json
{
  "status": "ok",
  "items": [
    {
      "id": "uuid",
      "title": "약 먹기",
      "timezone": "Asia/Seoul",
      "due_at": "2026-04-20T09:00:00+09:00",
      "next_fire_at": "2026-04-20T00:00:00+00:00",
      "status": "scheduled",
      "recurrence_type": "none",
      "recurrence_rule": {
        "type": "none"
      },
      "notes": ""
    }
  ]
}
```

### 4.3 `reminder_delete`
- 목적: reminder 삭제 또는 recurring occurrence skip
- Optional Fields:
  - `reminder_id: string`
  - `query: string`
  - `delete_scope: "single" | "series"`
- 규칙:
  - `reminder_id` 또는 `query` 중 하나는 필수
  - recurring reminder는 `delete_scope` 없으면 `needs_confirmation`

#### Ambiguity Result

```json
{
  "status": "ambiguity",
  "message": "Multiple reminders matched. Ask the user to choose one.",
  "candidates": [
    {
      "id": "uuid-1",
      "title": "약 먹기"
    },
    {
      "id": "uuid-2",
      "title": "약 사기"
    }
  ]
}
```

#### Confirmation Result

```json
{
  "status": "needs_confirmation",
  "message": "This is a recurring reminder. Ask whether to delete this occurrence or the entire series.",
  "reminder": {
    "id": "uuid",
    "title": "월요일 알림"
  }
}
```

### 4.4 `reminder_list`
- 목적: upcoming reminder 목록 조회
- Request Fields: 없음

### 4.5 `calendar_list_events`
- Required Fields:
  - `start_local: string`
  - `end_local: string`
  - `timezone: string`
- Optional Fields:
  - `calendar_id: string = "primary"`

#### Success Result

```json
{
  "status": "ok",
  "items": [
    {
      "id": "event-id",
      "title": "회의",
      "start": "2026-04-20T10:00:00+09:00",
      "end": "2026-04-20T11:00:00+09:00"
    }
  ]
}
```

### 4.6 `calendar_create_event`
- Required Fields:
  - `title`
  - `start_local`
  - `end_local`
  - `timezone`
- Optional Fields:
  - `description`
  - `location`
  - `calendar_id`

### 4.7 `calendar_update_event`
- Required Fields:
  - `event_id`
  - `timezone`
- Optional Fields:
  - `title`
  - `start_local`
  - `end_local`
  - `description`
  - `location`
  - `calendar_id`

### 4.8 OAuth Required Result
- calendar tool 공통 오류 포맷

```json
{
  "status": "oauth_required",
  "message": "Google account is not connected.",
  "connect_url": "https://assistant.example.com/auth/google/start?connect_token=..."
}
```

## 5. Internal Contracts

### 5.1 `InboundEvent`

```json
{
  "platform": "telegram",
  "external_user_id": "123456",
  "chat_id": "123456",
  "conversation_id": "123456",
  "message_id": "77",
  "update_id": "10001",
  "text": "내일 오전 9시에 약 먹으라고 알려줘",
  "username": "tester",
  "display_name": "Tester",
  "raw_payload": {}
}
```

### 5.2 `InternalUser`

```json
{
  "id": "uuid",
  "timezone": "Asia/Seoul",
  "platform": "telegram",
  "platform_user_id": "123456"
}
```

### 5.3 `OutboundMessage`

```json
{
  "platform": "telegram",
  "chat_id": "123456",
  "text": "내일 오전 9시 알림으로 등록했어요.",
  "reply_to_message_id": "77"
}
```

## 6. Data Model Summary

### 6.1 `users`
- `id`
- `timezone`
- timestamps

### 6.2 `channel_accounts`
- `user_id`
- `platform`
- `platform_user_id`
- `platform_chat_id`
- `username`
- `display_name`

### 6.3 `oauth_accounts`
- `user_id`
- `provider`
- `email`
- `access_token_encrypted`
- `refresh_token_encrypted`
- `expires_at`
- `scope`
- `token_type`

### 6.4 `oauth_connect_tokens`
- target-state 테이블
- `token`
- `user_id`
- `platform`
- `expires_at`
- `used_at`
- `signature_version`

### 6.5 `messages`
- `user_id`
- `platform`
- `conversation_id`
- `chat_id`
- `external_message_id`
- `external_update_id`
- `direction`
- `role`
- `content`
- `tool_name`
- `tool_payload`

### 6.6 `conversation_summaries`
- `user_id`
- `platform`
- `conversation_id`
- `summary_text`
- `message_count`

### 6.7 `reminders`
- `id`
- `user_id`
- `source_platform`
- `source_chat_id`
- `title`
- `timezone`
- `due_at`
- `recurrence_type`
- `recurrence_rule`
- `next_fire_at`
- `status`
- `notes`
- `created_from_message_id`
- `canceled_at`

### 6.8 `reminder_deliveries`
- `reminder_id`
- `platform`
- `target_chat_id`
- `status`
- `attempt_count`
- `last_error`
- `delivered_at`
- target-state 확장:
  - `next_attempt_at`
  - `max_attempts`

## 7. TTS Deferred Interface
- 구현 시점: 마지막 단계
- 목표 인터페이스:

```json
{
  "profile_name": "global-default",
  "provider": "abstract-provider-id",
  "text": "리마인더예요. 약 드실 시간이 되었어요.",
  "output_format": "mp3"
}
```

- 설계 원칙:
  - provider-agnostic
  - global single profile
  - audio sample hot-swap 가능

## 8. 비기능 요구사항
- Telegram webhook은 HTTPS 필수
- nginx에서 TLS 종료
- Certbot 자동 갱신
- reminder retry 정책: 3분 간격, 최대 3회
- webhook replay 방지
- Google connect token 재사용 방지
