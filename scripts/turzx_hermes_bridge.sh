#!/bin/zsh
# ==============================================================================
# turzx_hermes_bridge.sh — Seamless Autostart & Autoclose for TURZX Display
# ==============================================================================
# Integrates TURZX Carousel Telemetry Display with the Hermes Agent lifecycle.
#
# Commands:
#   start     Start the display carousel in background (if screen is connected)
#   stop      Stop the display carousel and blank the screen (autoclose)
#   restart   Stop and restart the carousel display
#   status    Check status of display process and hardware
#   install   Hook into ~/.hermes/bin/hermes-lifecycle.sh automatically
#   uninstall Remove hook from ~/.hermes/bin/hermes-lifecycle.sh
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TURZX_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="$TURZX_DIR/.venv/bin/python"
TURZX_BIN="$TURZX_DIR/.venv/bin/turzx"
CAROUSEL_SCRIPT="$TURZX_DIR/turzx_carousel.py"

PID_FILE="/tmp/turzx-carousel.pid"
# Locate real HERMES_HOME dynamically
if [[ -n "${HERMES_HOME:-}" && -f "$HERMES_HOME/bin/hermes-lifecycle.sh" ]]; then
    ACTUAL_HERMES_DIR="$HERMES_HOME"
elif [[ -f "/Users/kimlenglim/.hermes/bin/hermes-lifecycle.sh" ]]; then
    ACTUAL_HERMES_DIR="/Users/kimlenglim/.hermes"
elif [[ -f "$HOME/.hermes/bin/hermes-lifecycle.sh" ]]; then
    ACTUAL_HERMES_DIR="$HOME/.hermes"
else
    ACTUAL_HERMES_DIR="/Users/kimlenglim/.hermes"
fi

HERMES_LIFECYCLE="$ACTUAL_HERMES_DIR/bin/hermes-lifecycle.sh"
LOG_DIR="/Users/kimlenglim/Library/Logs/hermes"
LOG_FILE="$LOG_DIR/turzx-display.log"

log() { /bin/echo "[$(/bin/date '+%H:%M:%S')] [TURZX-Bridge] $*"; }

# System Resource Limit (Requirement 3: ulimit -n 65536)
ensure_resource_limits() {
    ulimit -n 65536 2>/dev/null || true
}

# Memory Pressure Check (Requirement 1: awk-based check, threshold 20% free RAM)
check_memory_pressure() {
    if command -v memory_pressure >/dev/null 2>&1; then
        if [ $(memory_pressure | awk '/System-wide memory free percentage:/ {gsub("%",""); print ($NF < 20) ? 1 : 0}') -eq 1 ]; then
            log "⚠️ High memory pressure detected (< 20% free RAM). Throttling display and scaling resources."
            return 1
        fi
    fi
    return 0
}

is_hardware_connected() {
    # Check for Turing 3.5" USB controller (0x1A86:0x5722)
    if /usr/sbin/system_profiler SPUSBDataType 2>/dev/null | grep -q "0x1a86"; then
        return 0
    elif [[ -c "/dev/cu.usbmodemUSB35INCHIPSV21" ]]; then
        return 0
    else
        return 1
    fi
}

