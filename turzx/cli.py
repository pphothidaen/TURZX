"""Command Line Interface (CLI) for TURZX Display Hardware.

Provides subcommands to send images, inspect status, calibrate colors,
set brightness and orientation, and run display self-tests.
"""

import argparse
import glob
import os
import sys
import time
from typing import Optional, Tuple

from .driver import TURZXDriver
from .image_utils import create_test_pattern
from .protocol import TURZXError
from .power import BrightnessProfile, compute_clamped_brightness, get_macbook_brightness


def parse_color(val: str) -> Tuple[int, int, int]:
    """Parse color from hex string (#RRGGBB) or comma-separated R,G,B."""
    val = val.strip().lower()
    if val.startswith("#"):
        val = val[1:]
        if len(val) == 6:
            return int(val[0:2], 16), int(val[2:4], 16), int(val[4:6], 16)
        if len(val) == 3:
            return int(val[0] * 2, 16), int(val[1] * 2, 16), int(val[2] * 2, 16)
    if "," in val:
        parts = [int(p.strip()) for p in val.split(",")]
        if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
            return parts[0], parts[1], parts[2]

    # Predefined color names
    named = {
        "black": (0, 0, 0),
        "white": (255, 255, 255),
        "red": (255, 0, 0),
        "green": (0, 255, 0),
        "blue": (0, 0, 255),
        "yellow": (255, 255, 0),
        "cyan": (0, 255, 255),
        "magenta": (255, 0, 255),
        "navy": (0, 0, 128),
    }
    if val in named:
        return named[val]

    raise argparse.ArgumentTypeError(f"Invalid color specification '{val}'. Use #RRGGBB, R,G,B, or name.")


def find_default_image() -> Optional[str]:
    """Search for sample image in project assets or user caches."""
    workspace = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    patterns = [
        os.path.expanduser("~/.hermes/cache/documents/native-inputs/**/*.png"),
        os.path.expanduser("~/.hermes/cache/documents/native-inputs/**/*.jpg"),
        os.path.join(workspace, "35inchENG/3.5/back18.png"),
        os.path.join(workspace, "35inchENG/3.5/back5.png"),
    ]
    matches = []
    for pat in patterns:
        for f in glob.glob(pat, recursive=True):
            if os.path.isfile(f):
                matches.append((os.path.getmtime(f), f))
    if matches:
        matches.sort(key=lambda x: x[0], reverse=True)
        return matches[0][1]
    return None


def get_driver_from_args(args: argparse.Namespace, prefer_usb: bool = True) -> TURZXDriver:
    """Create TURZXDriver instance initialized with CLI arguments."""
    port = getattr(args, "port", "AUTO")
    baudrate = getattr(args, "baudrate", 115200)
    return TURZXDriver(port=port, baudrate=baudrate, prefer_usb=prefer_usb)


def cmd_send(args: argparse.Namespace) -> int:
    """Send an image to the display."""
    if not (0 <= args.brightness <= 100):
        print(f"❌ Error: Brightness must be in range 0-100, got {args.brightness}", file=sys.stderr)
        return 1

    image_path = args.image_path or find_default_image()
    if not image_path or not os.path.isfile(image_path):
        print(f"❌ Error: Image file not found: {image_path}", file=sys.stderr)
        return 1

    prefer_usb = (args.transport != "serial")
    driver = get_driver_from_args(args, prefer_usb=prefer_usb)
    if not driver.connect():
        print("❌ Error: Failed to connect to TURZX display via USB or Serial.", file=sys.stderr)
        return 1

    try:
        status = driver.status()
        print(f"✓ Connected [{status['transport'].upper()}] on {status['port']}")
        print(f"  Model: {status['sub_revision']} ({status['display_width']}x{status['display_height']})")

        driver.set_orientation(args.orientation)
        driver.set_brightness(args.brightness)

        if args.clear:
            driver.clear()
            time.sleep(0.15)

        print(f"Streaming image: {image_path}")
        print(f"  Mode: fit={args.fit}, orientation={args.orientation}, brightness={args.brightness}%")

        t0 = time.time()
        success = driver.display_image_file(image_path, fit_mode=args.fit)
        elapsed = time.time() - t0

        if success:
            w = int(driver.display_width)
            h = int(driver.display_height)
            kb = (w * h * 2) / (max(0.0001, elapsed) * 1024)
            print(f"✅ Success! Displayed in {elapsed:.2f}s ({kb:.1f} KB/s)")
            return 0
        else:
            print("❌ Error: Display transfer failed.", file=sys.stderr)
            return 1
    finally:
        driver.disconnect()


