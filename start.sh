#!/usr/bin/env bash
#
# Start both Doudizhu-Arena backend and frontend simultaneously.
# Requires: conda env 'doudizhu-arena' with node and python.
#
# Usage:
#   bash start.sh              # Start both on default ports (8000 + 5173)
#   bash start.sh --reload     # Backend with hot-reload (uvicorn default)
#

set -e

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/src/backend"
FRONTEND_DIR="$PROJECT_DIR/src/frontend"
CONDA_ENV="doudizhu-arena"
ARENA_ENV_PREFIX="${DOUDIZHU_ENV_PREFIX:-$(conda run -n "$CONDA_ENV" python -I -c 'import sys; print(sys.prefix)')}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# Parse args
RELOAD_FLAG=""
if [[ "${1:-}" == "--reload" ]]; then
    RELOAD_FLAG="--reload"
    echo -e "${YELLOW}Backend reload mode enabled${NC}"
else
    echo -e "${YELLOW}Use --reload for backend hot-reload${NC}"
fi

# Create data directory if needed
mkdir -p "$PROJECT_DIR/data"

# Validate configuration before either service is started. This avoids a
# misleading state where Vite is available but every API request fails.
echo -e "${GREEN}=== Validating configuration ===${NC}"
cd "$BACKEND_DIR"
"$ARENA_ENV_PREFIX/bin/python" -I -c 'from pathlib import Path; import yaml; yaml.safe_load(Path("arena/config/agents.yaml").read_text(encoding="utf-8"))' || {
    echo -e "${RED}Invalid agents.yaml; services were not started.${NC}"
    exit 1
}

# Clean up function
BACKEND_PID=""
FRONTEND_PID=""
cleanup() {
    trap - EXIT INT TERM
    echo -e "\n${YELLOW}Shutting down...${NC}"
    for pid in "$BACKEND_PID" "$FRONTEND_PID"; do
        if [[ -n "$pid" ]]; then kill "$pid" 2>/dev/null || true; fi
    done
    for pid in "$BACKEND_PID" "$FRONTEND_PID"; do
        if [[ -n "$pid" ]]; then wait "$pid" 2>/dev/null || true; fi
    done
    echo -e "${GREEN}All processes stopped.${NC}"
}
trap cleanup EXIT INT TERM

# ── Backend ──────────────────────────────────────────────────────────────
echo -e "${GREEN}=== Starting Backend (port 8000) ===${NC}"

export PATH="$ARENA_ENV_PREFIX/bin:$PATH"

cd "$BACKEND_DIR"
"$ARENA_ENV_PREFIX/bin/python" -I -B -m uvicorn main:app --app-dir "$BACKEND_DIR" \
    --host 0.0.0.0 \
    --port 8000 \
    $RELOAD_FLAG \
    --log-level info &
BACKEND_PID=$!

echo "  Backend PID: $BACKEND_PID"

# ── Frontend ─────────────────────────────────────────────────────────────
echo -e "${GREEN}=== Starting Frontend (port 5173) ===${NC}"

cd "$FRONTEND_DIR"
"$ARENA_ENV_PREFIX/bin/node" ./node_modules/.bin/vite \
    --host 0.0.0.0 \
    --port 5173 --strictPort &
FRONTEND_PID=$!

echo "  Frontend PID: $FRONTEND_PID"

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}  Doudizhu Arena RUNNING${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "  Backend API:  ${YELLOW}http://localhost:8000/api/v1${NC}"
echo -e "  Frontend:     ${YELLOW}http://localhost:5173${NC}"
echo -e "  API Docs:     ${YELLOW}http://localhost:8000/docs${NC}"
echo -e "\n  Press Ctrl+C to stop both.\n"

# Wait for either process to exit
wait -n
