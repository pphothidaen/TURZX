#!/usr/bin/env python3
"""
TURZX Remote USB Hub Supervisor for ZTE ZXHN F6107A.
Checks if VirtualHere USB Server is running on the router (192.168.1.1:7575).
If not, establishes a Telnet session via Factory Mode handshake and launches
vhusbdarm and watchdog.sh. Ensures VirtualHere Client on macOS claims the device
(AUTO USE ALL, USE,F6107A.31), and supervises turzx_carousel.py.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROUTER_IP = os.getenv("TURZX_ROUTER_IP", "192.168.1.1")
VH_PORT = int(os.getenv("TURZX_VH_PORT", "7575"))
VH_DEVICE_ID = os.getenv("TURZX_VH_DEVICE_ID", "F6107A.31")
VH_CLIENT_BIN = os.getenv(
    "TURZX_VH_CLIENT_BIN",
    "/Applications/VirtualHereUniversal.app/Contents/MacOS/VirtualHereUniversal",
)

# Project root path resolution
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHON = os.path.join(PROJECT_DIR, ".venv", "bin", "python")
PYTHON_BIN = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable
CAROUSEL_SCRIPT = os.path.join(PROJECT_DIR, "turzx_carousel.py")
CAROUSEL_PID_FILE = "/tmp/turzx-carousel.pid"

CAROUSEL_ARGS = [
    "--interval", "12",
    "--brightness", "55",
    "--profile", "work_environment",
    "--idle-timeout", "300",
    "--dim-brightness", "15",
    "--auto-brightness",
    "--auto-screen-off",
]

HEALTH_CHECK_INTERVAL = float(os.getenv("TURZX_SUPERVISOR_HEALTH_INTERVAL", "5.0"))

# Add scratch or local f6107a core to path if available
SCRATCH_CORE = os.path.expanduser("~/.gemini/antigravity/brain/a785e05d-a3b5-4dde-a527-506616f5ab6a/scratch/f6107a")
if os.path.exists(SCRATCH_CORE) and SCRATCH_CORE not in sys.path:
    sys.path.insert(0, SCRATCH_CORE)


def log(msg: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [Supervisor] {msg}", flush=True)


def has_matching_pid(telnet_output: str) -> bool:
    """Parse Telnet output from pgrep and check if a valid numeric PID is present."""
    for line in telnet_output.splitlines():
        line = line.strip()
        if line.isdigit() and int(line) > 0:
            return True
    return False


def is_vh_port_open(ip: str = ROUTER_IP, port: int = VH_PORT, timeout: float = 1.5) -> bool:
    """Check if the VirtualHere TCP port is open and accepting connections."""
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def ensure_vh_server_on_router(ip: str = ROUTER_IP, port: int = VH_PORT, force: bool = False) -> bool:
    """Ensure VirtualHere server and watchdog are running on the router."""
    if not force and is_vh_port_open(ip, port):
        log(f"VirtualHere Server is already active on {ip}:{port}")
        return True

    log(f"Port {port} closed on {ip} (or force={force}). Negotiating Factory Mode Telnet...")
    try:
        from core.factory_mode import FactoryModeClient
        from core.telnet_client import TelnetClient
    except ImportError:
        log("ERROR: Core ZTE factory/telnet modules not found in path.")
        return False

    try:
        (u, p), _, _ = FactoryModeClient.negotiate(ip)
        sock, run_cmd = TelnetClient.create_session(ip, u, p)
        try:
            # 1. Ensure /dev/bus symlink exists for usbfs
            run_cmd("mkdir -p /dev/bus && ln -s /proc/bus/usb /dev/bus/usb 2>/dev/null")

            # 2. Self-heal /userconfig/screen_off.sh if missing
            chk_scr = run_cmd("test -f /userconfig/screen_off.sh && echo EXISTS")
            if "EXISTS" not in chk_scr:
                log("Recreating /userconfig/screen_off.sh on router...")
                run_cmd(
                    "cat << 'EOF' > /userconfig/screen_off.sh\n"
                    "#!/bin/sh\n"
                    "for i in 1 2 3 4 5 6 7 8 9 10; do\n"
                    "    if (printf \"\\x00\\x00\\x00\\x00\\x00\\x6c\" > /dev/ttyACM0) 2>/dev/null; then\n"
                    "        exit 0\n"
                    "    fi\n"
                    "    sleep 1\n"
                    "done\n"
                    "exit 0\n"
                    "EOF\n"
                    "chmod 755 /userconfig/screen_off.sh\n"
                )

            # 3. Self-heal /userconfig/watchdog.sh if missing
            chk_wd = run_cmd("test -f /userconfig/watchdog.sh && echo EXISTS")
            if "EXISTS" not in chk_wd:
                log("Recreating /userconfig/watchdog.sh on router...")
                run_cmd(
                    "cat << 'EOF' > /userconfig/watchdog.sh\n"
                    "#!/bin/sh\n"
                    "trap '' HUP TERM\n\n"
                    "while true; do\n"
                    "    if ! grep -q -E \":1D97 [0-9A-Fa-f]+:[0-9A-Fa-f]+ 01 \" /proc/net/tcp 2>/dev/null; then\n"
                    "        (printf \"\\x00\\x00\\x00\\x00\\x00\\x6c\" > /dev/ttyACM0) 2>/dev/null\n"
                    "    fi\n"
                    "    sleep 5\n"
                    "done\n"
                    "EOF\n"
                    "chmod 755 /userconfig/watchdog.sh\n"
                )

            # 4. Self-heal /userconfig/config.ini for onUnbind hook
            ini_content = run_cmd("cat /userconfig/config.ini 2>/dev/null")
            if "onUnbind.1a86.5722" not in ini_content:
                log("Adding onUnbind hook to /userconfig/config.ini on router...")
                run_cmd("echo 'onUnbind.1a86.5722=/userconfig/screen_off.sh' >> /userconfig/config.ini")

            # 5. Ensure scripts are executable
            run_cmd("chmod 755 /userconfig/screen_off.sh /userconfig/watchdog.sh 2>/dev/null")

            # 6. Ensure watchdog is running in background
            out_wd = run_cmd("pgrep -f /userconfig/watchdog.sh")
            if not has_matching_pid(out_wd):
                log("Starting watchdog.sh on router...")
                run_cmd("/userconfig/watchdog.sh </dev/null >/dev/null 2>&1 &")

            # 7. Ensure VirtualHere server is running with /userconfig/config.ini
            out_vh = run_cmd("pgrep vhusbdarm")
            if not has_matching_pid(out_vh) or not is_vh_port_open(ip, port):
                log("Starting vhusbdarm with /userconfig/config.ini on router...")
                run_cmd("killall vhusbdarm 2>/dev/null")
                time.sleep(0.5)
                run_cmd("/userconfig/vhusbdarm -c /userconfig/config.ini -r /var/tmp/vhusbd.log -b")
        finally:
            sock.close()
    except Exception as e:
        log(f"ERROR: Failed to configure router via Telnet: {e}")
        return False

    # Wait for port to open
    for _ in range(10):
        if is_vh_port_open(ip, port):
            log(f"VirtualHere Server started successfully on {ip}:{port}!")
            return True
        time.sleep(0.5)

    log(f"ERROR: Port {port} still closed after start attempt.")
    return False


def vh_client_cmd(cmd_args: list[str]) -> str:
    """Run a VirtualHere client IPC command and capture combined output."""
    try:
        res = subprocess.run(
            [VH_CLIENT_BIN, "-t", *cmd_args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=5.0,
        )
        return res.stdout or ""
    except Exception as e:
        log(f"Warning: VirtualHere client command failed: {e}")
        return ""


def ensure_vh_client_on_mac(
    ip: str = ROUTER_IP,
    port: int = VH_PORT,
    device_id: str = VH_DEVICE_ID,
    timeout: float = 10.0,
) -> bool:
    """Ensure VirtualHere Client GUI is running and has claimed the display device."""
    if not os.path.exists(VH_CLIENT_BIN):
        log(f"ERROR: VirtualHere Client not found at {VH_CLIENT_BIN}")
        return False

    client_proc_name = Path(VH_CLIENT_BIN).name
    # 1. Check if client is running, launch minimized if not
    ps = subprocess.run(["pgrep", "-f", client_proc_name], capture_output=True, text=True)
    if not ps.stdout.strip():
        app_path = VH_CLIENT_BIN.split(".app")[0] + ".app" if ".app" in VH_CLIENT_BIN else VH_CLIENT_BIN
        log(f"Launching VirtualHere Client ({app_path}) in background...")
        subprocess.Popen(["open", "-a", app_path, "--args", "-g"])
        # Wait up to 5s for client IPC readiness
        for _ in range(10):
            time.sleep(0.5)
            if vh_client_cmd(["LIST"]).strip():
                break

    # 2. Add hub
    vh_client_cmd([f"MANUAL HUB ADD,{ip}:{port}"])

    # 3. VirtualHere 'AUTO USE ALL' is a toggle command!
    # Inspect LIST first to avoid toggling Auto-Use OFF if it is already ON.
    listing = vh_client_cmd(["LIST"])
    if "Auto-Use All currently on" not in listing:
        log("Enabling VirtualHere 'AUTO USE ALL'...")
        vh_client_cmd(["AUTO USE ALL"])
        listing = vh_client_cmd(["LIST"])

    # 4. Resolve target device address dynamically if available
    target_addr = device_id
    m = re.search(r"UsbMonitor\s+\(([^)]+)\)", listing)
    if m:
        target_addr = m.group(1).strip()

    # 5. Check if device is already in-use by you locally.
    # Note: Sending USE on an already in-use device causes VirtualHere to disable Auto-Use!
    is_in_use = any(
        (target_addr in line or "UsbMonitor" in line) and "In-use by you" in line
        for line in listing.splitlines()
    )
    if not is_in_use:
        log(f"Claiming TURZX USB Display ({target_addr})...")
        vh_client_cmd([f"USE,{target_addr}"])
    else:
        log(f"TURZX USB Display ({target_addr}) is already attached and in use locally!")
        return True

    # 6. Wait for device attachment
    start_t = time.time()
    last_listing = ""
    while time.time() - start_t < timeout:
        listing = vh_client_cmd(["LIST"])
        last_listing = listing
        for line in listing.splitlines():
            if (target_addr in line or "UsbMonitor" in line) and "In-use by you" in line:
                log(f"TURZX USB Display ({target_addr}) is attached and in use locally!")
                return True
        time.sleep(1.0)

    log(f"WARNING: Device {target_addr} not marked 'In-use by you'. Listing:\n{last_listing.strip()}")
    # Check if local serial port exists as fallback indicator
    for dev in Path("/dev").glob("cu.usbmodem*"):
        if "USB35" in dev.name or "CH34" in dev.name:
            log(f"Detected active local device node: {dev}")
            return True

    return False


def _is_process_alive(pid: int) -> bool:
    """Portable POSIX process existence check."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