start_display() {
    log "Initiating TURZX display autostart..."
    mkdir -p "$LOG_DIR"
    ensure_resource_limits

    # Check if already running
    if [[ -f "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null; then
        log "TURZX Carousel is already running (PID $(<"$PID_FILE"))"
        return 0
    fi

    # Fail-closed check: verify hardware presence before launching
    if ! is_hardware_connected; then
        log "⚠️ TURZX hardware not detected (0x1A86:0x5722). Skipping autostart safely."
        return 0
    fi

    # Adaptive power & resource scaling based on memory pressure
    local power_args=(--interval 10 --brightness 60 --profile work_environment --idle-timeout 300 --dim-brightness 15 --auto-brightness --auto-screen-off)
    if ! check_memory_pressure; then
        log "Enforcing EFFICIENT power mode and throttled cadence due to memory pressure."
        power_args=(--interval 20 --brightness 40 --power-mode efficient --idle-timeout 180 --dim-brightness 10 --auto-brightness --auto-screen-off)
    fi

    log "Hardware detected. Starting Carousel in background..."
    (
        cd "$TURZX_DIR"
        nohup "$PYTHON_BIN" -u "$CAROUSEL_SCRIPT" "${power_args[@]}" >> "$LOG_FILE" 2>&1 &
        echo $! > "$PID_FILE"
    )
    local pid
    pid="$(<"$PID_FILE")"
    
    # Wait briefly to confirm start
    sleep 1.5
    if kill -0 "$pid" 2>/dev/null; then
        log "✅ TURZX Carousel started successfully (PID $pid) -> Log: $LOG_FILE"
    else
        log "❌ Startup failed or exited prematurely. Check $LOG_FILE"
        rm -f "$PID_FILE"
    fi
}

stop_display() {
    log "Initiating TURZX display autoclose..."
    
    # 1. Kill by PID file if present
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            log "Sent SIGTERM to process $pid"
            local waited=0
            while kill -0 "$pid" 2>/dev/null && (( waited < 4 )); do
                sleep 0.5
                waited=$((waited + 1))
            done
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
                log "Sent SIGKILL to process $pid"
            fi
        fi
        rm -f "$PID_FILE"
    fi

    # 2. Cleanup any remaining carousel process instances
    pkill -f "turzx_carousel.py" 2>/dev/null || true

    # 3. Autoclose panel action: blank the display screen to conserve hardware
    if is_hardware_connected; then
        log "Clearing display screen to black (panel standby)..."
        (
            cd "$TURZX_DIR"
            "$TURZX_BIN" clear >/dev/null 2>&1 || true
        )
        log "✅ Display screen cleared cleanly."
    fi

    log "TURZX autoclose completed."
}

show_status() {
    echo "=== TURZX Hermes Bridge Status ==="
    if [[ -f "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null; then
        echo "  Service:  RUNNING (PID $(<"$PID_FILE"))"
    elif pgrep -f "turzx_carousel.py" >/dev/null; then
        echo "  Service:  RUNNING (untracked PID $(pgrep -f 'turzx_carousel.py' | head -1))"
    else
        echo "  Service:  STOPPED"
    fi

    if is_hardware_connected; then
        echo "  Hardware: CONNECTED (0x1A86:0x5722)"
    else
        echo "  Hardware: NOT CONNECTED"
    fi
    echo "  Log File: $LOG_FILE"
    echo "=================================="
}

install_hook() {
    if [[ ! -f "$HERMES_LIFECYCLE" ]]; then
        echo "❌ Cannot find $HERMES_LIFECYCLE"
        exit 1
    fi

    if grep -q "turzx_hermes_bridge.sh" "$HERMES_LIFECYCLE"; then
        echo "✅ Hook already installed in $HERMES_LIFECYCLE"
        return 0
    fi

    echo "Backing up $HERMES_LIFECYCLE to $HERMES_LIFECYCLE.bak..."
    cp "$HERMES_LIFECYCLE" "$HERMES_LIFECYCLE.bak"

    echo "Injecting autostart and autoclose hooks into $HERMES_LIFECYCLE..."
    
    # 1. Add hook in start_services()
    sed -i '' "/start_services() {/a\\
    # TURZX Display Autostart\\
    if [[ -x \"$SCRIPT_DIR/turzx_hermes_bridge.sh\" ]]; then\\
        \"$SCRIPT_DIR/turzx_hermes_bridge.sh\" start || true\\
    fi\\
" "$HERMES_LIFECYCLE"

    # 2. Add hook in stop_services()
    sed -i '' "/stop_services() {/a\\
    # TURZX Display Autoclose\\
    if [[ -x \"$SCRIPT_DIR/turzx_hermes_bridge.sh\" ]]; then\\
        \"$SCRIPT_DIR/turzx_hermes_bridge.sh\" stop || true\\
    fi\\
" "$HERMES_LIFECYCLE"

    echo "✅ Successfully hooked into Hermes lifecycle!"
    echo "Now whenever Hermes starts or stops, TURZX display will autostart and autoclose automatically."
}

uninstall_hook() {
    if [[ -f "$HERMES_LIFECYCLE.bak" ]]; then
        echo "Restoring $HERMES_LIFECYCLE from backup..."
        cp "$HERMES_LIFECYCLE.bak" "$HERMES_LIFECYCLE"
        echo "✅ Hook uninstalled successfully."
    else
        echo "Removing TURZX hook lines from $HERMES_LIFECYCLE..."
        sed -i '' '/TURZX Display/d' "$HERMES_LIFECYCLE"
        sed -i '' '/turzx_hermes_bridge\.sh/d' "$HERMES_LIFECYCLE"
        echo "✅ Hook removed."
    fi
}

case "${1:-status}" in
    start)      start_display ;;
    stop)       stop_display ;;
    restart)    stop_display; sleep 1; start_display ;;
    status)     show_status ;;
    power)
        if [[ -n "${2:-}" ]]; then
            "$PYTHON_BIN" -m turzx.cli power-mode "$2"
        else
            "$PYTHON_BIN" -m turzx.cli power-mode
        fi
        ;;
    brightness)
        if [[ -n "${2:-}" ]]; then
            "$PYTHON_BIN" -m turzx.cli brightness "$2"
        else
            "$PYTHON_BIN" -m turzx.cli brightness --profile work_environment
        fi
        ;;
    memory)
        if command -v memory_pressure >/dev/null 2>&1; then
            free_pct=$(memory_pressure | awk '/System-wide memory free percentage:/ {gsub("%",""); print $NF}')
            echo "macOS System-wide Free Memory: ${free_pct}%"
            if [ $(memory_pressure | awk '/System-wide memory free percentage:/ {gsub("%",""); print ($NF < 20) ? 1 : 0}') -eq 1 ]; then
                echo "Status: HIGH MEMORY PRESSURE (<20% free RAM) -> Throttling Active"
            else
                echo "Status: HEALTHY (>=20% free RAM)"
            fi
        else
            echo "memory_pressure command not found on host"
        fi
        ;;
    limits)
        ensure_resource_limits
        echo "File descriptor limits: $(ulimit -n)"
        ;;
    install)    install_hook ;;
    uninstall)  uninstall_hook ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|power [mode]|brightness [level]|memory|limits|install|uninstall}"
        exit 1
        ;;
esac
