#!/usr/bin/env bash
# Start the containerized demo; Docker Compose v2 with --wait support is required.
set -Eeuo pipefail

task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_skip_bootstrap=false
task_no_build=false
task_timeout=180
task_frontend_url='http://127.0.0.1:8080'
task_backend_url='http://127.0.0.1:8000'
task_frontend_explicit=false
task_backend_explicit=false

usage() {
    cat <<'HELP'
Usage: bash start.sh [--skip-bootstrap] [--no-build] [--timeout SECONDS]
                     [--frontend-url URL] [--backend-url URL]
Bootstrap may call the Embedding API. Startup does not run evaluations or Judge.
Docker must be installed and running; configure .env before starting.
HELP
}
fail() { printf '%s\n' "ERROR: $*" >&2; exit 1; }
while (( $# )); do
    case "$1" in
        --skip-bootstrap) task_skip_bootstrap=true; shift ;;
        --no-build) task_no_build=true; shift ;;
        --timeout|--frontend-url|--backend-url)
            (( $# >= 2 )) || fail "Missing value for $1"
            case "$1" in
                --timeout) task_timeout="$2" ;;
                --frontend-url) task_frontend_url="$2"; task_frontend_explicit=true ;;
                --backend-url) task_backend_url="$2"; task_backend_explicit=true ;;
            esac
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done
[[ "$task_timeout" =~ ^[1-9][0-9]{0,3}$ ]] || fail 'Timeout must be an integer between 1 and 3600.'
(( 10#$task_timeout <= 3600 )) || fail 'Timeout must be at most 3600 seconds.'
command -v docker >/dev/null 2>&1 || fail 'Install and start Docker Engine/Desktop.'
command -v curl >/dev/null 2>&1 || fail 'Install curl for readiness checks.'
cd -- "$task_root"
task_env_file="${APEX_ENV_FILE:-.env}"
export COMPOSE_ENV_FILES="$task_env_file"
for task_file in docker-compose.yml "$task_env_file"; do
    [[ -f "$task_file" ]] || fail "Missing $task_file. Create .env from .env.example and configure credentials; see docs/deployment.md."
done
trap 'printf "%s\n" "Startup failed at line $LINENO. Inspect docker compose --profile app logs --tail 100. Existing volumes were preserved." >&2' ERR
docker info --format '{{.ServerVersion}}'
docker compose version
docker compose --profile app --profile tools config --quiet
# Parse only numeric port fields as data, never source/evaluate an env file.
while IFS='=' read -r task_name task_value; do
    task_value="${task_value%$'\r'}"
    [[ "$task_value" =~ ^[0-9]+$ ]] || continue
    if [[ "$task_name" == BACKEND_PORT && "$task_backend_explicit" == false ]]; then
        task_backend_url="http://127.0.0.1:${BACKEND_PORT:-$task_value}"
    elif [[ "$task_name" == FRONTEND_PORT && "$task_frontend_explicit" == false ]]; then
        task_frontend_url="http://127.0.0.1:${FRONTEND_PORT:-$task_value}"
    fi
done < "$task_env_file"
if [[ "$task_backend_explicit" == false && -n "${BACKEND_PORT:-}" ]]; then task_backend_url="http://127.0.0.1:$BACKEND_PORT"; fi
if [[ "$task_frontend_explicit" == false && -n "${FRONTEND_PORT:-}" ]]; then task_frontend_url="http://127.0.0.1:$FRONTEND_PORT"; fi
printf '%s\n' '[1/4] Starting PostgreSQL...'
docker compose up -d --wait --wait-timeout "$task_timeout" postgres
if [[ "$task_skip_bootstrap" == false ]]; then
    printf '%s\n' '[2/4] Preparing demo data and indexes (Embedding API may incur costs)...'
    task_build_args=()
    if [[ "$task_no_build" == false ]]; then task_build_args+=(--build); fi
    docker compose --profile tools run "${task_build_args[@]}" --rm bootstrap
else
    printf '%s\n' '[2/4] Skipping bootstrap; an active index must already exist.'
fi
printf '%s\n' '[3/4] Starting backend/frontend...'
task_build_args=(--build)
if [[ "$task_no_build" == true ]]; then task_build_args=(--no-build); fi
docker compose --profile app up -d "${task_build_args[@]}" --wait --wait-timeout "$task_timeout"
printf '%s\n' '[4/4] Checking application readiness...'
task_deadline=$(( SECONDS + task_timeout ))
task_ready=false
while (( SECONDS < task_deadline )); do
    if curl --fail --silent --output /dev/null --max-time 5 "${task_backend_url%/}/health/ready" \
        && curl --fail --silent --output /dev/null --max-time 5 "$task_frontend_url"; then
        task_ready=true
        break
    fi
    sleep 2
done
[[ "$task_ready" == true ]] || fail 'Readiness timed out. Inspect backend/frontend logs.'
printf '%s\n' "Ready: $task_frontend_url" "Swagger: ${task_backend_url%/}/docs"
printf '%s\n' 'Stop: docker compose --profile app stop'
printf '%s\n' 'Run evaluations separately; startup does not run Judge or the benchmark suite.'
