"""TURZX USB/Serial Protocol Definitions and Packet Codecs.

Implements the low-level binary protocol for the Turing Smart Screen / TURZX
USB-C secondary monitor series (UsbMonitor Rev A protocol).

Protocol Summary:
- Control commands: 6-byte fixed-size binary packet encoding 10-bit coordinates
  (x, y, ex, ey) and 1-byte command opcode.
- Orientation command: 16-byte fixed-size binary packet.
- Handshake: 6-byte HELLO command (opcode 69) and 6-byte model identification response.
- Pixel stream: Raw RGB565 little-endian stream (16 bits per pixel) transmitted
  directly following DISPLAY_BITMAP command.
"""

from enum import IntEnum
from typing import Optional, Tuple, Union


# =============================================================================
# USB & Hardware Identifiers
# =============================================================================

# QinHeng Electronics / Turing UsbMonitor hardware IDs
TURZX_VID: int = 0x1A86
TURZX_PID: int = 0x5722
TURZX_SERIAL_NUMBER: str = "USB35INCHIPSV2"

# USB Bulk Interface configuration (Interface 1, Bulk OUT 0x03, Bulk IN 0x82)
TURZX_USB_INTERFACE: int = 1
TURZX_USB_EP_OUT: int = 0x03
TURZX_USB_EP_IN: int = 0x82

# Serial defaults
TURZX_DEFAULT_BAUDRATES: Tuple[int, ...] = (115200, 9600, 19200, 38400, 57600, 230400)
TURZX_DEFAULT_TIMEOUT: float = 1.0
TURZX_MAX_FPS: int = 30

# Aliases for backward compatibility with earlier typo "TURXZ"
TURXZ_VID = TURZX_VID
TURXZ_PID = TURZX_PID
TURXZ_SERIAL_NUMBER = TURZX_SERIAL_NUMBER
TURXZ_USB_INTERFACE = TURZX_USB_INTERFACE
TURXZ_USB_EP_OUT = TURZX_USB_EP_OUT
TURXZ_USB_EP_IN = TURZX_USB_EP_IN
TURXZ_DEFAULT_BAUD_RATES = list(TURZX_DEFAULT_BAUDRATES)
TURXZ_SERIAL_TIMEOUT = TURZX_DEFAULT_TIMEOUT


# =============================================================================
# Protocol Enumerations
# =============================================================================

class Command(IntEnum):
    """TURZX / Turing Rev A command opcodes."""
    HELLO = 69            # Query hardware model and revision (0x45)
    RESET = 101            # Reset the display controller
    CLEAR = 102            # Clear screen to default white
    TO_BLACK = 103         # Make screen black
    SCREEN_OFF = 108       # Turn display backlight / panel off
    SCREEN_ON = 109        # Turn display backlight / panel on
    SET_BRIGHTNESS = 110   # Set backlight brightness level (0-255 inverted device level)
    SET_ORIENTATION = 121  # Set screen rotation and dimensions
    SET_MIRROR = 122       # Enable hardware mirror mode
    DISPLAY_PIXELS = 195   # Display non-contiguous pixel blocks
    DISPLAY_BITMAP = 197   # Stream continuous rectangular RGB565 bitmap


class Orientation(IntEnum):
    """Screen orientation modes."""
    PORTRAIT = 0        # Vertical (320x480 standard)
    LANDSCAPE = 1       # Horizontal (480x320)
    REV_PORTRAIT = 2    # Inverted vertical (180 deg)
    REV_LANDSCAPE = 3   # Inverted horizontal (270 deg)


class SubRevision(IntEnum):
    """Hardware model identifiers received in HELLO response."""
    TURING_3_5 = 0                          # Official Turing 3.5" (no response or 0)
    USBMONITOR_3_5 = 0x010101010101         # UsbMonitor 3.5" IPS (320x480)
    USBMONITOR_5 = 0x020202020202           # UsbMonitor 5.0" IPS (480x800)
    USBMONITOR_7 = 0x030303030303           # UsbMonitor 7.0" IPS (600x1024)

    @classmethod
    def from_response(cls, resp: bytes = b"", descriptor: Optional[str] = None) -> "SubRevision":
        """Parse raw 6-byte HELLO response or device serial/product descriptor."""
        if bytes([0x01] * 6) in resp or resp == bytes([0x01] * 6):
            return cls.USBMONITOR_3_5
        if bytes([0x02] * 6) in resp or resp == bytes([0x02] * 6):
            return cls.USBMONITOR_5
        if bytes([0x03] * 6) in resp or resp == bytes([0x03] * 6):
            return cls.USBMONITOR_7

        if descriptor:
            d_upper = descriptor.upper()
            if "35" in d_upper or "3.5" in d_upper:
                return cls.USBMONITOR_3_5
            if "50" in d_upper or "5.0" in d_upper or ("5INCH" in d_upper and "35" not in d_upper):
                return cls.USBMONITOR_5
            if "70" in d_upper or "7.0" in d_upper or "7INCH" in d_upper:
                return cls.USBMONITOR_7

        return cls.TURING_3_5

    @property
    def resolution(self) -> Tuple[int, int]:
        """Return (width, height) native resolution for this revision."""
        if self == SubRevision.USBMONITOR_5:
            return 480, 800
        if self == SubRevision.USBMONITOR_7:
            return 600, 1024
        return 320, 480