def terminate_stale_carousel(exclude_pid: int | None = None) -> None:
    """Terminate any existing turzx_carousel.py processes to prevent device lock contention."""
    # Check PID file
    pid_p = Path(CAROUSEL_PID_FILE)
    if pid_p.is_file():
        try:
            pid = int(pid_p.read_text(encoding="utf-8").strip())
            if pid != exclude_pid and pid != os.getpid():
                log(f"Terminating existing carousel process (PID {pid}) from pidfile...")
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(1.0)
                    if _is_process_alive(pid):
                        os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
        except Exception:
            pass
        pid_p.unlink(missing_ok=True)

    # Check for any remaining running instances
    try:
        ps = subprocess.run(["pgrep", "-f", "turzx_carousel.py"], capture_output=True, text=True)
        for line in ps.stdout.strip().splitlines():
            try:
                p = int(line.strip())
                if p != exclude_pid and p != os.getpid():
                    log(f"Killing orphan carousel process PID {p}...")
                    os.kill(p, signal.SIGTERM)
                    time.sleep(0.5)
                    if _is_process_alive(p):
                        os.kill(p, signal.SIGKILL)
            except (ValueError, ProcessLookupError, OSError):
                pass
    except Exception:
        pass


def run_supervisor() -> None:
    """Main supervisor loop that ensures connection and maintains turzx_carousel.py."""
    log("=" * 60)
    log("Starting TURZX Remote USB Hub Supervisor")
    log(f"Router Target:    {ROUTER_IP}:{VH_PORT}")
    log(f"Display Device:   {VH_DEVICE_ID}")
    log(f"Python Runtime:   {PYTHON_BIN}")
    log(f"Carousel Script:  {CAROUSEL_SCRIPT}")
    log(f"Health Interval:  {HEALTH_CHECK_INTERVAL}s")
    log("=" * 60)

    # Initialize Rule 5 session process token if available
    try:
        from turzx.auth import generate_process_token
        generate_process_token(force_regenerate=True)
        log("Initialized secure local process token (Rule 5 compliance)")
    except Exception as e:
        log(f"Process token note: {e}")

    stop_requested = False
    active_child: subprocess.Popen | None = None

    def handle_shutdown(signum, frame):
        nonlocal stop_requested
        sig_name = signal.Signals(signum).name
        log(f"Received signal {sig_name}. Initiating graceful shutdown...")
        stop_requested = True
        if active_child and active_child.poll() is None:
            log(f"Sending SIGTERM to carousel child process (PID {active_child.pid})...")
            active_child.terminate()
            try:
                active_child.wait(timeout=4.0)
            except subprocess.TimeoutExpired:
                log("Child process did not exit within 4s. Sending SIGKILL...")
                active_child.kill()

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Clean up any stale instances before starting
    terminate_stale_carousel()

    backoff_seconds = 2.0
    while not stop_requested:
        # Step 1: Ensure router server
        if not ensure_vh_server_on_router(ROUTER_IP, VH_PORT):
            log(f"Router server check failed. Retrying in {backoff_seconds:.1f}s...")
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 1.5, 30.0)
            continue

        # Step 2: Ensure client on macOS
        if not ensure_vh_client_on_mac(ROUTER_IP, VH_PORT, VH_DEVICE_ID):
            log(f"Client device claim check failed. Retrying in {backoff_seconds:.1f}s...")
            time.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 1.5, 30.0)
            continue

        # Reset backoff upon successful connectivity
        backoff_seconds = 2.0

        # Step 3: Launch carousel subprocess
        terminate_stale_carousel()
        cmd = [PYTHON_BIN, "-u", CAROUSEL_SCRIPT] + CAROUSEL_ARGS
        log(f"Launching carousel process: {' '.join(cmd)}")
        try:
            active_child = subprocess.Popen(cmd, cwd=PROJECT_DIR)
            log(f"Carousel started successfully with PID {active_child.pid}.")
        except Exception as e:
            log(f"ERROR: Failed to spawn carousel: {e}")
            time.sleep(5.0)
            continue

        # Step 4: Monitor carousel process and perform active periodic health checks
        last_health_check = time.time()
        while not stop_requested:
            exit_code = active_child.poll()
            if exit_code is not None:
                log(f"Carousel process (PID {active_child.pid}) terminated with exit code {exit_code}.")
                active_child = None
                break

            now = time.time()
            if now - last_health_check >= HEALTH_CHECK_INTERVAL:
                last_health_check = now

                # Probe 1: Router VirtualHere server port (e.g. detect router reboot/crash)
                if not is_vh_port_open(ROUTER_IP, VH_PORT):
                    log(f"Port {VH_PORT} unreachable on {ROUTER_IP} (router reboot/crash detected). Re-establishing...")
                    ensure_vh_server_on_router(ROUTER_IP, VH_PORT, force=True)
                    ensure_vh_client_on_mac(ROUTER_IP, VH_PORT, VH_DEVICE_ID)

                # Probe 2: VirtualHere Client process on macOS
                client_proc_name = Path(VH_CLIENT_BIN).name
                ps_chk = subprocess.run(["pgrep", "-f", client_proc_name], capture_output=True, text=True)
                if not ps_chk.stdout.strip():
                    log("VirtualHere Client not running on macOS. Recovering client...")
                    ensure_vh_client_on_mac(ROUTER_IP, VH_PORT, VH_DEVICE_ID)

            time.sleep(1.0)

        if stop_requested:
            break

        # If child exited unexpectedly, wait brief recovery delay before reconnecting
        log("Re-verifying pipeline and restarting carousel in 3 seconds...")
        time.sleep(3.0)

    log("Supervisor shutdown complete.")


def main() -> int:
    parser = argparse.ArgumentParser(description="TURZX Remote USB Hub Supervisor")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run pre-flight connection verification once and exit immediately",
    )
    args = parser.parse_args()

    if args.once:
        log("Running preflight check (--once)...")
        server_ok = ensure_vh_server_on_router(ROUTER_IP, VH_PORT)
        client_ok = ensure_vh_client_on_mac(ROUTER_IP, VH_PORT, VH_DEVICE_ID)
        if server_ok and client_ok:
            log("Pipeline preflight PASSED: Router USB-over-IP is ready.")
            return 0
        else:
            log("Pipeline preflight FAILED: Could not establish ready state.")
            return 1

    run_supervisor()
    return 0


if __name__ == "__main__":
    sys.exit(main())
