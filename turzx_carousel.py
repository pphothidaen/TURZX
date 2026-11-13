#!/usr/bin/env python3
"""
TURZX Carousel Display Runner.
Rotates through dedicated focus views on the 3.5" IPS display:
  [ ● ○ ○ ] Page 0: Cluster Overview (8 agents summary)
  [ ○ ● ○ ] Page 1: AGY Cluster Deep-Dive (Gemini, Claude, Argon sub-quotas)
  [ ○ ○ ● ] Page 2: Codex Cluster Deep-Dive (Primary, Secondary, Reasoning + Thread telemetry)
Adaptive pagination (4 pages) activates automatically when total agents > 8.
Supports real-time SIGUSR1 signal interrupts for immediate reload upon dynamic agent mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from turzx import TURZXDriver, image_to_rgb565
from turzx.protocol import TURZXConnectionError, TURZXProtocolError
from turzx.power import (
    BrightnessProfile,
    DisplayPowerManager,
    PowerMode,
    SystemPowerManager,
    compute_clamped_brightness,
    get_macbook_brightness,
    get_macos_user_inactivity_seconds,
    is_macos_screen_locked,
)
from generate_carousel_pages import (
    get_carousel_pages,
)

try:
    import usb.core
    USB_ERRORS: tuple[type[Exception], ...] = (usb.core.USBError,)
except ImportError:
    USB_ERRORS = ()

COMMUNICATION_ERRORS = (TURZXProtocolError, TURZXConnectionError, OSError, IOError, *USB_ERRORS)

PID_FILE = "/tmp/turzx-carousel.pid"
reload_event = threading.Event()


def handle_sigusr1(signum, frame):
    """Signal handler for SIGUSR1 to trigger immediate carousel reload."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"\n[{timestamp}] [Signal] SIGUSR1 received - triggering immediate carousel reload...")
    reload_event.set()


def setup_signal_handlers():
    """Register signal handlers for carousel reload."""
    sig = getattr(signal, "SIGUSR1", None)
    if sig is not None:
        try:
            signal.signal(sig, handle_sigusr1)
        except (ValueError, OSError):
            # Signal handling requires main thread
            pass


def write_pid_file(pid_path: str = PID_FILE) -> None:
    """Record carousel runner PID to PID_FILE."""
    try:
        Path(pid_path).write_text(str(os.getpid()), encoding="utf-8")
    except Exception as e:
        print(f"Warning: Failed to write {pid_path}: {e}")


def cleanup_pid_file(pid_path: str = PID_FILE) -> None:
    """Remove PID file on clean shutdown if owned by current process."""
    try:
        p = Path(pid_path)
        if p.is_file():
            content = p.read_text(encoding="utf-8").strip()
            if content == str(os.getpid()):
                p.unlink(missing_ok=True)
    except Exception:
        pass


