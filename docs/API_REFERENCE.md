# TURZX Python API Reference

The `turzx` library provides an object-oriented Python API for interacting with the TURZX USB-C secondary display.

## Installation & Import

```bash
pip install .
```

```python
from turzx import (
    TURZXDriver,
    Command,
    Orientation,
    SubRevision,
    TURZXError,
    TURZXConnectionError,
    TURZXProtocolError,
    image_to_rgb565,
    prepare_image,
)
```

---

## 1. Class: `TURZXDriver`

The main controller for device discovery, connection lifecycle, hardware configuration, and image streaming.

### Constructor
```python
driver = TURZXDriver(
    port: str = "AUTO",
    baudrate: int = 115200,
    timeout: float = 1.0,
    display_width: int = 320,
    display_height: int = 480,
    prefer_usb: bool = True,
)
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `port` | `str` | `"AUTO"` | Serial device path (e.g. `/dev/cu.usbmodem...` or `COM3`) or `"AUTO"` to auto-detect by VID:PID. |
| `baudrate` | `int` | `115200` | Baud rate for serial fallback. |
| `timeout` | `float` | `1.0` | Read/write timeout in seconds. |
| `display_width` | `int` | `320` | Native display width in pixels. |
| `display_height` | `int` | `480` | Native display height in pixels. |
| `prefer_usb` | `bool` | `True` | If `True`, attempts direct PyUSB bulk transfer (Endpoint 0x03) before falling back to serial. |

---

### Properties

| Property | Type | Description |
|---|---|---|
| `connected` | `bool` | `True` if device is actively connected. |
| `transport` | `str` | Active transport mechanism: `"usb"`, `"serial"`, or `"none"`. |
| `orientation` | `int` | Current orientation index (0=PORTRAIT, 1=LANDSCAPE, etc.). |
| `sub_revision` | `SubRevision` | Detected hardware model (`USBMONITOR_3_5`, `USBMONITOR_5`, `USBMONITOR_7`, or `TURING_3_5`). |
| `display_width` | `int` | Active display width in pixels (dynamically adjusts with orientation). |
| `display_height` | `int` | Active display height in pixels (dynamically adjusts with orientation). |
| `native_width` | `int` | Base hardware portrait width in pixels (e.g. 320). |
| `native_height` | `int` | Base hardware portrait height in pixels (e.g. 480). |

---

### Context Manager Support
`TURZXDriver` supports Python's `with` statement for automatic connection and cleanup:

```python
with TURZXDriver() as display:
    display.set_brightness(75)
    display.display_image_file("dashboard.png")
