#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

REPO_DIR="${REPO_DIR:-$(repo_root_dir)}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
RUN_SYSTEMCTL=1

SERVICE_TEMPLATE="${REPO_DIR}/deploy/systemd/personal-ai-assistant-certbot-renew.service.template"
TIMER_TEMPLATE="${REPO_DIR}/deploy/systemd/personal-ai-assistant-certbot-renew.timer"
SERVICE_TARGET="${SYSTEMD_DIR}/personal-ai-assistant-certbot-renew.service"
TIMER_TARGET="${SYSTEMD_DIR}/personal-ai-assistant-certbot-renew.timer"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo-dir)
            REPO_DIR="$2"
            SERVICE_TEMPLATE="${REPO_DIR}/deploy/systemd/personal-ai-assistant-certbot-renew.service.template"
            TIMER_TEMPLATE="${REPO_DIR}/deploy/systemd/personal-ai-assistant-certbot-renew.timer"
            shift 2
            ;;
        --systemd-dir)
            SYSTEMD_DIR="$2"
            SERVICE_TARGET="${SYSTEMD_DIR}/personal-ai-assistant-certbot-renew.service"
            TIMER_TARGET="${SYSTEMD_DIR}/personal-ai-assistant-certbot-renew.timer"
            shift 2
            ;;
        --skip-systemctl)
            RUN_SYSTEMCTL=0
            shift
            ;;
        *)
            die "Unknown argument: $1"
            ;;
    esac
done

require_commands install mkdir sed
[[ -f "$SERVICE_TEMPLATE" ]] || die "Service template not found: $SERVICE_TEMPLATE"
[[ -f "$TIMER_TEMPLATE" ]] || die "Timer template not found: $TIMER_TEMPLATE"

mkdir -p "$SYSTEMD_DIR"
render_template "$SERVICE_TEMPLATE" "$SERVICE_TARGET" "$REPO_DIR"
install -m 0644 "$TIMER_TEMPLATE" "$TIMER_TARGET"

log "Installed ${SERVICE_TARGET}"
log "Installed ${TIMER_TARGET}"

if [[ "$RUN_SYSTEMCTL" == "1" ]]; then
    require_commands systemctl
    systemctl daemon-reload
    systemctl enable --now personal-ai-assistant-certbot-renew.timer
    log "Enabled personal-ai-assistant-certbot-renew.timer"
fi