def run_carousel(
    interval: float = 10.0,
    brightness: int = 80,
    single_page: int | None = None,
    driver: Any = None,
    stop_event: threading.Event | None = None,
    pid_file: str = PID_FILE,
    provisioner: Any = None,
    reconnect_interval: float = 2.0,
    power_mode: str | None = None,
    profile: str | None = None,
    idle_timeout: float = 300.0,
    dim_brightness: int = 15,
    auto_brightness: bool = False,
    auto_screen_off: bool = False,
):
    # Resolve adaptive brightness profile if specified
    if profile:
        profile_map = {
            "work": BrightnessProfile.WORK_ENVIRONMENT,
            "work_environment": BrightnessProfile.WORK_ENVIRONMENT,
            "deep_work": BrightnessProfile.DEEP_WORK,
            "deep": BrightnessProfile.DEEP_WORK,
            "presentation": BrightnessProfile.PRESENTATION,
            "present": BrightnessProfile.PRESENTATION,
        }
        prof = profile_map.get(profile.lower())
        if prof:
            brightness = prof.default_brightness

    # Resolve adaptive power mode if specified
    if power_mode:
        try:
            pm = SystemPowerManager.get_instance()
            pm.set_power_mode(power_mode)
            if not profile:
                brightness = pm.get_power_mode().default_brightness
            if power_mode.lower() == "minimum":
                interval = max(interval, 20.0)
        except Exception:
            pass

    display_power_mgr = DisplayPowerManager(
        idle_timeout=idle_timeout,
        dim_brightness=dim_brightness,
        auto_brightness=auto_brightness,
    )

    print("=======================================================")
    print("TURZX CAROUSEL TELEMETRY DISPLAY")
    print("=======================================================")
    print(f"  Page Cadence:    {interval:.1f}s per view")
    print(f"  Brightness:      {brightness}%" + (f" [Profile: {profile}]" if profile else ""))
    if auto_brightness:
        print("  Auto-Brightness: ON (15-85% panel clamp, 3% hysteresis)")
    if auto_screen_off:
        print(f"  Auto Screen Off: ON (Lock screen & {idle_timeout:.0f}s inactivity)")
    if power_mode:
        print(f"  Power Mode:      {power_mode.upper()}")
    print("  Views:           Adaptive (3 Pages <= 8 agents, 4 Pages > 8 agents)")
    print(f"  PID File:        {pid_file} (PID {os.getpid()})")
    print("=======================================================")

    setup_signal_handlers()
    write_pid_file(pid_file)

    own_driver = False
    if driver is None:
        driver = TURZXDriver(port="AUTO", baudrate=115200, prefer_usb=True)
        if not driver.connect():
            print("❌ Error: Failed to connect to TURZX display hardware.")
            cleanup_pid_file(pid_file)
            sys.exit(1)
        own_driver = True

    try:
        display_power_mgr.driver = driver
        screen_locked_at_start = auto_screen_off and is_macos_screen_locked()
        if hasattr(driver, "screen_on") and not screen_locked_at_start:
            try:
                driver.screen_on()
            except Exception:
                pass
        if auto_brightness:
            applied = display_power_mgr.sync_host_brightness(driver=driver, force=True)
            if applied is not None:
                brightness = applied
        else:
            display_power_mgr.set_brightness(brightness, driver=driver)

        pages = get_carousel_pages(prov=provisioner)

        if single_page is not None:
            if single_page < 0 or single_page >= len(pages):
                print(f"Invalid page index: {single_page}. Must be between 0 and {len(pages)-1}.")
                if own_driver:
                    sys.exit(1)
                return
            title, renderer = pages[single_page]
            print(f"Rendering single view: {title}...")
            img = renderer()
            raw_rgb565 = image_to_rgb565(img)
            frame_hash = hashlib.blake2b(raw_rgb565, digest_size=8).hexdigest()
            try:
                success = driver.display_image(raw_rgb565)
                if not success:
                    print(f"⚠️ Warning: Display frame write reported failure for {title}")
                else:
                    print(f"✅ {title} displayed successfully (hash: {frame_hash}).")
            except COMMUNICATION_ERRORS as err:
                print(f"❌ Failed to display {title}: {err}")
                if own_driver:
                    sys.exit(1)
            return

        print("\nStarting carousel loop (Press Ctrl+C to stop)...")
        idx = 0
        last_rendered_hash: str | None = None
        last_rendered_page_idx: int | None = None
        force_render: bool = False

        while not (stop_event and stop_event.is_set()):
            # 1. Lock screen gate (Requirement 2: auto_screen_off)
            if auto_screen_off:
                locked = is_macos_screen_locked()
                if locked:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] 🌙 macOS lock screen active -> Invoking driver.screen_off() & pausing carousel loop...")
                    display_power_mgr.screen_off(driver=driver)

                    # Low-power polling wait loop
                    cur_locked = locked
                    while not (stop_event and stop_event.is_set()):
                        poll_timeout = min(1.0, max(0.05, interval / 2.0))
                        if stop_event and stop_event.wait(timeout=poll_timeout):
                            # Check lock state one last time before exiting on stop_event
                            cur_locked = is_macos_screen_locked()
                            break
                        elif not stop_event:
                            time.sleep(poll_timeout)

                        cur_locked = is_macos_screen_locked()
                        if not cur_locked:
                            # User returned and screen unlocked!
                            break

                    if not cur_locked:
                        timestamp = datetime.now().strftime("%H:%M:%S")
                        print(f"[{timestamp}] ☀️ User active / screen unlocked -> Invoking driver.screen_on() & restoring display...")
                        display_power_mgr.screen_on(driver=driver)
                        display_power_mgr.record_activity(driver=driver)
                        if auto_brightness:
                            display_power_mgr.sync_host_brightness(driver=driver, force=True)
                        else:
                            display_power_mgr.restore_active_brightness(driver=driver)
                        force_render = True
                    elif stop_event and stop_event.is_set():
                        break

            # 2. Host brightness synchronization (Requirement 1: 15-85% clamp, 3% hysteresis)
            if auto_brightness and display_power_mgr.is_screen_on:
                display_power_mgr.sync_host_brightness(driver=driver)

            pages = get_carousel_pages(prov=provisioner)
            idx = idx % len(pages)
            title, renderer = pages[idx]

            img = renderer()
            raw_rgb565 = image_to_rgb565(img)
            frame_hash = hashlib.blake2b(raw_rgb565, digest_size=8).hexdigest()

            # 3. Event-Driven Frame Caching & Redundant Transfer Elimination (Requirement 3)
            page_switched = (idx != last_rendered_page_idx)
            content_changed = (frame_hash != last_rendered_hash)

            if not force_render and not page_switched and not content_changed:
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] Frame content unchanged (hash: {frame_hash}), skipping redundant 307.2 KB USB transfer.")
            else:
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] Displaying {title} (View {idx+1}/{len(pages)}, hash: {frame_hash})...")
                # Auto-reconnect & USB glitch recovery loop (Incident Runbook 1)
                try:
                    success = driver.display_image(raw_rgb565)
                    if not success:
                        raise TURZXProtocolError("Display frame transmission failed (display_image returned False)")
                    last_rendered_hash = frame_hash
                    last_rendered_page_idx = idx
                    force_render = False
                except COMMUNICATION_ERRORS as err:
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    print(f"[{timestamp}] ⚠️ Communication / USB glitch during display: {err}")
                    print(f"[{timestamp}] Initiating Incident Runbook 1: Reconnecting display hardware...")

                    # Fail-closed: safely release stale driver / USB resources
                    try:
                        if hasattr(driver, "disconnect"):
                            driver.disconnect()
                    except Exception as disc_err:
                        print(f"[{timestamp}] Driver disconnect notice: {disc_err}")

                    reconnected = False
                    while not (stop_event and stop_event.is_set()):
                        # Incident Runbook 1: Wait 1.5 - 3.0 seconds before re-probing descriptor
                        if stop_event:
                            if stop_event.wait(timeout=reconnect_interval):
                                break
                        else:
                            time.sleep(reconnect_interval)

                        timestamp = datetime.now().strftime("%H:%M:%S")
                        try:
                            print(f"[{timestamp}] Re-probing USB/Serial descriptors and reconnecting...")
                            if driver.connect():
                                if auto_brightness:
                                    display_power_mgr.sync_host_brightness(driver=driver, force=True)
                                elif hasattr(driver, "set_brightness"):
                                    try:
                                        driver.set_brightness(display_power_mgr.current_brightness)
                                    except Exception:
                                        pass
                                print(f"[{timestamp}] ✅ TURZX display reconnected successfully!")
                                reconnected = True
                                # Attempt retransmission of frame after reconnect
                                try:
                                    driver.display_image(raw_rgb565)
                                    last_rendered_hash = frame_hash
                                    last_rendered_page_idx = idx
                                    force_render = False
                                except Exception as retry_err:
                                    print(f"[{timestamp}] Post-reconnect display write notice: {retry_err}")
                                break
                            else:
                                print(f"[{timestamp}] Hardware probe unsuccessful, retrying...")
                        except COMMUNICATION_ERRORS as reconn_err:
                            print(f"[{timestamp}] Reconnection attempt failed: {reconn_err}")
                        except Exception as gen_err:
                            print(f"[{timestamp}] Unexpected error during reconnect probe: {gen_err}")

                    if stop_event and stop_event.is_set():
                        break

                    if not reconnected:
                        continue

            # Wait on threading.Event with timeout=interval
            signaled = reload_event.wait(timeout=interval)
            reload_event.clear()

            if stop_event and stop_event.is_set():
                break

            if signaled:
                display_power_mgr.record_activity(driver=driver)
            else:
                display_power_mgr.check_idle(driver=driver, check_host_inactivity=auto_screen_off)

            # Immediately re-evaluate carousel pages (3 pages when <= 8, 4 pages when > 8)
            pages = get_carousel_pages(prov=provisioner)
            if signaled:
                # Reload immediately; keep valid index for current view
                idx = idx % len(pages)
            else:
                idx = (idx + 1) % len(pages)

    except KeyboardInterrupt:
        print("\nStopping carousel loop gracefully...")
    finally:
        cleanup_pid_file(pid_file)
        if own_driver and hasattr(driver, "disconnect"):
            driver.disconnect()
            print("Driver disconnected cleanly.")


