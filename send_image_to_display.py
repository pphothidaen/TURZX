#!/usr/bin/env python3
"""Send an image to the TURZX 3.5" / 5.0" / 7.0" IPS display hardware.

Supports native 320x480 images, and automatically scales/crops images of any
resolution or format (PNG, JPEG, BMP, WebP).
Uses high-speed PyUSB Bulk OUT endpoint (0x03) on macOS to prevent serial
corruption, with automatic fallback to pyserial.

Usage:
    python3 send_image_to_display.py [image_path] [options]

Options:
    --brightness, -b INT     Display brightness 0-100 (default: 60)
    --orientation, -o INT    Orientation 0=PORTRAIT, 1=LANDSCAPE (default: 0)
    --clear, -c              Clear screen before displaying
    --fit STR                Scaling mode: 'cover', 'contain', or 'stretch' (default: cover)
    --transport STR          Transport mode: 'auto', 'usb', or 'serial' (default: auto)

If no image_path is supplied, automatically uses the latest uploaded image in
Hermes cache or the default sample theme.
"""

import argparse
import glob
import os
import sys
import time
from typing import Optional

# Ensure workspace root is in python path
WORKSPACE_ROOT = os.path.dirname(os.path.abspath(__file__))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

from turzx import TURZXDriver  # noqa: E402


def find_latest_image() -> Optional[str]:
    """Find the latest user image in Hermes cache or project assets."""
    search_patterns = [
        "/Users/kimlenglim/.hermes/cache/documents/native-inputs/**/*.png",
        "/Users/kimlenglim/.hermes/cache/documents/native-inputs/**/*.jpg",
        os.path.join(WORKSPACE_ROOT, "35inchENG/3.5/back18.png"),
        os.path.join(WORKSPACE_ROOT, "35inchENG/3.5/back5.png"),
    ]

    all_files = []
    for pattern in search_patterns:
        for f in glob.glob(pattern, recursive=True):
            if os.path.isfile(f):
                all_files.append((os.path.getmtime(f), f))

    if all_files:
        all_files.sort(key=lambda x: x[0], reverse=True)
        return all_files[0][1]

    return None


def main():
    parser = argparse.ArgumentParser(description="Send an image to the TURZX 3.5\" IPS display")
    parser.add_argument("image_path", nargs="?", default=None, help="Path to image file (PNG/JPEG/BMP/WebP)")
    parser.add_argument("-b", "--brightness", type=int, default=60, help="Brightness level 0-100 (default: 60)")
    parser.add_argument("-o", "--orientation", type=int, default=0, choices=[0, 1, 2, 3],
                        help="0=PORTRAIT, 1=LANDSCAPE, 2=REV_PORTRAIT, 3=REV_LANDSCAPE")
    parser.add_argument("-c", "--clear", action="store_true", help="Clear screen before displaying")
    parser.add_argument("--fit", choices=["cover", "contain", "stretch"], default="cover",
                        help="Fit mode: cover, contain, or stretch (default: cover)")
    parser.add_argument("--transport", choices=["auto", "usb", "serial"], default="auto",
                        help="Transport mode (default: auto)")
    args = parser.parse_args()

    print("=" * 65)
    print("TURZX IPS DISPLAY — SHOW IMAGE")
    print("=" * 65)

    # 1. Resolve image path
    image_path = args.image_path
    if not image_path:
        image_path = find_latest_image()
        print("ℹ No image path provided. Selected latest image:")
        print(f"  -> {image_path}")

    if not image_path or not os.path.isfile(image_path):
        print(f"❌ Error: Image file not found: {image_path}")
        sys.exit(1)

    # 2. Connect driver
    prefer_usb = (args.transport != "serial")
    print(f"\n[1] Connecting to TURZX display (prefer_usb={prefer_usb})...")
    driver = TURZXDriver(prefer_usb=prefer_usb)
    if not driver.connect():
        print("❌ Error: Failed to connect to TURZX display via USB or Serial.")
        sys.exit(1)

    try:
        status = driver.status()
        print(f"    ✓ Connected! Transport: [{status['transport'].upper()}] on {status['port']}")
        print(f"    Display model: {status['sub_revision']} ({status['display_width']}x{status['display_height']})")

        # 3. Configure orientation and brightness
        print("\n[2] Configuring display:")
        driver.set_orientation(args.orientation)
        if args.orientation == 0:
            mode_str = "PORTRAIT"
        elif args.orientation == 1:
            mode_str = "LANDSCAPE"
        else:
            mode_str = str(args.orientation)
        print(f"    Orientation: {args.orientation} ({mode_str})")
        driver.set_brightness(args.brightness)
        print(f"    Brightness:  {args.brightness}%")

        if args.clear:
            print("    Clearing display...")
            driver.clear()
            time.sleep(0.2)

        # 4. Stream image
        print("\n[3] Streaming image to TURZX hardware display...")
        print(f"    File: {image_path} (fit_mode={args.fit})")

        t_start = time.time()
        success = driver.display_image_file(image_path, fit_mode=args.fit)
        elapsed = time.time() - t_start

        if success:
            payload_size = driver.display_width * driver.display_height * 2
            throughput_kb = (payload_size / elapsed) / 1024
            print("    ✅ IMAGE DISPLAYED SUCCESSFULLY!")
            print(f"    Transferred: {payload_size:,} bytes in {elapsed:.2f}s ({throughput_kb:.1f} KB/s)")
        else:
            print("    ❌ Failed to display image on hardware.")
            sys.exit(1)

        print("\n" + "=" * 65)
        print("SUCCESS: IMAGE IS NOW DISPLAYED ON TURZX MONITOR")
        print("=" * 65)

    finally:
        driver.disconnect()


if __name__ == "__main__":
    main()
