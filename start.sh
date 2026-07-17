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

PROJECT_DIR="/home/k4s9/Doudizhu-Arena"
BACKEND_DIR="$PROJECT_DIR/src/backend"
FRONTEND_DIR="$PROJECT_DIR/src/frontend"
CONDA_ENV="doudizhu-arena"
CONDA_PREFIX="/home/k4s9/miniconda3/envs/$CONDA_ENV"

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

# Clean up function
cleanup() {
    echo -e "\n${YELLOW}Shutting down...${NC}"
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
    wait "$BACKEND_PID" 2>/dev/null
    wait "$FRONTEND_PID" 2>/dev/null
    echo -e "${GREEN}All processes stopped.${NC}"
}
trap cleanup EXIT INT TERM

# ── Backend ──────────────────────────────────────────────────────────────
echo -e "${GREEN}=== Starting Backend (port 8000) ===${NC}"

export PATH="$CONDA_PREFIX/bin:$PATH"

cd "$BACKEND_DIR"
"$CONDA_PREFIX/bin/python" -m uvicorn main:app \
    --host 0.0.0.0 \
    --port 8000 \
    $RELOAD_FLAG \
    --log-level info &
BACKEND_PID=$!

echo "  Backend PID: $BACKEND_PID"

# ── Frontend ─────────────────────────────────────────────────────────────
echo -e "${GREEN}=== Starting Frontend (port 5173) ===${NC}"

cd "$FRONTEND_DIR"
"$CONDA_PREFIX/bin/node" ./node_modules/.bin/vite \
    --host 0.0.0.0 \
    --port 5173 &
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
wait
