#!/usr/bin/env bash
# ==============================================================================
# TURXZ Standard Test Runner
# Supports flags: --fast, --all, --coverage (and arbitrary pytest arguments).
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Determine pytest binary
if [ -x ".venv/bin/pytest" ]; then
    PYTEST_BIN=".venv/bin/pytest"
elif command -v pytest >/dev/null 2>&1; then
    PYTEST_BIN="pytest"
elif [ -x ".venv/bin/python" ]; then
    PYTEST_BIN=".venv/bin/python -m pytest"
else
    PYTEST_BIN="python3 -m pytest"
fi

RUN_MODE="all"
EXTRA_ARGS=()

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --fast)
            RUN_MODE="fast"
            shift
            ;;
        --all)
            RUN_MODE="all"
            shift
            ;;
        --coverage)
            RUN_MODE="coverage"
            shift
            ;;
        -h|--help)
            echo "TURXZ Test Runner"
            echo "Usage: ./scripts/test.sh [OPTIONS] [PYTEST_ARGS...]"
            echo ""
            echo "Options:"
            echo "  --fast       Run fast unit tests (skip slow integration runs)"
            echo "  --all        Run complete test suite (default)"
            echo "  --coverage   Run complete test suite with coverage report"
            echo "  --help       Show this help message"
            exit 0
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

echo -e "${BLUE}${BOLD}[TURXZ Test Runner]${NC} Mode: ${BOLD}${RUN_MODE}${NC}"

case "$RUN_MODE" in
    fast)
        echo -e "${BLUE}Running fast test suite (excluding slow integration tests)...${NC}"
        $PYTEST_BIN tests/ -m "not slow" -q "${EXTRA_ARGS[@]:+${EXTRA_ARGS[@]}}"
        ;;
    coverage)
        echo -e "${BLUE}Running test suite with code coverage analysis...${NC}"
        $PYTEST_BIN tests/ \
            --cov=turzx \
            --cov=turxz_scheduled_jobs \
            --cov-report=term-missing \
            --cov-report=xml:coverage.xml \
            "${EXTRA_ARGS[@]:+${EXTRA_ARGS[@]}}"
        ;;
    all|*)
        echo -e "${BLUE}Running complete test suite...${NC}"
        $PYTEST_BIN tests/ -v "${EXTRA_ARGS[@]:+${EXTRA_ARGS[@]}}"
        ;;
esac

echo -e "\n${GREEN}${BOLD}✓ Test run completed successfully!${NC}"
