#!/usr/bin/env bash
# ==============================================================================
# TURXZ Developer Environment Bootstrap Script
# Detects runtime environment (uv / python3 venv), configures git hooks, and verifies dependencies.
# ==============================================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${BLUE}${BOLD}======================================================${NC}"
echo -e "${BLUE}${BOLD}          TURXZ AI Project Environment Bootstrap      ${NC}"
echo -e "${BLUE}${BOLD}======================================================${NC}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# 1. Verify Python Version
echo -e "\n${BLUE}[1/4] Checking Python environment...${NC}"
if command -v python3 >/dev/null 2>&1; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
    echo -e "Found Python: ${GREEN}${PY_VER}${NC} ($(which python3))"
else
    echo -e "${RED}[ERROR] Python 3 is not installed or not in PATH.${NC}"
    exit 1
fi

# 2. Virtual Environment Setup (uv preferred, fallback to venv)
echo -e "\n${BLUE}[2/4] Setting up virtual environment...${NC}"
if [ -d ".venv" ]; then
    echo -e "${GREEN}Existing virtual environment found in .venv/${NC}"
elif command -v uv >/dev/null 2>&1; then
    echo -e "Using ${BOLD}uv${NC} to create virtual environment..."
    uv venv .venv
    echo -e "${GREEN}Created .venv using uv.${NC}"
else
    echo -e "Using standard ${BOLD}python3 -m venv${NC} to create virtual environment..."
    python3 -m venv .venv
    echo -e "${GREEN}Created .venv using python3 -m venv.${NC}"
fi

# 3. Configure Git Hooks
echo -e "\n${BLUE}[3/4] Configuring strict Git hooks...${NC}"
if [ -d ".githooks" ]; then
    chmod +x .githooks/* || true
    git config core.hooksPath .githooks
    echo -e "${GREEN}Git hooksPath successfully configured to .githooks/${NC}"
    echo -e "Configured hooks:"
    ls -l .githooks | grep -E "pre-commit|pre-push" | awk '{print "  - " $9 " (mode: " $1 ")"}'
else
    echo -e "${YELLOW}[WARN] .githooks directory not found.${NC}"
fi

# 4. System & Hardware Device Permissions Check (macOS / Linux)
echo -e "\n${BLUE}[4/4] Probing hardware subsystem...${NC}"
OS_NAME="$(uname -s)"
echo -e "Operating System: ${BOLD}${OS_NAME}${NC} ($(uname -m))"

if [ "$OS_NAME" = "Darwin" ]; then
    echo -e "Checking for TURZX USB devices on macOS..."
    if command -v ioreg >/dev/null 2>&1; then
        if ioreg -p IOUSB -l 2>/dev/null | grep -iE "1a86|5722|USB35INCH" >/dev/null; then
            echo -e "${GREEN}TURZX USB hardware detected!${NC}"
        else
            echo -e "${YELLOW}No physical TURZX display detected on USB. Mock hardware mode enabled for tests.${NC}"
        fi
    fi
elif [ "$OS_NAME" = "Linux" ]; then
    echo -e "Checking for udev rules / USB access..."
    if command -v lsusb >/dev/null 2>&1; then
        if lsusb | grep -i "1a86:5722" >/dev/null; then
            echo -e "${GREEN}TURZX USB hardware detected!${NC}"
        else
            echo -e "${YELLOW}No physical TURZX display detected on USB.${NC}"
        fi
    fi
fi

echo -e "\n${GREEN}${BOLD}✓ Bootstrap completed successfully!${NC}"
echo -e "Next steps:"
echo -e "  1. Activate virtual environment: ${BOLD}source .venv/bin/activate${NC}"
echo -e "  2. Install dependencies & CLI:  ${BOLD}./scripts/setup.sh${NC}"
echo -e "  3. Run test suite:              ${BOLD}./scripts/test.sh${NC}"
echo -e "======================================================\n"
