# TURZX Display Architecture & Binary Protocol Specification

## 1. Executive Summary & Hardware Overview

The TURZX secondary display (also marketed as Turing Smart Screen, UsbMonitor, or 3.5" IPS USB-C Secondary Screen) is a compact, high-resolution LCD monitor designed for system monitoring, status dashboards, and secondary PC displays.

### Hardware Specifications
| Parameter | Specification |
|---|---|
| **Display Panel** | 3.5" IPS LCD (320 × 480 pixels native portrait) |
| **Color Depth** | 16-bit RGB565 (65,536 colors, little-endian) |
| **USB Controller** | QinHeng Electronics (WCH) USB Controller / CH340 family |
| **USB Vendor ID (VID)** | `0x1A86` |
| **USB Product ID (PID)** | `0x5722` |
| **USB Serial Descriptor** | `USB35INCHIPSV2` |
| **USB Bulk Interface** | Interface `1` |
| **USB Endpoints** | Bulk OUT `0x03`, Bulk IN `0x82` |
| **Serial CDC Device** | `/dev/cu.usbmodemUSB35INCHIPSV21` (macOS), `/dev/ttyACM0` (Linux), `COMx` (Windows) |
| **Default Baud Rate** | 115200 baud (8-N-1) |
| **Family Revisions** | 3.5" (320×480), 5.0" (480×800), 7.0" (600×1024) |

---

## 2. macOS Serial Issue vs. Direct USB Bulk Transfer

### The Problem with CDC-ACM on macOS
When communicating via standard virtual COM port (`/dev/cu.usbmodem...`) on macOS:
1. The kernel driver `AppleUSBACM` fragments large binary streams into non-deterministic chunk boundaries.
2. Fast continuous writes over 115200 baud cause buffer starvation and dropped bytes in the CDC-ACM ring buffer.
3. The display firmware expects pixel streams to arrive immediately after a `DISPLAY_BITMAP` command without intermediate header bytes. Any dropped byte shifts the RGB565 byte phase, causing visual tearing or displaying a static blue screen.

### The Solution: Direct PyUSB Bulk OUT Transfer
The hardware exposes two interfaces:
- **Interface 0**: CDC Communications interface (virtual serial modem)
- **Interface 1**: CDC Data / Bulk Streaming interface:
  - Endpoint `0x03` (Bulk OUT, max packet size 64 bytes)
  - Endpoint `0x82` (Bulk IN, max packet size 64 bytes)

By claiming **Interface 1** and writing raw packets directly to **Endpoint `0x03`** using `pyusb` (backed by `libusb`), communication completely bypasses the macOS CDC serial driver. This achieves:
- **Zero byte dropping or stream fragmentation**.
- **160 KB/s transfer throughput** (full 320×480 frame transferred in ~1.8 s).
- High reliability across macOS versions (macOS Sonoma, Sequoia, and Apple Silicon M-series).
- Automatic fallback to PySerial if direct USB is unavailable.

---

## 3. Binary Wire Protocol Specification

The device uses the **UsbMonitor Rev A** binary protocol. All communication consists of command packets followed optionally by raw pixel data. The device does not send acknowledgment packets for standard commands.

### 3.1. 6-Byte Control Command Packet Layout

All standard commands (clear, reset, brightness, screen power, bitmap display) use a fixed 6-byte packet:

```
+------------+------------+------------+------------+------------+------------+
|   Byte 0   |   Byte 1   |   Byte 2   |   Byte 3   |   Byte 4   |   Byte 5   |
+------------+------------+------------+------------+------------+------------+
|  x[9:2]    | x[1:0]     | y[3:0]     | ex[5:0]    |  ey[7:0]   |  Command   |
|            | y[9:4]     | ex[9:6]    | ey[9:8]    |            |   Opcode   |
+------------+------------+------------+------------+------------+------------+
```

#### Coordinate Bit-Packing (10-bit integers, range 0–1023):
The coordinates represent a rectangular bounding box `(x, y)` to `(ex, ey)`:
- $x$: Starting column (0–1023)
- $y$: Starting row (0–1023)
- $ex$: Ending column inclusive (e.g. $x + width - 1$)
- $ey$: Ending row inclusive (e.g. $y + height - 1$)

#### Encoding Math:
```python
buf[0] = (x >> 2) & 0xFF
buf[1] = (((x & 3) << 6) | (y >> 4)) & 0xFF
buf[2] = (((y & 15) << 4) | (ex >> 6)) & 0xFF
buf[3] = (((ex & 63) << 2) | (ey >> 8)) & 0xFF
buf[4] = ey & 0xFF
buf[5] = cmd & 0xFF
```

#### Decoding Math:
```python
x  = (buf[0] << 2) | (buf[1] >> 6)
y  = ((buf[1] & 0x3F) << 4) | (buf[2] >> 4)
ex = ((buf[2] & 0x0F) << 6) | (buf[3] >> 2)
ey = ((buf[3] & 0x03) << 8) | buf[4]
cmd = buf[5]
```

### 3.2. Command Opcodes

| Opcode (Decimal) | Hex | Name | Payload / Description |
|---|---|---|---|
| **69** | `0x45` | `HELLO` | 6-byte handshake; asks display controller for model revision |
| **101** | `0x65` | `RESET` | Hardware reset controller ($x=y=ex=ey=0$) |
| **102** | `0x66` | `CLEAR` | Clears entire display to white ($x=y=ex=ey=0$) |
| **103** | `0x67` | `TO_BLACK` | Fills screen with black ($x=y=ex=ey=0$) |
| **108** | `0x6C` | `SCREEN_OFF` | Shuts down display backlight panel ($x=y=ex=ey=0$) |
| **109** | `0x6D` | `SCREEN_ON` | Powers display backlight panel back on ($x=y=ex=ey=0$) |
| **110** | `0x6E` | `SET_BRIGHTNESS` | Sets backlight level: $x = \text{device\_level}$, $y=ex=ey=0$ |
| **121** | `0x79` | `SET_ORIENTATION` | Configures orientation & dimensions (16-byte packet) |
| **122** | `0x7A` | `SET_MIRROR` | Enables/disables rendering mirror ($x=1 \text{ or } 0$) |
| **195** | `0xC3` | `DISPLAY_PIXELS` | Non-contiguous sparse pixel block update |
| **197** | `0xC5` | `DISPLAY_BITMAP` | Initiates continuous RGB565 bitmap stream to rectangle |

---

### 3.3. 16-Byte Orientation Command Packet Layout

Orientation changes require a 16-byte control packet that specifies both the rotation index and the resolution dimensions:

```
Byte 0..4:   0x00 0x00 0x00 0x00 0x00  (Position fields unused)
Byte 5:      0x79                      (Command.SET_ORIENTATION = 121)
Byte 6:      orientation + 100         (100=PORTRAIT, 101=LANDSCAPE, etc.)
Byte 7..8:   width >> 8, width & 0xFF  (16-bit big-endian width)
Byte 9..10:  height >> 8, height & 0xFF(16-bit big-endian height)
Byte 11..15: 0x00 0x00 0x00 0x00 0x00  (Padding zeros)
```

#### Orientation Constants:
| Value | Mode | Dimensions (3.5") | Description |
|---|---|---|---|
| **0** | `PORTRAIT` | 320 × 480 | USB-C port at bottom (standard) |
| **1** | `LANDSCAPE` | 480 × 320 | USB-C port at right |
| **2** | `REV_PORTRAIT` | 320 × 480 | Inverted vertical (180°) |
| **3** | `REV_LANDSCAPE` | 480 × 320 | Inverted horizontal (270°) |

---

### 3.4. HELLO Handshake & Sub-Revision Detection

To detect which hardware screen model is attached:
1. Host sends 6 bytes: `b"\x45\x45\x45\x45\x45\x45"` (`[Command.HELLO] * 6`).
2. Host reads 6 bytes from USB Endpoint `0x82` (or serial port):
   - `0x01 0x01 0x01 0x01 0x01 0x01` $\rightarrow$ **UsbMonitor 3.5" IPS (320 × 480)**
   - `0x02 0x02 0x02 0x02 0x02 0x02` $\rightarrow$ **UsbMonitor 5.0" IPS (480 × 800)**
   - `0x03 0x03 0x03 0x03 0x03 0x03` $\rightarrow$ **UsbMonitor 7.0" IPS (600 × 1024)**
   - No response / timeout $\rightarrow$ Host inspects the USB device descriptor / serial string (e.g. `USB35INCHIPSV2` $\rightarrow$ **UsbMonitor 3.5" IPS**, `USB50INCH` $\rightarrow$ **5.0"**, `USB70INCH` $\rightarrow$ **7.0"**), defaulting to **Turing 3.5" (320 × 480)** if no match is found.

---

### 3.5. Brightness Conversion Formula

The display controller hardware uses an **inverted scale**:
- Device Level `0` = Brightest (100% backlight)
- Device Level `255` = Darkest (0% backlight)

To convert user percentage $P \in [0, 100]$ to hardware level $D$:
$$D = \text{round}\left(255 - \frac{P}{100.0} \times 255\right)$$

The packet is then encoded with $x = D, y = 0, ex = 0, ey = 0, cmd = 110$:
```python
encode_command_packet(device_level, 0, 0, 0, Command.SET_BRIGHTNESS)
```

---

### 3.6. RGB565 Little-Endian Image Streaming

Colors are represented as 16-bit high-color words:
```
Bit 15 14 13 12 11 | 10 9  8  7  6  5 | 4  3  2  1  0
<----- Red -----> | <---- Green ----> | <--- Blue --->
     5 bits                 6 bits            5 bits
```

Conversion from 8-bit RGB `(R, G, B)`:
$$R_{5} = R \gg 3, \quad G_{6} = G \gg 2, \quad B_{5} = B \gg 3$$
$$\text{word}_{16} = (R_{5} \ll 11) \mid (G_{6} \ll 5) \mid B_{5}$$

Because the display expects **little-endian** byte ordering:
$$\text{Byte}_{0} = \text{word} \ \& \ 0\text{xFF} = (G_{6}[2:0] \ll 5) \mid B_{5}$$
$$\text{Byte}_{1} = (\text{word} \gg 8) \ \& \ 0\text{xFF} = (R_{5} \ll 3) \mid (G_{6}[5:3])$$

#### Example Color Encodings:
| Color | RGB888 | 16-bit Hex | Little-Endian Bytes |
|---|---|---|---|
| **Black** | `(0, 0, 0)` | `0x0000` | `b"\x00\x00"` |
| **White** | `(255, 255, 255)` | `0xFFFF` | `b"\xFF\xFF"` |
| **Pure Red** | `(255, 0, 0)` | `0xF800` | `b"\x00\xF8"` |
| **Pure Green** | `(0, 255, 0)` | `0x07E0` | `b"\xE0\x07"` |
| **Pure Blue** | `(0, 0, 255)` | `0x001F` | `b"\x1F\x00"` |

#### Streaming Protocol:
1. Send `DISPLAY_BITMAP` command:
   ```python
   encode_command_packet(x, y, x + width - 1, y + height - 1, Command.DISPLAY_BITMAP)
   ```
2. Immediately stream raw RGB565 byte buffer:
   - Total payload for 320 × 480 is: $320 \times 480 \times 2 = 307,200 \text{ bytes}$.
   - For USB Bulk: stream in **4096-byte aligned chunks** to Endpoint `0x03`.
   - For Serial: stream in **$width \times 8$ byte chunks** (2,560 bytes) and flush.

---

## 4. Multi-Agent System Architecture & Governance

The TURZX platform features a layered autonomous agent framework designed to safely decouple hardware control, background scheduling, external AI agent status synchronization, and test validation.

```
                      +---------------------------------------+
                      |         Human Operator / CLI          |
                      +---------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                            TURZX Multi-Agent Layer                                |
|                                                                                   |
|   +--------------------------+               +--------------------------------+   |
|   |    turzx-core-agent      |<=============>|     turzx-scheduler-agent      |   |
|   |  - Display pipeline      |  Local IPC    |  - Cron & Interval tasks       |   |
|   |  - Framebuffer & RGB565  |  Tokens       |  - Health & Quota monitoring   |   |
|   |  - CLI Dispatcher        |               |  - Execution lifecycle         |   |
|   +--------------------------+               +--------------------------------+   |
|                 |                                             |                   |
|                 v                                             v                   |
|   +--------------------------+               +--------------------------------+   |
|   |   turzx-hardware-agent   |               |   turzx-integration-agent      |   |
|   |  - USB Bulk (0x1A86:5722)|               |  - Session Bridge              |   |
|   |  - CDC Serial (/dev/cu*) |               |  - Agent Quotas                |   |
|   |  - Failover & Recovery   |               |  - Redis & External Agents     |   |
|   +--------------------------+               +--------------------------------+   |
|                                                                                   |
|                        +---------------------------+                              |
|                        |     turzx-test-agent      |                              |
|                        |  - Mock USB & Serial      |                              |
|                        |  - CI/CD Test Harness     |                              |
|                        +---------------------------+                              |
+-----------------------------------------------------------------------------------+
                                          |
                                          v
+-----------------------------------------------------------------------------------+
|                               Physical Hardware                                   |
|                CH340/CH341 USB-to-Serial & AX206/LCM Display Buffer               |
+-----------------------------------------------------------------------------------+
```

### The Five Governance Golden Rules

1. **Zero Assumption Compliance**: No agent assumes hardware connection without active USB descriptor (`0x1A86:0x5722`) or CDC serial port interrogation.
2. **Fail-Closed Enforcement**: Any protocol error, corrupt buffer, or unrecognized token immediately aborts execution safely without flooding endpoints or leaving displays in undefined states.
3. **Semantic Signature Verification**: All command messages and inter-agent signals must match declared JSON schemas in `.agents/ai/`.
4. **Resource Boundary Enforcement**: Hard caps are enforced: 30 FPS display rate, 768 KB max frame buffers, 10 concurrent jobs, and 300-second job timeouts.
5. **Lightweight Local Process Token Authentication**: For local macOS intra-process traffic, communication is secured via shared process tokens (`TURXZ_PROCESS_TOKEN`) with `0600` POSIX file security and constant-time HMAC validation.

---

## 5. Scheduler Architecture & Task Lifecycle

The `turxz_scheduled_jobs` engine coordinates periodic and event-driven routines:

```
[Start Scheduler Daemon]
         │
         ├──> [Execute Startup Jobs (ScheduleType.STARTUP)]
         │
         └──> [Scheduler Main Loop (Tick Interval = 1.0s)]
                   │
                   ├──> [Check Trigger Conditions: Interval / Cron / Once]
                   │          │
                   │          v (Due)
                   │    [TURXZJobExecutor]
                   │          ├── Thread Pool Worker (default)
                   │          ├── Async Event Loop Worker
                   │          └── Synchronous Worker
                   │
                   ├──> [HealthMonitor Sample (Memory, CPU, Status)]
                   │
                   └──> [Prune Ring Buffer History (Max 1000 items)]
```
