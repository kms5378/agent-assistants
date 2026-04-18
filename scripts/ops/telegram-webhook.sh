#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

MODE="register"
DROP_PENDING_UPDATES=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --check)
            MODE="check"
            shift
            ;;
        --info)
            MODE="info"
            shift
            ;;
        --drop-pending-updates)
            DROP_PENDING_UPDATES=1
            shift
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

load_env_file
require_commands curl
require_envs APP_BASE_URL TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_KEY TELEGRAM_WEBHOOK_SECRET

BASE_URL="$(strip_trailing_slash "$APP_BASE_URL")"
EXPECTED_URL="${BASE_URL}/webhooks/telegram/${TELEGRAM_WEBHOOK_KEY}"
API_BASE="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}"

fetch_webhook_info() {
    curl -sS "${API_BASE}/getWebhookInfo"
}

check_webhook() {
    local response
    response="$(fetch_webhook_info)"
    printf '%s\n' "$response"

    printf '%s' "$response" | grep -q '"ok":true' || die "Telegram getWebhookInfo failed."
    printf '%s' "$response" | grep -Fq "\"url\":\"${EXPECTED_URL}\"" || die "Webhook URL does not match expected URL: ${EXPECTED_URL}"
    log "Webhook URL matches expected URL: ${EXPECTED_URL}"
}

register_webhook() {
    local response
    response="$(
        curl -sS -X POST "${API_BASE}/setWebhook" \
            -d "url=${EXPECTED_URL}" \
            -d "secret_token=${TELEGRAM_WEBHOOK_SECRET}" \
            -d "drop_pending_updates=${DROP_PENDING_UPDATES}"
    )"
    printf '%s\n' "$response"
    printf '%s' "$response" | grep -q '"ok":true' || die "Telegram setWebhook failed."
    check_webhook
}

case "$MODE" in
    register)
        register_webhook
        ;;
    check)
        check_webhook
        ;;
    info)
        fetch_webhook_info
        ;;
esac
