#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
UI_DIR="$(cd "${REPO_ROOT}/.." && pwd)/ui"
RUNTIME_DIR="${REPO_ROOT}/.run"
UV_CACHE_DIR="${RUNTIME_DIR}/uv-cache"
BACKEND_PID_FILE="${RUNTIME_DIR}/backend.pid"
FRONTEND_PID_FILE="${RUNTIME_DIR}/frontend.pid"
BACKEND_LOG="${RUNTIME_DIR}/backend.log"
FRONTEND_LOG="${RUNTIME_DIR}/frontend.log"
CLICKHOUSE_CONTAINER="${CONTEXT_COMPILER_CLICKHOUSE_CONTAINER:-context-compiler-clickhouse}"
BACKEND_URL="http://127.0.0.1:8000"
FRONTEND_URL="http://127.0.0.1:5173"

CLICKHOUSE_ENV=(
  "CONTEXT_COMPILER_CLICKHOUSE_HOST=127.0.0.1"
  "CONTEXT_COMPILER_CLICKHOUSE_PORT=8123"
  "CONTEXT_COMPILER_CLICKHOUSE_SECURE=false"
  "CONTEXT_COMPILER_CLICKHOUSE_USERNAME="
  "CONTEXT_COMPILER_CLICKHOUSE_PASSWORD="
  "CONTEXT_COMPILER_CLICKHOUSE_DATABASE=default"
  "CONTEXT_COMPILER_CLICKHOUSE_METADATA_DATABASE=compiler_meta"
  "UV_CACHE_DIR=${UV_CACHE_DIR}"
)

info() {
  printf '[context-compiler] %s\n' "$*"
}

fail() {
  printf '[context-compiler] ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command '$1' was not found."
}

run_backend() {
  env "${CLICKHOUSE_ENV[@]}" "$@"
}

prepare_environment() {
  mkdir -p "${RUNTIME_DIR}"
  if [[ ! -f "${REPO_ROOT}/.env" ]]; then
    cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
    chmod 600 "${REPO_ROOT}/.env"
    info "Created .env from .env.example."
  else
    info "Preserving existing .env."
  fi
}

install_dependencies() {
  info "Installing backend dependencies..."
  (
    cd "${REPO_ROOT}"
    UV_CACHE_DIR="${UV_CACHE_DIR}" uv sync --frozen
  )
  info "Installing frontend dependencies..."
  (
    cd "${UI_DIR}"
    npm install
  )
}

ensure_clickhouse() {
  if ! docker info >/dev/null 2>&1; then
    fail "Docker is not running. Start Docker Desktop and run this command again."
  fi

  if docker inspect "${CLICKHOUSE_CONTAINER}" >/dev/null 2>&1; then
    if [[ "$(docker inspect -f '{{.State.Running}}' "${CLICKHOUSE_CONTAINER}")" != "true" ]]; then
      info "Starting existing ClickHouse container..."
      docker start "${CLICKHOUSE_CONTAINER}" >/dev/null
    else
      info "ClickHouse container is already running."
    fi
  else
    info "Creating local ClickHouse container..."
    docker run \
      --name "${CLICKHOUSE_CONTAINER}" \
      --detach \
      --publish 8123:8123 \
      --publish 9000:9000 \
      --env CLICKHOUSE_SKIP_USER_SETUP=1 \
      clickhouse/clickhouse-server:latest >/dev/null
  fi

  wait_for_url "http://127.0.0.1:8123/ping" 60 || {
    docker logs --tail 80 "${CLICKHOUSE_CONTAINER}" >&2 || true
    fail "ClickHouse did not become ready within 60 seconds."
  }
  info "ClickHouse is ready."
}

initialize_backend() {
  info "Applying ClickHouse migrations..."
  (
    cd "${REPO_ROOT}"
    run_backend "${REPO_ROOT}/.venv/bin/python" -m app.clickhouse.migrations
  )
  info "Bootstrapping approved context..."
  (
    cd "${REPO_ROOT}"
    run_backend "${REPO_ROOT}/.venv/bin/python" -m app.cli \
      bootstrap-context --source docs/base_context.md
  )
  info "Precomputing metrics from the eight existing Atlys tables..."
  if ! (
    cd "${REPO_ROOT}"
    run_backend "${REPO_ROOT}/.venv/bin/python" -m app.cli precompute-baseline
  ); then
    info "WARNING: baseline metrics were not computed; load the Atlys source tables first."
  fi
}

check_llm_configuration() {
  if (
    cd "${REPO_ROOT}"
    run_backend "${REPO_ROOT}/.venv/bin/python" -c \
      'from app.core.config import Settings; raise SystemExit(0 if Settings().llm_configured else 1)'
  ); then
    info "LLM provider configuration found."
  else
    info "WARNING: LLM_BASE_URL, LLM_API_KEY, and LLM_MODEL are not all configured."
    info "The application will launch, but pipeline generation will not work until .env is updated."
  fi
}