def cmd_status(args: argparse.Namespace) -> int:
    """Query and print display hardware status."""
    driver = get_driver_from_args(args)
    connected = driver.connect()
    try:
        status = driver.status()
        print("=" * 55)
        print("TURZX DISPLAY HARDWARE STATUS")
        print("=" * 55)
        print(f"  Connected:       {'YES' if status['connected'] else 'NO'}")
        print(f"  Transport:       {status['transport'].upper()}")
        print(f"  Port:            {status['port']}")
        print(f"  Model Revision:  {status['sub_revision']}")
        print(f"  Resolution:      {status['display_width']}x{status['display_height']}")
        print(f"  Orientation:     {status['orientation']}")
        print(f"  Baudrate:        {status['baudrate']}")

        ports = driver.available_ports()
        if ports:
            print("\nAvailable Serial Ports:")
            for p in ports:
                print(f"  - {p['device']} ({p['description']}) [VID:{p['vid']} PID:{p['pid']}]")
        print("=" * 55)
        return 0 if connected else 1
    finally:
        driver.disconnect()


def cmd_clear(args: argparse.Namespace) -> int:
    """Clear display screen."""
    driver = get_driver_from_args(args)
    if not driver.connect():
        print("❌ Error: Failed to connect to display.", file=sys.stderr)
        return 1
    try:
        color = args.color  # Already parsed by type=parse_color if passed
        driver.clear(color=color)
        color_desc = f" ({color})" if color else " (white)"
        print(f"✓ Display cleared{color_desc}")
        return 0
    finally:
        driver.disconnect()


def cmd_brightness(args: argparse.Namespace) -> int:
    """Set screen brightness level, adaptive profile, or auto-sync with MacBook screen."""
    profile_arg = getattr(args, "profile", None)
    auto_arg = getattr(args, "auto", False)
    level = getattr(args, "level", None)
    prof_desc = ""

    if auto_arg:
        host_b = get_macbook_brightness()
        if host_b is None:
            print("❌ Error: Unable to query host display brightness via native DisplayServices.", file=sys.stderr)
            return 1
        level = compute_clamped_brightness(host_b, min_clamp=15, max_clamp=85)
        prof_desc = f" [Auto: MacBook host {int(round(host_b * 100))}% -> clamped {level}%]"
    elif profile_arg:
        profile_map = {
            "work": BrightnessProfile.WORK_ENVIRONMENT,
            "work_environment": BrightnessProfile.WORK_ENVIRONMENT,
            "deep_work": BrightnessProfile.DEEP_WORK,
            "deep": BrightnessProfile.DEEP_WORK,
            "presentation": BrightnessProfile.PRESENTATION,
            "present": BrightnessProfile.PRESENTATION,
            "idle": BrightnessProfile.IDLE,
            "off": BrightnessProfile.OFF,
        }
        prof = profile_map.get(profile_arg.lower())
        if not prof:
            print(f"❌ Error: Unknown profile '{profile_arg}'. Choices: work_environment, deep_work, presentation", file=sys.stderr)
            return 1
        level = prof.default_brightness
        prof_desc = f" [Profile: {profile_arg}]"

    if level is None:
        print("❌ Error: Specify brightness percentage (0-100), --profile, or --auto", file=sys.stderr)
        return 1

    if not (0 <= level <= 100):
        print(f"❌ Error: Brightness must be in range 0-100, got {level}", file=sys.stderr)
        return 1

    driver = get_driver_from_args(args)
    if not driver.connect():
        print("❌ Error: Failed to connect to display.", file=sys.stderr)
        return 1
    try:
        driver.set_brightness(level)
        print(f"✓ Display brightness set to {level}%{prof_desc}")
        return 0
    finally:
        driver.disconnect()