def main():
    parser = argparse.ArgumentParser(description="TURZX Multi-Agent Quota Carousel Display")
    parser.add_argument("--interval", "-i", type=float, default=10.0, help="Interval in seconds between page rotations (default: 10s)")
    parser.add_argument("--brightness", "-b", type=int, default=80, help="Display brightness level 0-100 (default: 80)")
    parser.add_argument("--page", "-p", type=int, default=None, help="Display a specific single page instead of rotating")
    parser.add_argument("--reconnect-delay", type=float, default=2.0, help="Delay in seconds between reconnection attempts on USB glitch (default: 2.0s)")
    parser.add_argument("--power-mode", choices=["maximum", "balanced", "efficient", "minimum"], default=None,
                        help="Adaptive power mode (maximum, balanced, efficient, minimum)")
    parser.add_argument("--profile", choices=["work_environment", "work", "deep_work", "deep", "presentation", "present"],
                        default=None, help="Display brightness preset profile")
    parser.add_argument("--idle-timeout", type=float, default=300.0,
                        help="Idle timeout in seconds before dimming (default: 300s / 5m)")
    parser.add_argument("--dim-brightness", type=int, default=15,
                        help="Dimmed display brightness percentage (default: 15%%)")
    parser.add_argument("--auto-brightness", action="store_true", default=False,
                        help="Auto-adjust brightness based on MacBook screen brightness (15-85%% clamp, 3%% hysteresis)")
    parser.add_argument("--auto-screen-off", action="store_true", default=False,
                        help="Turn display on/off with macOS lock screen and user inactivity")
    args = parser.parse_args()

    run_carousel(
        interval=args.interval,
        brightness=args.brightness,
        single_page=args.page,
        reconnect_interval=args.reconnect_delay,
        power_mode=args.power_mode,
        profile=args.profile,
        idle_timeout=args.idle_timeout,
        dim_brightness=args.dim_brightness,
        auto_brightness=args.auto_brightness,
        auto_screen_off=args.auto_screen_off,
    )


if __name__ == "__main__":
    main()
