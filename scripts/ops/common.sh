#!/usr/bin/env bash
set -euo pipefail

ops_script_dir() {
    cd "$(dirname "${BASH_SOURCE[0]}")" && pwd
}

repo_root_dir() {
    cd "$(ops_script_dir)/../.." && pwd
}

log() {
    printf '[ops] %s\n' "$*" >&2
}

die() {
    printf '[ops] ERROR: %s\n' "$*" >&2
    exit 1
}

require_commands() {
    local command_name
    for command_name in "$@"; do
        command -v "$command_name" >/dev/null 2>&1 || die "Required command not found: $command_name"
    done
}

load_env_file() {
    local env_file="${ENV_FILE:-$(repo_root_dir)/.env}"
    [[ -f "$env_file" ]] || die "Env file not found: $env_file"
    local line
    local key
    local value

    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%$'\r'}"
        [[ -z "${line//[[:space:]]/}" ]] && continue
        [[ "$line" =~ ^[[:space:]]*# ]] && continue

        if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"

            if [[ "${value:0:1}" == "\"" && "${value: -1}" == "\"" ]]; then
                value="${value:1:${#value}-2}"
            elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
                value="${value:1:${#value}-2}"
            fi

            export "$key=$value"
            continue
        fi

        die "Unsupported env file line: $line"
    done < "$env_file"
}

require_envs() {
    local env_name
    for env_name in "$@"; do
        [[ -n "${!env_name:-}" ]] || die "Required environment variable is missing: $env_name"
    done
}

strip_trailing_slash() {
    local value="$1"
    printf '%s\n' "${value%/}"
}

render_template() {
    local template_path="$1"
    local output_path="$2"
    local repo_dir="$3"

    sed "s|__REPO_DIR__|$repo_dir|g" "$template_path" > "$output_path"
}
