#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

ALLOW_INSECURE=0
BASE_URL_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --base-url)
            BASE_URL_OVERRIDE="$2"
            shift 2
            ;;
        --insecure)
            ALLOW_INSECURE=1
            shift
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

load_env_file
require_commands curl
require_envs APP_BASE_URL TELEGRAM_WEBHOOK_KEY TELEGRAM_WEBHOOK_SECRET

BASE_URL="$(strip_trailing_slash "${BASE_URL_OVERRIDE:-$APP_BASE_URL}")"
CURL_ARGS=(-sS)
if [[ "$ALLOW_INSECURE" == "1" || "${CURL_INSECURE:-0}" == "1" ]]; then
    CURL_ARGS+=(-k)
fi

LAST_STATUS=""
LAST_BODY=""

http_request() {
    local method="$1"
    local url="$2"
    shift 2

    local body_file
    body_file="$(mktemp)"
    LAST_STATUS="$(curl "${CURL_ARGS[@]}" -o "$body_file" -w '%{http_code}' -X "$method" "$url" "$@")"
    LAST_BODY="$(cat "$body_file")"
    rm -f "$body_file"
}

assert_status() {
    local name="$1"
    local expected_status="$2"
    [[ "$LAST_STATUS" == "$expected_status" ]] || die "${name} expected HTTP ${expected_status}, got ${LAST_STATUS}. body=${LAST_BODY}"
    log "${name} returned HTTP ${LAST_STATUS}"
}

assert_body_contains() {
    local name="$1"
    local expected_fragment="$2"
    printf '%s' "$LAST_BODY" | grep -Fq "$expected_fragment" || die "${name} response did not contain expected fragment: ${expected_fragment}"
    log "${name} response contained expected fragment: ${expected_fragment}"
}

http_request GET "${BASE_URL}/healthz"
assert_status "healthz" "200"
assert_body_contains "healthz" '"status":"ok"'

http_request POST "${BASE_URL}/webhooks/telegram/wrong-key" \
    -H "Content-Type: application/json" \
    -d '{"update_id":999001,"callback_query":{"id":"1"}}'
assert_status "webhook wrong key" "404"

http_request POST "${BASE_URL}/webhooks/telegram/${TELEGRAM_WEBHOOK_KEY}" \
    -H "Content-Type: application/json" \
    -H "X-Telegram-Bot-Api-Secret-Token: wrong-secret" \
    -d '{"update_id":999002,"callback_query":{"id":"1"}}'
assert_status "webhook wrong secret" "403"

http_request POST "${BASE_URL}/webhooks/telegram/${TELEGRAM_WEBHOOK_KEY}" \
    -H "Content-Type: application/json" \
    -H "X-Telegram-Bot-Api-Secret-Token: ${TELEGRAM_WEBHOOK_SECRET}" \
    -d '{"update_id":999003,"callback_query":{"id":"1"}}'
assert_status "webhook valid ignored update" "200"
assert_body_contains "webhook valid ignored update" '"ignored":true'

http_request GET "${BASE_URL}/auth/google/start?connect_token=invalid"
assert_status "oauth invalid connect token" "400"

log "All smoke tests passed for ${BASE_URL}"