# Disconnected automatically upon exiting block
```

---

### Lifecycle Methods

#### `connect() -> bool`
Establish connection to display hardware. Automatically probes PyUSB bulk endpoint first, then serial fallback.
- **Returns**: `True` if successfully connected, `False` otherwise.

#### `disconnect() -> bool`
Safely releases USB interface and closes serial handles.
- **Returns**: `True`.

#### `initialize() -> bool`
Runs complete device initialization sequence:
1. `connect()`
2. `set_orientation(Orientation.PORTRAIT)`
3. `set_brightness(50)`
4. `clear()`
- **Returns**: `True` if entire sequence succeeds.

#### `reset() -> bool`
Sends hardware reset opcode (101) to the display controller, sleeps 2.5 seconds, and reconnects.

---

### Display Control Methods

#### `set_brightness(level: int) -> bool`
Sets the display backlight brightness.
- **Parameters**: `level` (int, `0` to `100` inclusive).
- **Raises**: `TURZXProtocolError` if not connected, `ValueError` if level not in `[0, 100]`.

#### `set_orientation(orientation: Union[Orientation, int]) -> bool`
Rotates screen orientation and dynamically swaps `display_width` and `display_height` between portrait and landscape dimensions.
- **Parameters**:
  - `0` or `Orientation.PORTRAIT`: 320 × 480 (standard portrait)
  - `1` or `Orientation.LANDSCAPE`: 480 × 320 (horizontal)
  - `2` or `Orientation.REV_PORTRAIT`: 320 × 480 (inverted 180°)
  - `3` or `Orientation.REV_LANDSCAPE`: 480 × 320 (inverted 270°)

#### `clear(color: Optional[Tuple[int, int, int]] = None) -> bool`
Clears the screen.
- **Parameters**:
  - If `None`: sends hardware `CLEAR` command (fills white).
  - If `(0, 0, 0)`: sends fast hardware `TO_BLACK` opcode (instant 6-byte packet).
  - If custom `(R, G, B)` tuple: fills the screen with the specified color.

#### `screen_off() -> bool`
Turns the display panel backlight OFF (standby mode).

#### `screen_on() -> bool`
Turns the display panel backlight ON.

---

### Image Display Methods

#### `display_image_file(image_path: str, x: int = 0, y: int = 0, width: int = 0, height: int = 0, fit_mode: str = "cover") -> bool`
Loads an image from disk, scales it to match target dimensions, and streams it to the screen.
- **Parameters**:
  - `image_path`: Path to PNG, JPEG, BMP, or WebP file.
  - `x`, `y`: Screen offset.
  - `width`, `height`: Target dimensions (`0` to use full screen resolution).
  - `fit_mode`: `"cover"` (crop to fill), `"contain"` (letterbox), or `"stretch"`.
- **Returns**: `True` on success.
- **Raises**: `FileNotFoundError` if file does not exist.

#### `display_pil_image(image: Image.Image, x: int = 0, y: int = 0, width: int = 0, height: int = 0, fit_mode: str = "cover") -> bool`
Processes a PIL `Image` object and displays it on screen.

#### `display_image(image_data: bytes, x: int = 0, y: int = 0, width: int = 0, height: int = 0) -> bool`
Low-level transmission of raw RGB565 little-endian bytes (`width * height * 2` bytes).

#### `display_color(color: Tuple[int, int, int]) -> bool`
Fills the screen with a solid RGB color (e.g. `(0, 0, 0)` for black).

---

### Diagnostic Methods

#### `status() -> Dict[str, Any]`
Returns hardware status dictionary:
```python
{
    "connected": True,
    "transport": "usb",
    "port": "USB:0x1a86:0x5722",
    "baudrate": 115200,
    "sub_revision": "USBMONITOR_3_5",
    "display_width": 320,
    "display_height": 480,
    "orientation": 0,
}
```

#### `available_ports() -> List[Dict[str, str]]`
Lists all detected serial devices on the system with VID, PID, serial number, and path.

---

## 2. Image Processing Utilities (`turzx.image_utils`)

### `image_to_rgb565(image: Image.Image) -> bytes`
Converts any PIL image (RGB, RGBA, Grayscale) into a 16-bit RGB565 little-endian byte stream.
- Automatically composites alpha transparency against black.
- Uses vectorized NumPy when available, with pure Python fallback.

### `prepare_image(image: Image.Image, target_width: int, target_height: int, fit_mode: str = "cover", background: Tuple[int, int, int] = (0, 0, 0)) -> Image.Image`
Scales and formats an image to exact screen dimensions.
- Modes:
  - `"cover"`: Scales up proportionally so the image completely fills the screen without distortion; crops excess evenly from edges.
  - `"contain"`: Scales proportionally so the entire image is visible; pads edges with `background` color.
  - `"stretch"`: Non-proportional direct resize.

### `create_test_pattern(width: int, height: int, pattern: str = "bars") -> Image.Image`
Generates calibration patterns:
- `"bars"`: SMPTE 8-color test bars (White, Yellow, Cyan, Green, Magenta, Red, Blue, Black).
- `"gradient"`: Full-spectrum horizontal RGB ramp.
- `"grid"`: Alignment crosshatch with center target circle.

---

## 3. Protocol & Codecs (`turzx.protocol`)

- `encode_command_packet(x: int, y: int, ex: int, ey: int, cmd: Union[Command, int]) -> bytes`: Returns 6-byte packed packet.
- `decode_command_packet(packet: bytes) -> Tuple[int, int, int, int, int]`: Unpacks packet into `(x, y, ex, ey, cmd)`.
- `encode_orientation_packet(orientation: Union[Orientation, int], width: int, height: int) -> bytes`: Returns 16-byte orientation packet.
- `encode_hello_packet() -> bytes`: Returns 6-byte `0x45` handshake query.
- `brightness_to_device_level(level: int) -> int`: Converts 0–100% to 0–255 hardware level.
