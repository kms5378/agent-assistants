from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
OPS_SCRIPTS = [
    REPO_ROOT / "scripts" / "ops" / "common.sh",
    REPO_ROOT / "scripts" / "ops" / "certbot-renew.sh",
    REPO_ROOT / "scripts" / "ops" / "telegram-webhook.sh",
    REPO_ROOT / "scripts" / "ops" / "smoke-test.sh",
    REPO_ROOT / "scripts" / "ops" / "install-certbot-timer.sh",
]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_ops_scripts_have_valid_bash_syntax():
    for script in OPS_SCRIPTS:
        result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script} syntax error: {result.stderr}"


def test_telegram_webhook_script_registers_and_checks_webhook(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APP_BASE_URL=https://assistant.example.com",
                "TELEGRAM_BOT_TOKEN=test-token",
                "TELEGRAM_WEBHOOK_KEY=hook-key",
                "TELEGRAM_WEBHOOK_SECRET=secret-token",
            ]
        ),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl_log = tmp_path / "curl.log"
    _write_executable(
        bin_dir / "curl",
        """#!/bin/sh
printf '%s\\n' "$*" >> "${CURL_LOG}"
case "$*" in
  *setWebhook*)
    printf '%s' '{"ok":true,"result":true,"description":"Webhook was set"}'
    ;;
  *getWebhookInfo*)
    printf '%s' '{"ok":true,"result":{"url":"https://assistant.example.com/webhooks/telegram/hook-key"}}'
    ;;
  *)
    printf '%s' '{"ok":false}'
    exit 1
    ;;
esac
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CURL_LOG"] = str(curl_log)

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "ops" / "telegram-webhook.sh"), "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert "Webhook URL matches expected URL" in result.stderr
    log_output = curl_log.read_text(encoding="utf-8")
    assert "setWebhook" in log_output
    assert "getWebhookInfo" in log_output


def test_certbot_renew_script_restarts_nginx_even_when_certbot_fails(tmp_path: Path):
    compose_file = tmp_path / "docker-compose.prod.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ops_log = tmp_path / "ops.log"
    _write_executable(
        bin_dir / "docker",
        """#!/bin/sh
printf 'docker %s\\n' "$*" >> "${OPS_LOG_FILE}"
""",
    )
    _write_executable(
        bin_dir / "certbot",
        """#!/bin/sh
printf 'certbot %s\\n' "$*" >> "${OPS_LOG_FILE}"
exit "${CERTBOT_EXIT_CODE:-0}"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["OPS_LOG_FILE"] = str(ops_log)
    env["CERTBOT_EXIT_CODE"] = "1"
    env["REPO_DIR"] = str(tmp_path)
    env["COMPOSE_FILE"] = str(compose_file)

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "ops" / "certbot-renew.sh"), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 1
    log_output = ops_log.read_text(encoding="utf-8")
    assert f"docker compose -f {compose_file} stop nginx" in log_output
    assert "certbot renew --standalone --dry-run" in log_output
    assert f"docker compose -f {compose_file} up -d nginx" in log_output


def test_install_certbot_timer_renders_service_and_timer(tmp_path: Path):
    systemd_dir = tmp_path / "systemd"

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts" / "ops" / "install-certbot-timer.sh"),
            "--repo-dir",
            str(REPO_ROOT),
            "--systemd-dir",
            str(systemd_dir),
            "--skip-systemctl",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    service_file = systemd_dir / "personal-ai-assistant-certbot-renew.service"
    timer_file = systemd_dir / "personal-ai-assistant-certbot-renew.timer"
    assert service_file.exists()
    assert timer_file.exists()
    assert str(REPO_ROOT) in service_file.read_text(encoding="utf-8")
    assert "ExecStart=" in service_file.read_text(encoding="utf-8")


def test_smoke_test_script_checks_expected_routes(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APP_BASE_URL=https://assistant.example.com",
                "TELEGRAM_WEBHOOK_KEY=hook-key",
                "TELEGRAM_WEBHOOK_SECRET=secret-token",
            ]
        ),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(
        bin_dir / "curl",
        """#!/bin/sh
out_file=""
write_format=""
url=""
secret_header=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    -o)
      out_file="$2"
      shift 2
      ;;
    -w)
      write_format="$2"
      shift 2
      ;;
    -X)
      shift 2
      ;;
    -H)
      case "$2" in
        X-Telegram-Bot-Api-Secret-Token:*)
          secret_header="$2"
          ;;
      esac
      shift 2
      ;;
    -d)
      shift 2
      ;;
    -s|-S|-sS|-k)
      shift
      ;;
    --data|--data-raw|--data-urlencode)
      shift 2
      ;;
    http*)
      url="$1"
      shift
      ;;
    *)
      shift
      ;;
  esac
done

status="200"
body='{}'

case "$url" in
  *"/healthz")
    status="200"
    body='{"status":"ok"}'
    ;;
  *"/webhooks/telegram/wrong-key")
    status="404"
    body='{"detail":"Webhook not found."}'
    ;;
  *"/webhooks/telegram/hook-key")
    if [ "$secret_header" = "X-Telegram-Bot-Api-Secret-Token: wrong-secret" ]; then
      status="403"
      body='{"detail":"Invalid webhook token."}'
    else
      status="200"
      body='{"ok":true,"ignored":true}'
    fi
    ;;
  *"/auth/google/start?connect_token=invalid")
    status="400"
    body='{"detail":"Connect token is invalid, expired, or already used."}'
    ;;
esac

printf '%s' "$body" > "$out_file"
printf '%s' "$status"
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "ops" / "smoke-test.sh"), "--env-file", str(env_file)],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert "All smoke tests passed" in result.stderr
