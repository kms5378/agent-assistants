#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

REPO_DIR="${REPO_DIR:-$(repo_root_dir)}"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_DIR/docker-compose.yml}"
BASE_URL="${BASE_URL:-http://localhost}"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-60}"
KEEP_RUNNING=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --env-file)
            ENV_FILE="$2"
            shift 2
            ;;
        --compose-file)
            COMPOSE_FILE="$2"
            shift 2
            ;;
        --base-url)
            BASE_URL="$2"
            shift 2
            ;;
        --max-wait)
            MAX_WAIT_SECONDS="$2"
            shift 2
            ;;
        --keep-running)
            KEEP_RUNNING=1
            shift
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

require_commands docker curl
[[ -f "$COMPOSE_FILE" ]] || die "Compose file not found: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]] || die "Env file not found: $ENV_FILE"

cleanup() {
    if [[ "$KEEP_RUNNING" == "0" ]]; then
        log "Stopping local compose stack"
        docker compose -f "$COMPOSE_FILE" down --remove-orphans
    fi
}

trap cleanup EXIT

cd "$REPO_DIR"
log "Starting local compose stack with $COMPOSE_FILE"
docker compose -f "$COMPOSE_FILE" up --build -d

deadline=$((SECONDS + MAX_WAIT_SECONDS))
while true; do
    if curl -fsS "${BASE_URL%/}/healthz" >/dev/null 2>&1; then
        log "Local app responded on ${BASE_URL%/}/healthz"
        break
    fi
    if (( SECONDS >= deadline )); then
        docker compose -f "$COMPOSE_FILE" ps >&2 || true
        docker compose -f "$COMPOSE_FILE" logs --no-color --tail=200 >&2 || true
        die "Timed out waiting for local app health endpoint"
    fi
    sleep 2
done

log "Running smoke-test.sh against ${BASE_URL}"
"$SCRIPT_DIR/smoke-test.sh" --env-file "$ENV_FILE" --base-url "$BASE_URL"

log "Local preflight completed successfully"