def cmd_power_mode(args: argparse.Namespace) -> int:
    """Set or inspect system-wide adaptive power mode."""
    from .power import SystemPowerManager, PowerMode
    pm = SystemPowerManager.get_instance()
    mode_str = getattr(args, "mode", None)
    if not mode_str:
        telemetry = pm.get_system_telemetry()
        print("=== TURZX Adaptive Power & Resource Status ===")
        print(f"  Active Mode:       {telemetry['power_mode'].upper()} ({telemetry['capacity_percent']}%)")
        print(f"  Max Concurrency:   {telemetry['max_concurrency']} workers")
        print(f"  Memory Free:       {telemetry['memory']['free_percent']}% (Critical: {telemetry['memory']['is_critical']})")
        print(f"  Thermal State:     {telemetry['thermal']['state'].upper()} ({telemetry['thermal']['temperature']}°C)")
        print(f"  Display Profile:   {telemetry['display']['profile']} ({telemetry['display']['brightness']}%)")
        print(f"  File Descriptors:  soft={telemetry['resource_limits']['nofile_soft']}, hard={telemetry['resource_limits']['nofile_hard']}")
        return 0

    try:
        new_mode = PowerMode(mode_str.lower())
    except ValueError:
        print(f"❌ Error: Invalid power mode '{mode_str}'. Choices: maximum, balanced, efficient, minimum", file=sys.stderr)
        return 1

    driver = None
    try:
        driver = get_driver_from_args(args)
        if not driver.connect():
            driver = None
    except Exception:
        driver = None

    try:
        pm.set_power_mode(new_mode, token=getattr(args, "token", None), driver=driver)
        print(f"✓ Power mode switched to {new_mode.value.upper()} ({new_mode.capacity_percent}% capacity)")
        print(f"  Default Brightness: {new_mode.default_brightness}%")
        print(f"  Max Concurrency:    {new_mode.max_concurrency()} workers")
        return 0
    finally:
        if driver:
            try:
                driver.disconnect()
            except Exception:
                pass


def cmd_orientation(args: argparse.Namespace) -> int:
    """Set screen orientation."""
    driver = get_driver_from_args(args)
    if not driver.connect():
        print("❌ Error: Failed to connect to display.", file=sys.stderr)
        return 1
    try:
        driver.set_orientation(args.mode)
        mode_names = {0: "PORTRAIT", 1: "LANDSCAPE", 2: "REV_PORTRAIT", 3: "REV_LANDSCAPE"}
        print(f"✓ Orientation set to {args.mode} ({mode_names.get(args.mode, 'CUSTOM')})")
        return 0
    finally:
        driver.disconnect()


def cmd_test(args: argparse.Namespace) -> int:
    """Run display self-test with diagnostic test patterns."""
    driver = get_driver_from_args(args)
    if not driver.connect():
        print("❌ Error: Failed to connect to display.", file=sys.stderr)
        return 1
    try:
        driver.set_orientation(0)
        driver.set_brightness(75)

        pattern = args.pattern
        print(f"Running display test pattern: '{pattern}' ({driver.display_width}x{driver.display_height})...")
        img = create_test_pattern(driver.display_width, driver.display_height, pattern=pattern)

        t0 = time.time()
        success = driver.display_pil_image(img)
        elapsed = time.time() - t0

        if success:
            print(f"✅ Self-test pattern rendered successfully in {elapsed:.2f}s!")
            return 0
        else:
            print("❌ Self-test pattern failed to stream to display.", file=sys.stderr)
            return 1
    finally:
        driver.disconnect()


def cmd_power(args: argparse.Namespace) -> int:
    """Turn display panel on or off."""
    driver = get_driver_from_args(args)
    if not driver.connect():
        print("❌ Error: Failed to connect to display.", file=sys.stderr)
        return 1
    try:
        if args.state == "on":
            driver.screen_on()
            print("✓ Screen powered ON")
        else:
            driver.screen_off()
            print("✓ Screen powered OFF")
        return 0
    finally:
        driver.disconnect()


