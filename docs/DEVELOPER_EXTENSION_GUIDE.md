# TURZX Developer Extension & Integration Guide

This guide is designed for developers building custom monitoring dashboards, hardware integrations, background daemons, and expanding support for additional screen resolutions.

---

## 1. Building Dynamic Dashboards with Pillow

Because `turzx` accepts any standard PIL `Image` object, you can compose rich user interfaces with text, progress bars, shapes, and charts using `Pillow`.

### Complete Dashboard Example:
```python
import psutil
import datetime
from PIL import Image, ImageDraw, ImageFont
from turzx import TURZXDriver

def create_system_dashboard(width: int = 320, height: int = 480) -> Image.Image:
    # 1. Background
    img = Image.new("RGB", (width, height), (15, 23, 42))  # Dark slate background
    draw = ImageDraw.Draw(img)

    # 2. Header
    draw.rectangle([0, 0, width, 50], fill=(30, 41, 59))
    now = datetime.datetime.now().strftime("%H:%M:%S")
    draw.text((15, 15), "SYSTEM MONITOR", fill=(56, 189, 248))
    draw.text((width - 80, 15), now, fill=(148, 163, 184))

    # 3. CPU Usage Metric
    cpu_pct = psutil.cpu_percent()
    draw.text((20, 70), f"CPU Usage: {cpu_pct:.1f}%", fill=(255, 255, 255))
    # Progress bar outline
    draw.rectangle([20, 95, width - 20, 115], outline=(71, 85, 105), width=2)
    # Fill bar
    bar_width = int((width - 44) * (cpu_pct / 100.0))
    bar_color = (34, 197, 94) if cpu_pct < 70 else (239, 68, 68)
    if bar_width > 0:
        draw.rectangle([22, 97, 22 + bar_width, 113], fill=bar_color)

    # 4. RAM Usage Metric
    mem = psutil.virtual_memory()
    draw.text((20, 140), f"Memory: {mem.percent:.1f}% ({mem.used // (1024**2)} MB)", fill=(255, 255, 255))
    draw.rectangle([20, 165, width - 20, 185], outline=(71, 85, 105), width=2)
    mem_bar = int((width - 44) * (mem.percent / 100.0))
    if mem_bar > 0:
        draw.rectangle([22, 167, 22 + mem_bar, 183], fill=(59, 130, 246))

    # 5. Disk / Network info
    draw.line([(20, 210), (width - 20, 210)], fill=(51, 65, 85), width=1)
    draw.text((20, 230), f"Processes: {len(psutil.pids())}", fill=(203, 213, 225))
    draw.text((20, 260), f"Boot: {datetime.datetime.fromtimestamp(psutil.boot_time()).strftime('%Y-%m-%d')}", fill=(148, 163, 184))

    return img

# Stream to hardware
with TURZXDriver() as display:
    img = create_system_dashboard()
    display.display_pil_image(img)
```

---

## 2. High-Performance Partial Updates

When only a small portion of the screen changes (for instance, a digital clock or CPU percentage), transmitting the entire 307,200 bytes is unnecessary. The UsbMonitor protocol natively supports arbitrary bounding box coordinates `(x, y)` to `(ex, ey)`.

### Partial Update Code Snippet:
```python
from turzx import TURZXDriver, image_to_rgb565
from PIL import Image, ImageDraw

with TURZXDriver() as display:
    # Initial full screen render
    full_bg = Image.new("RGB", (320, 480), (0, 0, 0))
    display.display_pil_image(full_bg)

    # Now update only a 120x40 pixel clock box at position (100, 20)
    box_w, box_h = 120, 40
    pos_x, pos_y = 100, 20

    clock_tile = Image.new("RGB", (box_w, box_h), (30, 30, 30))
    draw = ImageDraw.Draw(clock_tile)
    draw.text((10, 10), "12:45:00", fill=(0, 255, 200))

    # Convert only the 120x40 tile (9,600 bytes instead of 307,200 bytes!)
    tile_rgb565 = image_to_rgb565(clock_tile)

    # Transmit with explicit x, y, width, height
    display.display_image(tile_rgb565, x=pos_x, y=pos_y, width=box_w, height=box_h)
```
**Benefits:**
- Reduces transmission size from **307.2 KB down to 9.6 KB** (32× reduction!).
- Latency drops from ~1.8 s to under **0.06 s**, allowing up to 15–20 FPS for targeted gauges.

---

## 3. Background Render Loop Architecture

For integration with services (such as FastAPI, daemon workers, or Home Assistant):

```python
import threading
import time
from turzx import TURZXDriver

class DisplayDaemon:
    def __init__(self, interval: float = 5.0):
        self.interval = interval
        self.driver = TURZXDriver()
        self._running = False
        self._thread = None

    def start(self):
        if not self.driver.connect():
            raise RuntimeError("Failed to connect to display hardware")
        self.driver.initialize()
        self._running = True
        self._thread = threading.Thread(target=self._render_loop, daemon=True)
        self._thread.start()

    def _render_loop(self):
        while self._running:
            try:
                # Render your image here
                img = self.render_frame()
                self.driver.display_pil_image(img)
            except Exception as e:
                print(f"Render error: {e}")
            time.sleep(self.interval)

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        self.driver.disconnect()
```

---

## 4. Supporting Larger Hardware Models (5.0" and 7.0")

The driver automatically detects the hardware revision via the `HELLO` handshake:

| Model | SubRevision | Resolution | Framebuffer Bytes |
|---|---|---|---|
| **3.5" IPS** | `USBMONITOR_3_5` | 320 × 480 | 307,200 bytes |
| **5.0" IPS** | `USBMONITOR_5` | 480 × 800 | 768,000 bytes |
| **7.0" IPS** | `USBMONITOR_7` | 600 × 1024 | 1,228,800 bytes |

To query the detected native resolution programmatically:
```python
with TURZXDriver() as display:
    w = display.display_width
    h = display.display_height
    print(f"Connected to {display.sub_revision.name} ({w}x{h})")

    # Render image using detected width & height
    canvas = Image.new("RGB", (w, h), (10, 10, 10))
    display.display_pil_image(canvas)
```

---

## 5. Platform Permissions & Driver Setup

### macOS Setup
macOS includes native USB support. Install `libusb` via Homebrew for optimal PyUSB performance:
```bash
brew install libusb
pip install pyusb pyserial pillow numpy
```

### Linux Setup (Ubuntu / Debian / Raspberry Pi)
Create a `udev` rule to grant non-root users permission to access the USB bulk endpoint:
```bash
sudo tee /etc/udev/rules.d/99-turzx.rules << 'EOF'
# TURZX / Turing UsbMonitor Display
SUBSYSTEM=="usb", ATTR{idVendor}=="1a86", ATTR{idProduct}=="5722", MODE="0666", GROUP="plugdev"
KERNEL=="ttyACM[0-9]*", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="5722", MODE="0666", GROUP="dialout"
EOF

sudo udevadm control --reload-rules
sudo udevadm trigger
sudo usermod -aG plugdev,dialout $USER
```

### Windows Setup
On Windows, the display can be driven using either:
1. **PySerial CDC ACM**: Device appears under `Device Manager` as `Ports (COM & LPT) -> USB-SERIAL CH340` or `UsbMonitor`. Ensure baudrate `115200`.
2. **PyUSB / libusb**: Use [Zadig](https://zadig.akeo.ie/) to replace the driver for Interface 1 with **WinUSB**.
