# TURZX / Turing Smart Screen USB Display Driver & Toolkit

[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-42%20passed-brightgreen.svg)]()

A clean, standalone, high-performance Python driver, CLI, and developer toolkit for the **TURZX / Turing Smart Screen 3.5", 5.0", and 7.0" USB-C secondary displays** (UsbMonitor Rev A protocol).

---

## Key Highlights

- ⚡ **High-Speed PyUSB Bulk Transfer**: Transmits directly to USB Bulk OUT Endpoint `0x03` on Interface 1, completely bypassing macOS CDC-ACM serial fragmentation and buffer starvation bugs. Transmit speeds reach **~160 KB/s** (~1.8 s per full 320×480 frame).
- 🔄 **Dual Transport with Auto-Fallback**: Automatically uses direct PyUSB on macOS/Linux, with transparent fallback to PySerial virtual COM port (`/dev/cu.usbmodem...` / `COMx`).
- 🖥️ **Multi-Model Support**: Auto-detects 3.5" (320×480), 5.0" (480×800), and 7.0" (600×1024) models via the hardware `HELLO` handshake.
- 📐 **Smart Aspect Ratio Scaling**: Supports `cover` (fill & crop), `contain` (letterbox with background color), and `stretch` modes for arbitrary images (PNG, JPEG, BMP, WebP).
- 🛠️ **Unified CLI Tool (`turzx`)**: Command-line control to upload images, inspect hardware status, clear the screen, set brightness, rotate orientation, or run display self-test patterns.
- 🧩 **Complete Developer Documentation**: Comprehensive architectural reverse engineering notes, binary wire protocol layouts, API references, and extension guides for building custom live widgets.

---

## Hardware Specifications

| Specification | Details |
|---|---|
| **Supported Devices** | TURZX 3.5" IPS, 5.0" IPS, 7.0" IPS, Turing Smart Screen |
| **Vendor ID (VID)** | `0x1A86` (QinHeng Electronics / WCH) |
| **Product ID (PID)** | `0x5722` |
| **USB Descriptors** | `USB35INCHIPSV2` / `UsbMonitor` |
| **Default Resolution** | 320 × 480 (3.5" native portrait) |
| **Color Format** | 16-bit RGB565 little-endian (`width * height * 2` bytes) |
| **USB Interface / Endpoints**| Interface 1, Bulk OUT `0x03`, Bulk IN `0x82` |

---

## Quick Start

### 1. Installation

```bash
cd TURZX
pip install .
```
*(Dependencies: `pyusb`, `pyserial`, `pillow`, `numpy`)*

### 2. Python API Usage

```python
from turzx import TURZXDriver

# Automatically connect, set brightness, and upload wallpaper
with TURZXDriver() as display:
    display.set_orientation(0)        # 0=Portrait, 1=Landscape
    display.set_brightness(75)        # 0-100%
    display.display_image_file("my_wallpaper.png", fit_mode="cover")
```

### 3. Command Line Interface (CLI)

```bash
# Upload and display an image
turzx send photo.png -b 80

# Check hardware connection status and detected model
turzx status

# Clear the screen to black
turzx clear --color black

# Set brightness to 50%
turzx brightness 50

# Run display calibration color bars self-test
turzx test --pattern bars
```

---

## Documentation

Detailed user guides and developer documentation are available:

- 📘 **[Complete User Guide (คู่มือการใช้งานฉบับสมบูรณ์)](USER_GUIDE.md)**
  - Comprehensive step-by-step user manual in Thai, covering CLI commands, Python API, real-world examples, and troubleshooting.

Developer specifications and deep-dive technical documents in the [`docs/`](docs/) directory:

1. **[Architecture & Binary Protocol Specification](docs/ARCHITECTURE_AND_PROTOCOL.md)**
   - Analysis of macOS `AppleUSBACM` serial bug and the PyUSB Bulk OUT endpoint solution.
   - 6-byte Command Packet layout and 10-bit coordinate bit-packing math.
   - 16-byte Orientation Packet structure.
   - `HELLO` handshake and model identification table.
   - RGB565 little-endian bit conversion and streaming chunk format.

2. **[Python API Reference](docs/API_REFERENCE.md)**
   - Complete documentation of `TURZXDriver` methods and properties.
   - Image utilities (`image_to_rgb565`, `prepare_image`, `create_test_pattern`).
   - Exceptions (`TURZXError`, `TURZXConnectionError`, `TURZXProtocolError`).

3. **[CLI Manual & Shell Guide](docs/CLI_USAGE.md)**
   - Detailed breakdown of subcommands (`send`, `status`, `clear`, `brightness`, `orientation`, `test`, `power`, `reset`).
   - Shell scripting examples (cron job, automated background wallpaper rotation).

4. **[Developer Extension Guide](docs/DEVELOPER_EXTENSION_GUIDE.md)**
   - Step-by-step guide to building custom monitoring dashboards with Pillow.
   - Partial screen update optimization (32× bandwidth reduction for live gauges).
   - Daemon background render loops.
   - Multi-platform setup (macOS `libusb`, Linux `udev` rules, Windows WinUSB).

---

## Running the Test Suite

The project includes unit and integration tests covering protocol packet codecs, bit-level boundary coordinates, RGB565 encoding, image scaling algorithms, CLI commands, and mock hardware drivers:

```bash
python3 -m unittest discover tests -v
# or
pytest
```

All 42 unit tests run in <0.5 s with zero failures.

---

## Project Structure

```
TURZX/
├── turzx/                         # Core standalone driver package
│   ├── __init__.py                # Package exports & version
│   ├── protocol.py                # Binary packet codecs & hardware constants
│   ├── driver.py                  # TURZXDriver (PyUSB + PySerial fallback)
│   ├── image_utils.py             # RGB565 conversion & image scaling
│   └── cli.py                     # Unified CLI tool
├── docs/                          # Comprehensive documentation
│   ├── ARCHITECTURE_AND_PROTOCOL.md
│   ├── API_REFERENCE.md
│   ├── CLI_USAGE.md
│   └── DEVELOPER_EXTENSION_GUIDE.md
├── tests/                         # Test suite
│   ├── test_protocol.py           # Codec & bit-packing unit tests
│   ├── test_image_utils.py        # RGB565 & image transformation tests
│   ├── test_driver.py             # Driver lifecycle & mock transport tests
│   └── test_cli.py                # CLI commands & argument parsing tests
├── pyproject.toml                 # Modern Python packaging configuration
├── setup.py                       # Standard setuptools configuration
├── serial_driver.py               # Backward-compatibility shim
├── send_image_to_display.py       # Standalone image upload script
├── backend/                       # DeepSeek Web Bridge backend
│   └── services/
│       └── serial_driver.py       # Backend compatibility shim
├── LICENSE                        # MIT License
├── USER_GUIDE.md                  # Comprehensive user manual
└── README.md                      # Developer entry point
```

---

## License

MIT License. See LICENSE file for details.
