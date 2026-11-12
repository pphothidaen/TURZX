#!/usr/bin/env bash
# ==============================================================================
# TURXZ Development Setup Script
# Installs packages in editable mode with development dependencies.
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BLUE}${BOLD}======================================================${NC}"
echo -e "${BLUE}${BOLD}            TURXZ Package Setup (Editable)            ${NC}"
echo -e "${BLUE}${BOLD}======================================================${NC}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Determine python and pip binaries
if [ -x ".venv/bin/pip" ]; then
    PIP_BIN=".venv/bin/pip"
    PYTHON_BIN=".venv/bin/python"
elif [ -n "${VIRTUAL_ENV:-}" ] && command -v pip >/dev/null 2>&1; then
    PIP_BIN="pip"
    PYTHON_BIN="python"
elif command -v uv >/dev/null 2>&1; then
    PIP_BIN="uv pip"
    PYTHON_BIN="python3"
else
    echo -e "${RED}[ERROR] No active virtual environment found. Please run ./scripts/bootstrap.sh first.${NC}"
    exit 1
fi

echo -e "\n${BLUE}[1/3] Installing TURZX in editable mode with dev dependencies...${NC}"
$PIP_BIN install -e ".[dev]"

echo -e "\n${BLUE}[2/3] Verifying CLI entrypoints...${NC}"
TURZX_BIN=""
SCHEDULER_BIN=""

if [ -x ".venv/bin/turzx" ]; then
    TURZX_BIN=".venv/bin/turzx"
elif command -v turzx >/dev/null 2>&1; then
    TURZX_BIN="turzx"
fi

if [ -x ".venv/bin/turxz-scheduler" ]; then
    SCHEDULER_BIN=".venv/bin/turxz-scheduler"
elif command -v turxz-scheduler >/dev/null 2>&1; then
    SCHEDULER_BIN="turxz-scheduler"
fi

if [ -n "$TURZX_BIN" ]; then
    echo -e "Testing ${BOLD}turzx --help${NC}..."
    $TURZX_BIN --help >/dev/null
    echo -e "${GREEN}✓ 'turzx' CLI entrypoint operational.${NC}"
else
    echo -e "${RED}[ERROR] 'turzx' entrypoint not found.${NC}"
    exit 1
fi

if [ -n "$SCHEDULER_BIN" ]; then
    echo -e "Testing ${BOLD}turxz-scheduler --help${NC}..."
    $SCHEDULER_BIN --help >/dev/null
    echo -e "${GREEN}✓ 'turxz-scheduler' CLI entrypoint operational.${NC}"
else
    echo -e "${RED}[ERROR] 'turxz-scheduler' entrypoint not found.${NC}"
    exit 1
fi

echo -e "\n${BLUE}[3/3] Verifying Git hook registration...${NC}"
CURRENT_HOOKS=$(git config core.hooksPath || echo "not set")
if [ "$CURRENT_HOOKS" != ".githooks" ]; then
    echo -e "Binding core.hooksPath to .githooks..."
    git config core.hooksPath .githooks
fi
echo -e "${GREEN}✓ Git hooks active at .githooks${NC}"

echo -e "\n${GREEN}${BOLD}✓ Environment setup complete! You are ready to develop.${NC}"
echo -e "Run tests with: ${BOLD}./scripts/test.sh${NC}"
echo -e "======================================================\n"
