# Personal AI Assistant MVP

FastAPI 기반의 대화형 Telegram assistant 입니다. 현재 구현 범위는 다음과 같습니다.

- Telegram webhook 수신 및 자연어 대화 응답
- OpenAI Responses API 기반 function calling 루프
- 자연어 기반 reminder 생성/조회/삭제/알림
- Google Calendar OAuth 연결 및 조회/생성/수정 REST 경계
- 단일 worker polling 기반 reminder 발송
- Discord 확장을 고려한 channel adapter 분리

## Documents

- `AGENTS.md`: 작업 시작 시 먼저 읽는 프로젝트 지침과 서비스/에이전트 역할
- `api-spec.md`: 외부/내부 API 명세
- `workflow.md`: 런타임 및 구현 순서도
- `hand-off.md`: 인수인계 및 후속 작업 기준
- `checklist.md`: phase별 실행 체크리스트
- `operations.md`: EC2 운영 배포, Certbot, Telegram webhook, 스모크 테스트 가이드
- `scripts/ops/`: 운영 자동화 스크립트 모음

## Local Run

1. `.env.example`를 `.env`로 복사하고 값을 채웁니다.
2. 개발 실행:

```bash
uvicorn app.api.main:app --reload
```

3. worker 실행:

```bash
python -m app.worker
```

## Docker Compose

```bash
docker compose up --build
```

기본 `docker compose`는 로컬 스모크 테스트를 위해 `nginx/docker-compose.conf`의 HTTP 프록시 설정을 사용합니다.
운영 HTTPS 배포 시에는 `nginx/nginx.conf`의 도메인/인증서 경로를 실제 값으로 맞춰 별도 적용해야 합니다.

운영 배포는 아래 명령을 기준으로 합니다.

```bash
docker compose -f docker-compose.prod.yml up -d postgres app worker
docker compose -f docker-compose.prod.yml up -d nginx
```

Certbot 발급/갱신, `setWebhook`, healthz/webhook/OAuth 스모크 테스트 절차는 `operations.md`를 따릅니다.
운영 자동화가 필요하면 `scripts/ops/install-certbot-timer.sh`, `scripts/ops/telegram-webhook.sh`, `scripts/ops/smoke-test.sh`를 사용합니다.
