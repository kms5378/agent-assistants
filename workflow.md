# Workflow

## 1. 사용자 대화 처리 순서도

```mermaid
flowchart LR
    A["Telegram User Message"] --> B["Nginx HTTPS Webhook"]
    B --> C["FastAPI /webhooks/telegram/{webhook_key}"]
    C --> D["TelegramAdapter.parse_update"]
    D --> E["ConversationService.handle_event"]
    E --> F["Load/Create InternalUser"]
    F --> G["Store inbound message + idempotency check"]
    G --> H["Build prompt: system + summary + recent turns"]
    H --> I["OpenAI Responses API"]
    I --> J{"Tool call exists?"}
    J -- "No" --> K["Store assistant message"]
    J -- "Yes" --> L["ToolRouter.execute"]
    L --> M["ReminderService / GoogleOAuthService"]
    M --> N["Store tool result"]
    N --> O["Submit function_call_output to model"]
    O --> J
    K --> P["Refresh conversation summary if needed"]
    P --> Q["TelegramAdapter.send_message"]
    Q --> R["User receives reply"]
```

## 2. Google OAuth 연결 순서도

```mermaid
sequenceDiagram
    participant U as User
    participant T as Telegram Bot
    participant A as App
    participant G as Google OAuth

    U->>T: "구글 캘린더 연결해줘"
    T->>A: conversation request
    A->>A: create signed one-time connect token
    A-->>T: connect URL 포함 응답
    U->>A: GET /auth/google/start?connect_token=...
    A->>A: connect token 검증
    A-->>G: 302 redirect
    U->>G: consent
    G-->>A: /auth/google/callback?state=...&code=...
    A->>G: token exchange
    A->>A: encrypt and save refresh token
    A-->>U: HTML 연결 완료 안내
```

## 3. Reminder 발송 및 재시도 순서도

```mermaid
flowchart TD
    A["Worker Tick"] --> B["Query reminders where status in scheduled/pending and next_fire_at <= now"]
    B --> C["Claim rows and set processing"]
    C --> D{"Send Telegram message success?"}
    D -- "Yes + one-time" --> E["Write reminder_deliveries(sent)"]
    E --> F["Set reminder status = sent"]
    D -- "Yes + recurring" --> G["Write reminder_deliveries(sent)"]
    G --> H["Compute next_fire_at"]
    H --> I["Set reminder status = scheduled"]
    D -- "No and attempts < 3" --> J["Write reminder_deliveries(failed-attempt)"]
    J --> K["Set status = pending"]
    K --> L["Set next_attempt_at = now + 3 minutes"]
    D -- "No and attempts >= 3" --> M["Write reminder_deliveries(failed-final)"]
    M --> N["Set reminder status = failed"]
```

## 4. 구현 작업 순서도

```mermaid
flowchart TD
    A["Phase 1: Security Foundation"] --> B["Signed one-time Google connect token"]
    B --> C["Postgres driver and migration baseline"]
    C --> D["Reminder retry fields and worker policy"]
    D --> E["Telegram MVP stabilization"]
    E --> F["Persona profile configuration layer"]
    F --> G["Global TTS profile abstraction"]
    G --> H["Provider-specific TTS implementation"]
```

## 5. 운영 배포 순서도

```mermaid
flowchart LR
    A["EC2 Launch"] --> B["Assign Elastic IP"]
    B --> C["Point Domain DNS to EC2"]
    C --> D["Run app + worker + postgres with Docker Compose"]
    D --> E["Configure nginx reverse proxy"]
    E --> F["Issue TLS cert with Certbot"]
    F --> G["Register Telegram setWebhook"]
    G --> H["Smoke test healthz, webhook, OAuth redirect"]
```

## 6. 핵심 운영 규칙
- Telegram은 webhook only
- HTTPS는 nginx + Certbot 조합
- reminder retry는 3분 간격 3회
- TTS는 마지막 단계에서 global profile 기준으로 추가
- Discord는 adapter만 추가하는 방식으로 확장