def cmd_reset(args: argparse.Namespace) -> int:
    """Reset the display hardware."""
    driver = get_driver_from_args(args)
    if not driver.connect():
        print("❌ Error: Failed to connect to display.", file=sys.stderr)
        return 1
    try:
        driver.reset()
        print("✓ Display reset complete")
        return 0
    finally:
        driver.disconnect()


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("-p", "--port", default="AUTO", help="Serial device port path or 'AUTO'")
    common_parser.add_argument("--baudrate", type=int, default=115200, help="Serial baud rate (default: 115200)")

    parser = argparse.ArgumentParser(
        prog="turzx",
        description="TURZX 3.5\" / 5.0\" / 7.0\" IPS USB-C Display Management CLI",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Command to execute")

    # send
    p_send = subparsers.add_parser("send", parents=[common_parser], help="Send an image to the display")
    p_send.add_argument("image_path", nargs="?", default=None, help="Path to image file (PNG/JPEG/BMP/WebP)")
    p_send.add_argument("-b", "--brightness", type=int, default=60, help="Brightness level 0-100 (default: 60)")
    p_send.add_argument("-o", "--orientation", type=int, default=0, choices=[0, 1, 2, 3],
                        help="0=PORTRAIT, 1=LANDSCAPE, 2=REV_PORTRAIT, 3=REV_LANDSCAPE")
    p_send.add_argument("-c", "--clear", action="store_true", help="Clear screen before displaying")
    p_send.add_argument("--fit", choices=["cover", "contain", "stretch"], default="cover",
                        help="Image fit mode (default: cover)")
    p_send.add_argument("--transport", choices=["auto", "usb", "serial"], default="auto",
                        help="Transport mode (default: auto)")

    # status
    subparsers.add_parser("status", parents=[common_parser], help="Show display hardware connection status")

    # clear
    p_clear = subparsers.add_parser("clear", parents=[common_parser], help="Clear the display")
    p_clear.add_argument("--color", type=parse_color, default=None,
                         help="Color to clear to (e.g. #000000, black, red, 255,0,0)")

    # brightness
    p_bright = subparsers.add_parser("brightness", parents=[common_parser], help="Set display brightness (0-100) or profile")
    p_bright.add_argument("level", type=int, nargs="?", default=None, help="Brightness percentage (0-100)")
    p_bright.add_argument("--profile", choices=["work_environment", "work", "deep_work", "deep", "presentation", "present"],
                          help="Preset brightness profile")
    p_bright.add_argument("--auto", action="store_true", default=False,
                          help="Auto-adjust brightness based on MacBook screen brightness (15-85%% clamp)")

    # orientation
    p_orient = subparsers.add_parser("orientation", parents=[common_parser], help="Set display orientation (0-3)")
    p_orient.add_argument("mode", type=int, choices=[0, 1, 2, 3],
                          help="0=PORTRAIT, 1=LANDSCAPE, 2=REV_PORT, 3=REV_LAND")

    # test
    p_test = subparsers.add_parser("test", parents=[common_parser], help="Render diagnostic calibration pattern")
    p_test.add_argument("--pattern", choices=["bars", "gradient", "grid"], default="bars",
                        help="Pattern type (default: bars)")

    # power
    p_power = subparsers.add_parser("power", parents=[common_parser], help="Control display panel power state")
    p_power.add_argument("state", choices=["on", "off"], help="'on' or 'off'")

    # power-mode
    p_power_mode = subparsers.add_parser("power-mode", parents=[common_parser], help="Configure or view adaptive power mode")
    p_power_mode.add_argument("mode", nargs="?", choices=["maximum", "balanced", "efficient", "minimum"],
                              help="Target power mode (default: inspect status)")
    p_power_mode.add_argument("--token", help="Local process token for authentication")

    # resource-status
    subparsers.add_parser("resource-status", parents=[common_parser], help="Inspect system power & resource telemetry")

    # reset
    subparsers.add_parser("reset", parents=[common_parser], help="Hardware reset display controller")

    return parser


def main(argv=None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    dispatch = {
        "send": cmd_send,
        "status": cmd_status,
        "clear": cmd_clear,
        "brightness": cmd_brightness,
        "orientation": cmd_orientation,
        "test": cmd_test,
        "power": cmd_power,
        "power-mode": cmd_power_mode,
        "resource-status": cmd_power_mode,
        "reset": cmd_reset,
    }

    handler = dispatch.get(args.subcommand)
    if handler:
        try:
            return handler(args)
        except (TURZXError, ValueError, FileNotFoundError, OSError) as err:
            print(f"❌ Error: {err}", file=sys.stderr)
            return 1
        except Exception as err:
            print(f"❌ Unexpected error: {err}", file=sys.stderr)
            return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
