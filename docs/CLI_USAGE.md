# TURZX Command-Line Interface (CLI) Manual

The `turzx` CLI allows full control of the TURZX secondary monitor directly from your terminal or shell scripts.

## Installation

```bash
cd /path/to/TURZX
pip install .
```
Or execute directly using the Python module:
```bash
python3 -m turzx.cli <subcommand> [options]
```

---

## Command Overview

```
turzx <subcommand> [options]

Subcommands:
  send          Send an image to the display
  status        Show hardware connection and model information
  clear         Clear display screen
  brightness    Set display backlight brightness (0-100%)
  orientation   Set display orientation (0-3)
  test          Display color bars or calibration test pattern
  power         Turn screen panel power on or off
  reset         Hardware reset display controller
```

---

## 1. `turzx send` — Display an Image

Streams an image file (PNG, JPEG, BMP, WebP) to the hardware display.

```bash
turzx send [image_path] [options]
```

### Options:
- `image_path`: Path to image file. If omitted, automatically finds the latest user image or sample theme.
- `-b, --brightness INT`: Set display brightness percentage `0` to `100` (default: `60`).
- `-o, --orientation INT`: Screen orientation `0` to `3` (default: `0` / Portrait).
  - `0`: Portrait (320 × 480)
  - `1`: Landscape (480 × 320)
  - `2`: Reverse Portrait (180°)
  - `3`: Reverse Landscape (270°)
- `-c, --clear`: Clear display before rendering.
- `--fit {cover,contain,stretch}`: Scaling algorithm (default: `cover`).
  - `cover`: Fills screen completely without stretching; crops excess.
  - `contain`: Preserves full image; pads borders with black.
  - `stretch`: Stretches image to exact resolution.
- `--transport {auto,usb,serial}`: Transport mode (default: `auto`).
- `-p, --port PATH`: Serial device port path (e.g. `/dev/ttyUSB0`, `COM3`) or `AUTO` (default: `AUTO`).
- `--baudrate INT`: Serial baud rate (default: `115200`).

### Examples:
```bash
# Display wallpaper in portrait mode at 80% brightness
turzx send wallpaper.png -b 80

# Display dashboard in landscape mode (480x320)
turzx send dashboard.png -o 1 --fit contain

# Use direct USB bulk mode
turzx send image.jpg --transport usb
```

---

## 2. `turzx status` — Check Device Information

Probes the connected hardware and prints active transport, USB endpoint, model revision, and available ports.

```bash
turzx status
```

### Example Output:
```
=======================================================
TURZX DISPLAY HARDWARE STATUS
=======================================================
  Connected:       YES
  Transport:       USB
  Port:            USB:0x1a86:0x5722
  Model Revision:  USBMONITOR_3_5
  Resolution:      320x480
  Orientation:     0
  Baudrate:        115200

Available Serial Ports:
  - /dev/cu.usbmodemUSB35INCHIPSV21 (UsbMonitor) [VID:0x1a86 PID:0x5722]
=======================================================
```

---

## 3. `turzx clear` — Clear Screen

Clears the screen. Supports optional custom color fills.

```bash
# Clear to default white
turzx clear

# Clear to solid black
turzx clear --color black
# or
turzx clear --color "#000000"

# Clear to deep blue
turzx clear --color "#0a192f"
```

---

## 4. `turzx brightness` — Adjust Backlight

```bash
# Set brightness to 50%
turzx brightness 50

# Maximum brightness
turzx brightness 100

# Minimum brightness
turzx brightness 5
```

---

## 5. `turzx orientation` — Rotate Display

```bash
# Portrait (320x480)
turzx orientation 0

# Landscape (480x320)
turzx orientation 1
```

---

## 6. `turzx test` — Hardware Self-Test & Calibration

Displays built-in calibration patterns to verify pixel integrity and color reproduction.

```bash
# SMPTE 8-color test bars
turzx test --pattern bars

# Full RGB gradient ramp
turzx test --pattern gradient

# Alignment crosshairs & grid
turzx test --pattern grid
```

---

## 7. `turzx power` — Backlight Control

Turns the display panel backlight on or off (great for scheduled sleep modes).

```bash
# Turn screen off (standby)
turzx power off

# Wake screen up
turzx power on
```

---

## 8. `turzx reset` — Hardware Reset

Triggers a soft reset sequence on the display controller.

```bash
turzx reset
```

---

## Automation & Shell Script Examples

### Periodic Wallpaper Cycler:
```bash
#!/usr/bin/env bash
# Cycle through photos in ~/Pictures/turzx every 60 seconds
while true; do
  for img in ~/Pictures/turzx/*.png; do
    turzx send "$img" -b 70
    sleep 60
  done
done
```

### System Night Mode via Cron:
```cron
# Turn off display at 11:00 PM
0 23 * * * turzx power off

# Turn on display at 7:00 AM
0 7 * * * turzx power on
```
