# Operations Guide

## 1. 문서 목적
- 이 문서는 EC2 단일 인스턴스 기준 운영 배포 절차를 정리한다.
- 로컬 스모크 테스트는 `docker-compose.yml`과 `nginx/docker-compose.conf`를 사용한다.
- 운영 HTTPS 배포는 `docker-compose.prod.yml`과 `nginx/nginx.conf`를 사용한다.

## 2. 배포 전 준비물
- Ubuntu 계열 EC2 인스턴스
- Elastic IP 또는 고정 퍼블릭 IP
- 서비스 도메인과 DNS A 레코드
- 80, 443 inbound 허용 보안 그룹
- Docker Engine + Docker Compose Plugin
- Certbot
- Telegram bot token
- Google OAuth client와 redirect URI

## 3. 환경 변수 준비
1. `.env.example`을 기반으로 `.env`를 만든다.

```bash
cp .env.example .env
```

2. 최소 필수값을 운영 값으로 교체한다.
- `APP_BASE_URL=https://your-domain.example.com`
- `DATABASE_URL=postgresql+psycopg://assistant:assistant@postgres:5432/assistant`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_WEBHOOK_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REDIRECT_URI=https://your-domain.example.com/auth/google/callback`
- `APP_ENCRYPTION_KEY`

3. 비밀값은 예측 불가능한 랜덤 문자열로 설정한다.
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_WEBHOOK_KEY`
- `APP_ENCRYPTION_KEY`

## 4. nginx 운영 설정
- 운영 nginx 템플릿은 `nginx/nginx.conf`다.
- `server_name your-domain.example.com;`를 실제 도메인으로 바꾼다.
- 인증서 경로 `/etc/letsencrypt/live/your-domain.example.com/...`도 같은 도메인으로 맞춘다.
- 로컬용 `nginx/docker-compose.conf`는 HTTP 프록시만 담당하므로 운영에 그대로 쓰지 않는다.

## 5. 최초 배포 순서
1. 앱, 워커, DB를 먼저 올린다.

```bash
docker compose -f docker-compose.prod.yml up -d postgres app worker
```

2. Certbot으로 최초 인증서를 발급한다.

```bash
sudo certbot certonly --standalone \
  -d your-domain.example.com \
  --agree-tos \
  -m you@example.com \
  --non-interactive
```

3. 인증서 발급이 끝나면 nginx까지 포함해 운영 스택을 올린다.

```bash
docker compose -f docker-compose.prod.yml up -d
```

4. 컨테이너 상태를 확인한다.

```bash
docker compose -f docker-compose.prod.yml ps
```

## 6. Certbot 갱신 절차
- 현재 운영 가이드는 `standalone` 모드를 기준으로 한다.
- 갱신 시 80 포트를 Certbot이 잠깐 사용해야 하므로 nginx를 잠시 내렸다가 다시 올린다.

```bash
sudo certbot renew --standalone \
  --pre-hook 'cd /srv/personal_ai_assistant_ver2.0 && docker compose -f docker-compose.prod.yml stop nginx' \
  --post-hook 'cd /srv/personal_ai_assistant_ver2.0 && docker compose -f docker-compose.prod.yml up -d nginx'
```

- 실제 cron 또는 systemd timer에 등록하기 전에 아래 dry-run으로 검증한다.

```bash
sudo certbot renew --dry-run --standalone \
  --pre-hook 'cd /srv/personal_ai_assistant_ver2.0 && docker compose -f docker-compose.prod.yml stop nginx' \
  --post-hook 'cd /srv/personal_ai_assistant_ver2.0 && docker compose -f docker-compose.prod.yml up -d nginx'
```

## 7. Telegram `setWebhook` 등록
1. `.env`를 로드한다.

```bash
set -a
source .env
set +a
```

2. webhook을 등록한다.

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -d "url=${APP_BASE_URL}/webhooks/telegram/${TELEGRAM_WEBHOOK_KEY}" \
  -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}"
```

3. 등록 결과를 확인한다.

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

## 8. 운영 스모크 테스트
### 8.1 Health Check

```bash
curl -i https://your-domain.example.com/healthz
```

- 기대 결과: `HTTP/1.1 200 OK`
- 기대 body: `{"status":"ok"}`

### 8.2 Telegram Webhook 경로와 시크릿 검증

```bash
curl -i -X POST "https://your-domain.example.com/webhooks/telegram/wrong-key" \
  -H "Content-Type: application/json" \
  -d '{"update_id":999001,"callback_query":{"id":"1"}}'
```

- 기대 결과: `404`

```bash
curl -i -X POST "https://your-domain.example.com/webhooks/telegram/${TELEGRAM_WEBHOOK_KEY}" \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: wrong-secret" \
  -d '{"update_id":999002,"callback_query":{"id":"1"}}'
```

- 기대 결과: `403`

```bash
curl -i -X POST "https://your-domain.example.com/webhooks/telegram/${TELEGRAM_WEBHOOK_KEY}" \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: ${TELEGRAM_WEBHOOK_SECRET}" \
  -d '{"update_id":999003,"callback_query":{"id":"1"}}'
```

- 기대 결과: `200`
- 기대 body: `{"ok":true,"ignored":true}`

### 8.3 Google OAuth 시작점 검증

```bash
curl -i "https://your-domain.example.com/auth/google/start?connect_token=invalid"
```

- 기대 결과: `400`

### 8.4 Google OAuth 실제 연결 확인
1. Telegram에서 봇에게 Google Calendar 연결 요청 메시지를 보낸다.
2. 응답으로 받은 연결 링크를 연다.
3. Google 동의 화면으로 리다이렉트되는지 확인한다.
4. 동의 완료 후 `Google Calendar connected` HTML 페이지가 보이는지 확인한다.

## 9. 운영 파일 역할
- `docker-compose.yml`: 로컬 스모크 테스트
- `docker-compose.prod.yml`: EC2 운영 배포
- `nginx/docker-compose.conf`: 로컬 HTTP 프록시
- `nginx/nginx.conf`: 운영 HTTPS 프록시
