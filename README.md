# Personal AI Assistant MVP

FastAPI 기반의 대화형 Telegram assistant 입니다. 현재 구현 범위는 다음과 같습니다.

- Telegram webhook 수신 및 자연어 대화 응답
- NVIDIA NIM Chat Completions 기반 function calling 루프
- 자연어 기반 reminder 생성/조회/삭제/알림
- Google Calendar OAuth 연결 및 조회/생성/수정 REST 경계
- 단일 worker polling 기반 reminder 발송
- Discord 확장을 고려한 channel adapter 분리

## Documents

- `AGENTS.md`: 작업 시작 시 먼저 읽는 프로젝트 지침과 서비스/에이전트 역할
- `api-spec.md`: 외부/내부 API 명세
- `workflow.md`: 런타임 및 구현 순서도
- `hand-off.md`: 인수인계 및 후속 작업 기준

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

## NVIDIA NIM 설정

`.env`에 다음 값을 설정합니다.

```dotenv
NVIDIA_API_KEY=your-nvidia-api-key
NVIDIA_MODEL=nvidia/nemotron-3-ultra-550b-a55b
```

- API endpoint: `https://integrate.api.nvidia.com/v1`
- 모델: [NVIDIA Nemotron 3 Ultra 550B A55B](https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b)
- NVIDIA 무료 Endpoint는 개발·평가용이며 운영 Telegram 트래픽에 사용하지 않는다.

## Docker Compose

```bash
docker compose up --build
```

운영 시 `nginx/nginx.conf`의 도메인/인증서 경로를 실제 값으로 수정해야 합니다.