# =============================================================================
# Exceptions
# =============================================================================

class TURZXError(Exception):
    """Base exception for all TURZX driver and protocol errors."""
    pass


class TURZXConnectionError(TURZXError):
    """Raised when hardware connection or discovery fails."""
    pass


class TURZXProtocolError(TURZXError):
    """Raised when a protocol violation, bad argument, or transmission fails."""
    pass


# Backward compatibility exception aliases
TURXZSerialError = TURZXError
TURXZSerialConnectionError = TURZXConnectionError
TURXZSerialProtocolError = TURZXProtocolError


# =============================================================================
# Packet Encoding & Decoding Functions
# =============================================================================

def encode_command_packet(x: int, y: int, ex: int, ey: int, cmd: Union[Command, int]) -> bytes:
    """Encode a 6-byte TURZX Rev A command packet.

    Bit Packing Layout:
      Coordinates x, y, ex, ey are encoded as 10-bit unsigned integers (0-1023),
      packed across the first 5 bytes (40 bits). Byte 5 contains the command opcode.

      Byte 0: [x_9..x_2]                 (8 bits)
      Byte 1: [x_1..x_0, y_9..y_4]       (2 bits of x + 6 bits of y)
      Byte 2: [y_3..y_0, ex_9..ex_6]     (4 bits of y + 4 bits of ex)
      Byte 3: [ex_5..ex_0, ey_9..ey_8]   (6 bits of ex + 2 bits of ey)
      Byte 4: [ey_7..ey_0]               (8 bits of ey)
      Byte 5: [cmd_7..cmd_0]             (8 bits of command opcode)

    Args:
        x: Starting horizontal coordinate (0-1023)
        y: Starting vertical coordinate (0-1023)
        ex: Ending horizontal coordinate (0-1023)
        ey: Ending vertical coordinate (0-1023)
        cmd: Command opcode (Command enum or int)

    Returns:
        6 bytes packet ready for USB / serial transmission.
    """
    if not (0 <= x <= 1023 and 0 <= y <= 1023 and 0 <= ex <= 1023 and 0 <= ey <= 1023):
        raise ValueError(f"Coordinates must be in range [0, 1023], got x={x}, y={y}, ex={ex}, ey={ey}")

    cmd_val = int(cmd) & 0xFF
    buf = bytearray(6)
    buf[0] = (x >> 2) & 0xFF
    buf[1] = (((x & 3) << 6) | (y >> 4)) & 0xFF
    buf[2] = (((y & 15) << 4) | (ex >> 6)) & 0xFF
    buf[3] = (((ex & 63) << 2) | (ey >> 8)) & 0xFF
    buf[4] = ey & 0xFF
    buf[5] = cmd_val
    return bytes(buf)


def decode_command_packet(packet: bytes) -> Tuple[int, int, int, int, int]:
    """Decode a 6-byte command packet into (x, y, ex, ey, cmd).

    Args:
        packet: 6-byte sequence

    Returns:
        Tuple of (x, y, ex, ey, cmd)
    """
    if len(packet) != 6:
        raise ValueError(f"Command packet must be exactly 6 bytes, got {len(packet)}")

    b0, b1, b2, b3, b4, cmd = packet
    x = (b0 << 2) | (b1 >> 6)
    y = ((b1 & 0x3F) << 4) | (b2 >> 4)
    ex = ((b2 & 0x0F) << 6) | (b3 >> 2)
    ey = ((b3 & 0x03) << 8) | b4
    return x, y, ex, ey, cmd


def encode_orientation_packet(orientation: Union[Orientation, int], width: int, height: int) -> bytes:
    """Encode a 16-byte SET_ORIENTATION command packet.

    Format:
      Bytes 0..4:  Position encoding (all 0 for full screen)
      Byte 5:      Command.SET_ORIENTATION (121)
      Byte 6:      (orientation + 100) & 0xFF
      Bytes 7..8:  width (16-bit big-endian)
      Bytes 9..10: height (16-bit big-endian)
      Bytes 11..15: Padding zeros (5 bytes)

    Args:
        orientation: Orientation mode (0=PORTRAIT, 1=LANDSCAPE, etc.)
        width: Display width in pixels
        height: Display height in pixels

    Returns:
        16-byte orientation packet.
    """
    buf = bytearray(16)
    buf[5] = Command.SET_ORIENTATION
    buf[6] = (int(orientation) + 100) & 0xFF
    buf[7] = (width >> 8) & 0xFF
    buf[8] = width & 0xFF
    buf[9] = (height >> 8) & 0xFF
    buf[10] = height & 0xFF
    return bytes(buf)


def encode_hello_packet() -> bytes:
    """Encode HELLO handshake packet (6 bytes of value 69 = 0x45)."""
    return bytes([Command.HELLO] * 6)


def brightness_to_device_level(level: int) -> int:
    """Convert human-readable brightness (0-100%) to hardware inverted level (0-255).

    Hardware scale:
      0 = maximum brightness
      255 = lowest brightness / dark

    Software scale:
      0% = dark
      100% = maximum brightness
    """
    if not (0 <= level <= 100):
        raise ValueError(f"Brightness level must be in range 0-100, got {level}")
    return int(round(255 - (level / 100.0) * 255))
