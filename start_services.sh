#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is not installed or not on PATH."
  exit 1
fi

# Set START_REST=0 to skip the optional REST API.
START_REST="${START_REST:-1}"
MCP_PORT="${MCP_PORT:-8002}"
MCP_URL="${MCP_URL:-http://127.0.0.1:${MCP_PORT}/mcp}"
REST_PORT="${REST_PORT:-8001}"
RUNTIME_PORT="${RUNTIME_PORT:-8080}"
STREAMLIT_PORT="${STREAMLIT_PORT:-8501}"

PIDS=()

cleanup() {
  echo
  echo "Stopping services..."

  if [[ ${#PIDS[@]} -gt 0 ]]; then
    kill "${PIDS[@]}" 2>/dev/null || true
    wait "${PIDS[@]}" 2>/dev/null || true
  fi

  echo "All started services were stopped."
}

trap cleanup EXIT INT TERM

start_service() {
  local name="$1"
  local cmd="$2"

  echo "Starting ${name}..."
  bash -lc "cd \"$ROOT_DIR\"; ${cmd}" &
  local pid=$!
  PIDS+=("$pid")
  echo "${name} PID: ${pid}"
}

require_free_port() {
  local name="$1"
  local port="$2"

  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Error: ${name} port ${port} is already in use."
    echo "Set a different port via environment variables, or stop the process using it."
    exit 1
  fi
}

wait_for_any_exit() {
  # Bash 4.3+ supports `wait -n`; macOS default Bash 3.2 does not.
  if wait -n 2>/dev/null; then
    return 0
  fi

  while true; do
    for pid in "${PIDS[@]}"; do
      if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null || true
        return 0
      fi
    done
    sleep 1
  done
}

echo "[1/5] Installing dependencies with uv sync..."
cd "$ROOT_DIR"
uv sync

echo "Using MCP_URL=${MCP_URL}"
require_free_port "MCP server" "$MCP_PORT"
if [[ "$START_REST" == "1" ]]; then
  require_free_port "REST API" "$REST_PORT"
fi
require_free_port "Agent runtime" "$RUNTIME_PORT"
require_free_port "Streamlit" "$STREAMLIT_PORT"

echo "[2/5] Starting MCP server..."
start_service "MCP server" "MCP_PORT=\"$MCP_PORT\" uv run python -m mcp_rest_lab.mcp_server"

if [[ "$START_REST" == "1" ]]; then
  echo "[3/5] Starting optional REST API..."
  start_service "REST API" "uv run uvicorn mcp_rest_lab.api:app --port \"$REST_PORT\""
else
  echo "[3/5] Skipping optional REST API (START_REST=$START_REST)."
fi

echo "[4/5] Starting runtime (includes aws sso login)..."
start_service "Agent runtime" "source .env; export MCP_URL=\"$MCP_URL\"; aws sso login --profile \"\$AWS_PROFILE\"; uv run python -m mcp_rest_lab.runtime"

echo "[5/5] Starting Streamlit..."
start_service "Streamlit" "uv run streamlit run app/streamlit_app.py --server.port \"$STREAMLIT_PORT\""

echo ""
echo "Services are running under this launcher."
echo "Press Ctrl+C to stop everything started by this script."
echo "Tip: START_REST=0 ./start_services.sh to skip the REST API process."

# Wait for any child to exit; cleanup trap handles shutdown for all.
wait_for_any_exit
