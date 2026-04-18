#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

REPO_DIR="${REPO_DIR:-$(repo_root_dir)}"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_DIR/docker-compose.prod.yml}"
NGINX_SERVICE="${NGINX_SERVICE:-nginx}"

require_commands certbot docker
[[ -f "$COMPOSE_FILE" ]] || die "Compose file not found: $COMPOSE_FILE"

nginx_stopped=0
cleanup() {
    if [[ "$nginx_stopped" == "1" ]]; then
        log "Starting ${NGINX_SERVICE} after certbot renew"
        docker compose -f "$COMPOSE_FILE" up -d "$NGINX_SERVICE"
    fi
}

trap cleanup EXIT

cd "$REPO_DIR"
log "Stopping ${NGINX_SERVICE} before certbot renew"
docker compose -f "$COMPOSE_FILE" stop "$NGINX_SERVICE"
nginx_stopped=1

log "Running certbot renew --standalone $*"
certbot renew --standalone "$@"
