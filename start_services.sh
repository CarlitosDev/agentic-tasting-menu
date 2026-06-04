#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: uv is not installed or not on PATH."
  exit 1
fi

# Set START_REST=0 to skip the optional REST learning track.
START_REST="${START_REST:-1}"

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

echo "[2/5] Starting MCP server..."
start_service "MCP server" "uv run python -m mcp_rest_lab.mcp_server"

if [[ "$START_REST" == "1" ]]; then
  echo "[3/5] Starting optional REST API..."
  start_service "REST API" "uv run uvicorn mcp_rest_lab.api:app --port 8001"
else
  echo "[3/5] Skipping optional REST API (START_REST=$START_REST)."
fi

echo "[4/5] Starting runtime (includes aws sso login)..."
start_service "Agent runtime" "source .env; aws sso login --profile \"\$AWS_PROFILE\"; uv run python -m mcp_rest_lab.runtime"

echo "[5/5] Starting Streamlit..."
start_service "Streamlit" "uv run streamlit run app/streamlit_app.py"

echo ""
echo "Services are running under this launcher."
echo "Press Ctrl+C to stop everything started by this script."
echo "Tip: START_REST=0 ./start_services.sh to skip the REST API process."

# Wait for any child to exit; cleanup trap handles shutdown for all.
wait_for_any_exit