pid_is_running() {
  local pid_file="$1"
  [[ -f "${pid_file}" ]] || return 1
  local pid
  pid="$(<"${pid_file}")"
  [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null
}

start_backend() {
  if pid_is_running "${BACKEND_PID_FILE}"; then
    info "Backend is already running (PID $(<"${BACKEND_PID_FILE}"))."
    return
  fi
  rm -f "${BACKEND_PID_FILE}"
  info "Starting backend..."
  (
    cd "${REPO_ROOT}"
    nohup env "${CLICKHOUSE_ENV[@]}" \
      "${REPO_ROOT}/.venv/bin/uvicorn" app.main:app \
      --host 127.0.0.1 --port 8000 >"${BACKEND_LOG}" 2>&1 &
    printf '%s\n' "$!" >"${BACKEND_PID_FILE}"
  )
}

start_frontend() {
  if pid_is_running "${FRONTEND_PID_FILE}"; then
    info "Frontend is already running (PID $(<"${FRONTEND_PID_FILE}"))."
    return
  fi
  rm -f "${FRONTEND_PID_FILE}"
  info "Starting frontend..."
  (
    cd "${UI_DIR}"
    nohup "${UI_DIR}/node_modules/.bin/vite" --host 127.0.0.1 \
      >"${FRONTEND_LOG}" 2>&1 &
    printf '%s\n' "$!" >"${FRONTEND_PID_FILE}"
  )
}

wait_for_url() {
  local url="$1"
  local timeout_seconds="${2:-30}"
  local started_at="${SECONDS}"
  while (( SECONDS - started_at < timeout_seconds )); do
    if curl --fail --silent --show-error "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

verify_application() {
  wait_for_url "${BACKEND_URL}/health" 30 || {
    tail -n 80 "${BACKEND_LOG}" >&2 || true
    fail "Backend did not become ready."
  }
  wait_for_url "${FRONTEND_URL}" 30 || {
    tail -n 80 "${FRONTEND_LOG}" >&2 || true
    fail "Frontend did not become ready."
  }

  info "Application is running:"
  printf '  Frontend:  %s\n' "${FRONTEND_URL}"
  printf '  Backend:   %s\n' "${BACKEND_URL}"
  printf '  API docs:  %s/docs\n' "${BACKEND_URL}"
  printf '  Logs:      %s\n' "${RUNTIME_DIR}"
}

stop_process() {
  local name="$1"
  local pid_file="$2"
  if ! pid_is_running "${pid_file}"; then
    rm -f "${pid_file}"
    info "${name} is not running."
    return
  fi

  local pid
  pid="$(<"${pid_file}")"
  info "Stopping ${name} (PID ${pid})..."
  kill "${pid}" 2>/dev/null || true
  for _ in {1..20}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      rm -f "${pid_file}"
      return
    fi
    sleep 0.25
  done
  kill -9 "${pid}" 2>/dev/null || true
  rm -f "${pid_file}"
}

stop_application() {
  stop_process "frontend" "${FRONTEND_PID_FILE}"
  stop_process "backend" "${BACKEND_PID_FILE}"
  if docker inspect "${CLICKHOUSE_CONTAINER}" >/dev/null 2>&1; then
    info "Stopping ClickHouse container..."
    docker stop "${CLICKHOUSE_CONTAINER}" >/dev/null
  fi
}

show_status() {
  if pid_is_running "${BACKEND_PID_FILE}"; then
    printf 'Backend:   running (PID %s)\n' "$(<"${BACKEND_PID_FILE}")"
  else
    printf 'Backend:   stopped\n'
  fi
  if pid_is_running "${FRONTEND_PID_FILE}"; then
    printf 'Frontend:  running (PID %s)\n' "$(<"${FRONTEND_PID_FILE}")"
  else
    printf 'Frontend:  stopped\n'
  fi
  if docker inspect "${CLICKHOUSE_CONTAINER}" >/dev/null 2>&1; then
    printf 'ClickHouse: %s\n' "$(docker inspect -f '{{.State.Status}}' "${CLICKHOUSE_CONTAINER}")"
  else
    printf 'ClickHouse: not created\n'
  fi
  curl --silent --show-error "${BACKEND_URL}/health" 2>/dev/null || true
  printf '\n'
}

show_logs() {
  mkdir -p "${RUNTIME_DIR}"
  touch "${BACKEND_LOG}" "${FRONTEND_LOG}"
  tail -n 80 -f "${BACKEND_LOG}" "${FRONTEND_LOG}"
}

usage() {
  cat <<'EOF'
Usage: ./scripts/setup-and-run.sh [command]

Commands:
  up        Install, initialize, and launch ClickHouse, backend, and frontend (default)
  start     Start previously initialized services without reinstalling or migrating
  setup     Install dependencies, start ClickHouse, migrate, and bootstrap context
  restart   Stop and launch all services again
  status    Show local service status
  logs      Follow backend and frontend logs
  down      Stop frontend, backend, and the local ClickHouse container
  help      Show this help
EOF
}

main() {
  local command="${1:-up}"
  require_command curl
  require_command docker
  mkdir -p "${RUNTIME_DIR}"

  case "${command}" in
    up)
      require_command uv
      require_command npm
      prepare_environment
      install_dependencies
      ensure_clickhouse
      initialize_backend
      check_llm_configuration
      start_backend
      start_frontend
      verify_application
      ;;
    setup)
      require_command uv
      require_command npm
      prepare_environment
      install_dependencies
      ensure_clickhouse
      initialize_backend
      check_llm_configuration
      ;;
    start)
      [[ -x "${REPO_ROOT}/.venv/bin/uvicorn" ]] || fail "Run setup first."
      [[ -x "${UI_DIR}/node_modules/.bin/vite" ]] || fail "Run setup first."
      ensure_clickhouse
      start_backend
      start_frontend
      verify_application
      ;;
    restart)
      stop_application
      ensure_clickhouse
      start_backend
      start_frontend
      verify_application
      ;;
    status)
      show_status
      ;;
    logs)
      show_logs
      ;;
    down)
      stop_application
      ;;
    help|-h|--help)
      usage
      ;;
    *)
      usage >&2
      fail "Unknown command: ${command}"
      ;;
  esac
}

main "$@"
