"""TURZX 3.5" / 5.0" / 7.0" USB Display Driver.

High-performance hardware driver for TURZX / Turing Smart Screen secondary monitors.
Supports:
  1. Direct PyUSB Bulk OUT endpoint (Interface 1, Endpoint 0x03) — optimal on macOS
     and Linux to bypass CDC-ACM driver packet chopping.
  2. Fallback PySerial CDC-ACM communication.
  3. Automatic hardware discovery, model revision detection, and connection recovery.
  4. Context manager pattern for clean deterministic resource release.
"""

import logging
import os
import platform
import threading
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from PIL import Image

from .image_utils import create_solid_image, image_to_rgb565, prepare_image
from .protocol import (
    TURZX_DEFAULT_BAUDRATES,
    TURZX_DEFAULT_TIMEOUT,
    TURZX_PID,
    TURZX_SERIAL_NUMBER,
    TURZX_USB_EP_IN,
    TURZX_USB_EP_OUT,
    TURZX_USB_INTERFACE,
    TURZX_VID,
    Command,
    Orientation,
    SubRevision,
    TURZXConnectionError,
    TURZXProtocolError,
    brightness_to_device_level,
    encode_command_packet,
    encode_hello_packet,
    encode_orientation_packet,
)

try:
    import usb.core
    import usb.util
    PYUSB_AVAILABLE = True
except ImportError:
    usb = None
    PYUSB_AVAILABLE = False

try:
    import serial
    import serial.tools.list_ports
    PYSERIAL_AVAILABLE = True
except ImportError:
    serial = None
    PYSERIAL_AVAILABLE = False

logger = logging.getLogger(__name__)


class TURZXDriver:
    """Hardware driver for TURZX / Turing Smart Screen USB-C secondary displays.

    Supports both PyUSB bulk endpoint streaming (preferred) and serial port fallback.
    Thread-safe and supports context manager usage.
    """

    def __init__(
        self,
        port: str = "AUTO",
        baudrate: int = 115200,
        timeout: float = TURZX_DEFAULT_TIMEOUT,
        display_width: int = 320,
        display_height: int = 480,
        prefer_usb: bool = True,
    ):
        """Initialize driver configuration.

        Args:
            port: Serial device path (e.g. '/dev/cu.usbmodem...' or 'COM3') or 'AUTO'.
            baudrate: Communication baud rate for serial fallback (default 115200).
            timeout: I/O timeout in seconds (default 1.0).
            display_width: Native screen width in pixels (default 320).
            display_height: Native screen height in pixels (default 480).
            prefer_usb: If True, attempt direct PyUSB bulk transport before serial.
        """
        self.port: Optional[str] = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.native_width = min(display_width, display_height)
        self.native_height = max(display_width, display_height)
        self.display_width = display_width
        self.display_height = display_height
        self._custom_resolution = (display_width != 320 or display_height != 480)
        self.prefer_usb = prefer_usb

        self._transport: str = "none"  # "usb", "serial", or "none"
        self._usb_dev: Optional[Any] = None
        self._serial: Optional[Any] = None
        self._connected: bool = False
        self._sub_revision: Optional[SubRevision] = None
        self._orientation: int = int(Orientation.PORTRAIT)

        self._lock = threading.RLock()

        if self.port == "AUTO" and PYSERIAL_AVAILABLE:
            detected = self._auto_detect_port()
            if detected:
                self.port = detected

    def __enter__(self) -> "TURZXDriver":
        """Enter context manager, connecting automatically."""
        if not self.connect():
            raise TURZXConnectionError("Could not connect to TURZX display.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager, disconnecting automatically."""
        self.disconnect()

    @property
    def connected(self) -> bool:
        """Whether the driver is currently connected to hardware."""
        return self._connected

    @property
    def transport(self) -> str:
        """Active transport mechanism ('usb', 'serial', or 'none')."""
        return self._transport

    @property
    def orientation(self) -> int:
        """Current screen orientation setting."""
        return self._orientation

    @property
    def sub_revision(self) -> Optional[SubRevision]:
        """Detected hardware sub-revision model."""
        return self._sub_revision

    # =========================================================================
    # Device Discovery & Connection
    # =========================================================================

    def _auto_detect_port(self) -> Optional[str]:
        """Auto-detect the TURZX display serial port via pyserial tools."""
        if not PYSERIAL_AVAILABLE:
            return None
        for port_obj in serial.tools.list_ports.comports():
            # 1. Match serial number attribute
            if port_obj.serial_number and TURZX_SERIAL_NUMBER.upper() in port_obj.serial_number.upper():
                logger.info("Auto-detected TURZX display at %s (serial number match)", port_obj.device)
                return port_obj.device
            # 2. Match VID:PID
            if port_obj.vid == TURZX_VID and port_obj.pid == TURZX_PID:
                logger.info("Auto-detected TURZX display at %s (VID:PID match)", port_obj.device)
                return port_obj.device
            # 3. Match device path, description, or hwid
            dev_str = f"{port_obj.device} {port_obj.description or ''} {port_obj.hwid or ''}".upper()
            if TURZX_SERIAL_NUMBER.upper() in dev_str or "USBMONITOR" in dev_str:
                logger.info("Auto-detected TURZX display at %s (device name/description match)", port_obj.device)
                return port_obj.device
        logger.debug("TURZX display not found in serial port enumeration")
        return None

    def available_ports(self) -> List[Dict[str, str]]:
        """List all connected serial ports with hardware details."""
        ports = []
        if PYSERIAL_AVAILABLE:
            for port_obj in serial.tools.list_ports.comports():
                ports.append({
                    "device": port_obj.device,
                    "description": port_obj.description or "",
                    "hwid": port_obj.hwid or "",
                    "vid": f"0x{port_obj.vid:04x}" if port_obj.vid else "N/A",
                    "pid": f"0x{port_obj.pid:04x}" if port_obj.pid else "N/A",
                    "serial": port_obj.serial_number or "N/A",
                })
        return ports

    def connect(self) -> bool:
        """Establish connection to the TURZX display hardware.

        Attempts PyUSB Bulk OUT endpoint first (if prefer_usb=True and pyusb available),
        then falls back to serial communication.
        """
        with self._lock:
            if self._connected:
                return True

            # 1. Try PyUSB bulk transport first
            if self.prefer_usb and PYUSB_AVAILABLE:
                if self._try_connect_usb():
                    return True

            # 2. Try Serial transport as fallback
            if PYSERIAL_AVAILABLE:
                if self._try_connect_serial():
                    return True

            logger.error("Could not establish connection to TURZX display via USB or Serial")
            self._connected = False
            return False

    def _try_connect_usb(self) -> bool:
        """Attempt direct PyUSB connection to interface 1 bulk endpoint."""
        try:
            logger.info("Scanning for TURZX USB device (VID:0x%04x PID:0x%04x)...", TURZX_VID, TURZX_PID)
            dev = usb.core.find(idVendor=TURZX_VID, idProduct=TURZX_PID)
            if dev is None:
                logger.debug("TURZX USB device not detected via PyUSB")
                return False

            try:
                dev.set_configuration()
            except Exception as e:
                logger.debug("set_configuration warning (ignored): %s", e)

            try:
                if dev.is_kernel_driver_active(TURZX_USB_INTERFACE):
                    dev.detach_kernel_driver(TURZX_USB_INTERFACE)
            except (NotImplementedError, Exception):
                pass

            usb.util.claim_interface(dev, TURZX_USB_INTERFACE)
            self._usb_dev = dev
            self._transport = "usb"
            self._connected = True

            # Perform HELLO handshake
            self._hello_handshake()
            logger.info(
                "✓ Connected via PyUSB Bulk OUT endpoint 0x%02x (device: %s)",
                TURZX_USB_EP_OUT,
                dev.product or "TURZX Display",
            )
            return True

        except Exception as e:
            logger.warning("Direct PyUSB connection failed: %s", e)
            if self._usb_dev:
                try:
                    usb.util.release_interface(self._usb_dev, TURZX_USB_INTERFACE)
                    usb.util.dispose_resources(self._usb_dev)
                except Exception:
                    pass
                self._usb_dev = None
            self._transport = "none"
            self._connected = False
            return False

    def _try_connect_serial(self) -> bool:
        """Attempt serial connection using pyserial."""
        if not PYSERIAL_AVAILABLE:
            return False

        target_port = self.port
        if target_port == "AUTO" or not target_port:
            target_port = self._auto_detect_port()

        if not target_port or target_port == "AUTO":
            logger.debug("No TURZX serial port specified or detected.")
            return False

        baud_candidates = [self.baudrate] + [b for b in TURZX_DEFAULT_BAUDRATES if b != self.baudrate]
        for baud in baud_candidates:
            try:
                logger.info("Attempting serial connection on %s at %d baud...", target_port, baud)
                ser = serial.Serial(
                    port=target_port,
                    baudrate=baud,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=self.timeout,
                    rtscts=True,
                )
                time.sleep(0.1)
                ser.reset_input_buffer()
                ser.reset_output_buffer()

                self._serial = ser
                self._transport = "serial"
                self.port = target_port
                self.baudrate = baud
                self._connected = True

                self._hello_handshake()
                logger.info("✓ Connected via Serial on %s at %d baud", self.port, baud)
                return True

            except Exception as e:
                logger.warning("Serial connection attempt failed at %d baud on %s: %s", baud, target_port, e)
                if self._serial:
                    try:
                        self._serial.close()
                    except Exception:
                        pass
                    self._serial = None

        self._transport = "none"
        self._connected = False
        return False

    def _hello_handshake(self) -> bool:
        """Send HELLO handshake to probe model sub-revision."""
        try:
            pkt = encode_hello_packet()
            resp = b""
            desc = ""

            if self._transport == "usb" and self._usb_dev:
                try:
                    desc = self._usb_dev.serial_number or self._usb_dev.product or ""
                except Exception:
                    pass
                if not desc and PYUSB_AVAILABLE:
                    try:
                        desc = usb.util.get_string(self._usb_dev, self._usb_dev.iSerialNumber) or ""
                    except Exception:
                        pass

                self._usb_dev.write(TURZX_USB_EP_OUT, pkt, timeout=500)
                try:
                    resp = bytes(self._usb_dev.read(TURZX_USB_EP_IN, 6, timeout=200))
                except Exception:
                    resp = b""
            elif self._transport == "serial" and self._serial:
                desc = self.port or ""
                self._serial.write(pkt)
                self._serial.flush()
                time.sleep(0.1)
                resp = self._serial.read(6)

            self._sub_revision = SubRevision.from_response(resp, descriptor=desc)
            if self._sub_revision != SubRevision.TURING_3_5 or not self._custom_resolution:
                base_w, base_h = self._sub_revision.resolution
                self.native_width = min(base_w, base_h)
                self.native_height = max(base_w, base_h)

            if self._orientation in (Orientation.LANDSCAPE, Orientation.REV_LANDSCAPE):
                self.display_width = self.native_height
                self.display_height = self.native_width
            else:
                self.display_width = self.native_width
                self.display_height = self.native_height

            logger.info(
                "Display sub-revision: %s (Resolution: %dx%d, Native: %dx%d)",
                self._sub_revision.name,
                self.display_width,
                self.display_height,
                self.native_width,
                self.native_height,
            )
            return True

        except Exception as e:
            logger.debug("HELLO handshake notice: %s (defaulting to current/320x480)", e)
            self._sub_revision = SubRevision.TURING_3_5
            if not self._custom_resolution:
                self.native_width, self.native_height = 320, 480
            if self._orientation in (Orientation.LANDSCAPE, Orientation.REV_LANDSCAPE):
                self.display_width, self.display_height = self.native_height, self.native_width
            else:
                self.display_width, self.display_height = self.native_width, self.native_height
            return True

    # =========================================================================
    # Low-Level Command Transmission
    # =========================================================================

    def _send_command(self, x: int, y: int, ex: int, ey: int, cmd: Union[Command, int]) -> bool:
        """Send a 6-byte command packet to hardware."""
        if not self._connected:
            raise TURZXProtocolError("Not connected to TURZX display. Call connect() first.")

        packet = encode_command_packet(x, y, ex, ey, cmd)
        try:
            if self._transport == "usb" and self._usb_dev:
                self._usb_dev.write(TURZX_USB_EP_OUT, packet, timeout=1000)
                return True
            if self._transport == "serial" and self._serial:
                self._serial.write(packet)
                if platform.system() == "Darwin":
                    self._serial.flush()
                return True
            return False
        except Exception as e:
            logger.error("Failed to send command %s: %s", getattr(cmd, "name", cmd), e)
            raise TURZXProtocolError(f"Command transmission failed: {e}") from e

    def _send_orientation_packet(self, orientation: Union[Orientation, int]) -> bool:
        """Send a 16-byte SET_ORIENTATION command packet."""
        if not self._connected:
            raise TURZXProtocolError("Not connected to TURZX display. Call connect() first.")

        packet = encode_orientation_packet(orientation, self.display_width, self.display_height)
        try:
            if self._transport == "usb" and self._usb_dev:
                self._usb_dev.write(TURZX_USB_EP_OUT, packet, timeout=1000)
                return True
            if self._transport == "serial" and self._serial:
                self._serial.write(packet)
                if platform.system() == "Darwin":
                    self._serial.flush()
                return True
            return False
        except Exception as e:
            logger.error("Failed to send SET_ORIENTATION: %s", e)
            raise TURZXProtocolError(f"SET_ORIENTATION failed: {e}") from e

    def _chunked(self, data: bytes, chunk_size: int) -> Iterator[bytes]:
        """Yield fixed-size chunks of data."""
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    # =========================================================================
    # High-Level Display Control API
    # =========================================================================

    def initialize(self) -> bool:
        """Run full initialization sequence: connect -> orientation -> brightness -> clear."""
        if not self._connected:
            if not self.connect():
                return False

        self.set_orientation(Orientation.PORTRAIT)
        self.set_brightness(50)
        self.clear()
        logger.info("✓ TURZX hardware initialization sequence completed successfully")
        return True

    def reset(self) -> bool:
        """Reset the display controller."""
        logger.info("Resetting TURZX display controller...")
        try:
            self._send_command(0, 0, 0, 0, Command.RESET)
        except Exception:
            pass
        self.disconnect()
        time.sleep(2.5)
        return self.connect()

    def clear(self, color: Optional[Tuple[int, int, int]] = None) -> bool:
        """Clear the display.

        Args:
            color: If None, sends hardware Command.CLEAR (white).
                   If (0, 0, 0), sends hardware Command.TO_BLACK (black).
                   If custom RGB tuple provided, renders solid color.
        """
        with self._lock:
            if color is None:
                if self._orientation != int(Orientation.PORTRAIT):
                    cur = self._orientation
                    self.set_orientation(Orientation.PORTRAIT)
                    res = self._send_command(0, 0, 0, 0, Command.CLEAR)
                    self.set_orientation(cur)
                    return res
                return self._send_command(0, 0, 0, 0, Command.CLEAR)
            elif color == (0, 0, 0):
                if self._orientation != int(Orientation.PORTRAIT):
                    cur = self._orientation
                    self.set_orientation(Orientation.PORTRAIT)
                    res = self._send_command(0, 0, 0, 0, Command.TO_BLACK)
                    self.set_orientation(cur)
                    return res
                return self._send_command(0, 0, 0, 0, Command.TO_BLACK)
            return self.display_color(color)

    def screen_off(self) -> bool:
        """Turn display backlight / screen off."""
        return self._send_command(0, 0, 0, 0, Command.SCREEN_OFF)

    def screen_on(self) -> bool:
        """Turn display backlight / screen on."""
        return self._send_command(0, 0, 0, 0, Command.SCREEN_ON)

    def set_brightness(self, level: int) -> bool:
        """Set screen brightness (0-100%).

        Args:
            level: Brightness percentage from 0 (dark) to 100 (maximum).
        """
        device_level = brightness_to_device_level(level)
        return self._send_command(device_level, 0, 0, 0, Command.SET_BRIGHTNESS)

    def set_orientation(self, orientation: Union[Orientation, int]) -> bool:
        """Set display orientation and adjust active dimensions.

        Args:
            orientation: 0=PORTRAIT, 1=LANDSCAPE, 2=REV_PORTRAIT, 3=REV_LANDSCAPE
        """
        with self._lock:
            self._orientation = int(orientation)
            if self._orientation in (Orientation.LANDSCAPE, Orientation.REV_LANDSCAPE):
                self.display_width = self.native_height
                self.display_height = self.native_width
            else:
                self.display_width = self.native_width
                self.display_height = self.native_height
            return self._send_orientation_packet(self._orientation)

    # =========================================================================
    # Image Display API
    # =========================================================================

    def display_image(
        self,
        image_data: bytes,
        x: int = 0,
        y: int = 0,
        width: int = 0,
        height: int = 0,
    ) -> bool:
        """Stream an RGB565 little-endian image to the display.

        Args:
            image_data: Raw RGB565 little-endian byte stream (width * height * 2 bytes).
            x: Starting X coordinate (default 0).
            y: Starting Y coordinate (default 0).
            width: Image width in pixels (0 for active display width).
            height: Image height in pixels (0 for active display height).
        """
        if width == 0:
            width = self.display_width
        if height == 0:
            height = self.display_height

        if width <= 0 or height <= 0:
            raise ValueError(f"Width and height must be positive, got width={width}, height={height}")

        expected_size = width * height * 2
        if len(image_data) != expected_size:
            raise ValueError(
                f"Image data size mismatch: expected {expected_size} bytes ({width}x{height}x2), "
                f"got {len(image_data)} bytes"
            )

        x1 = x + width - 1
        y1 = y + height - 1

        if not (0 <= x <= 1023 and 0 <= y <= 1023 and 0 <= x1 <= 1023 and 0 <= y1 <= 1023):
            raise ValueError(
                f"Bounding box ({x}, {y}) to ({x1}, {y1}) exceeds hardware coordinate range [0, 1023]"
            )

        with self._lock:
            if not self._connected:
                if not self.connect():
                    return False

            # Send DISPLAY_BITMAP command
            self._send_command(x, y, x1, y1, Command.DISPLAY_BITMAP)

            # Transmit stream
            if self._transport == "usb" and self._usb_dev:
                try:
                    # 4096-byte chunk size optimal for USB 2.0 Bulk endpoint
                    chunk_size = 4096
                    for chunk in self._chunked(image_data, chunk_size):
                        self._usb_dev.write(TURZX_USB_EP_OUT, chunk, timeout=2000)
                    return True
                except Exception as e:
                    logger.error("USB bulk image stream error: %s", e)
                    return False

            if self._transport == "serial" and self._serial:
                try:
                    chunk_size = width * 8
                    for chunk in self._chunked(image_data, chunk_size):
                        self._serial.write(chunk)
                        if platform.system() == "Darwin":
                            self._serial.flush()
                    return True
                except Exception as e:
                    logger.error("Serial image stream error: %s", e)
                    return False

            return False

    def display_pil_image(
        self,
        image: Image.Image,
        x: int = 0,
        y: int = 0,
        width: int = 0,
        height: int = 0,
        fit_mode: str = "cover",
    ) -> bool:
        """Process and display a PIL Image on the hardware.

        Args:
            image: Source PIL image.
            x: X offset on screen.
            y: Y offset on screen.
            width: Target width (0 to use native display width).
            height: Target height (0 to use native display height).
            fit_mode: Resizing strategy ('cover', 'contain', or 'stretch').
        """
        target_w = width if width > 0 else self.display_width
        target_h = height if height > 0 else self.display_height

        prepared = prepare_image(image, target_w, target_h, fit_mode=fit_mode)
        rgb565 = image_to_rgb565(prepared)
        return self.display_image(rgb565, x, y, target_w, target_h)

    def display_image_file(
        self,
        image_path: str,
        x: int = 0,
        y: int = 0,
        width: int = 0,
        height: int = 0,
        fit_mode: str = "cover",
    ) -> bool:
        """Load an image file from disk and display it on the hardware.

        Args:
            image_path: Path to PNG, JPEG, BMP, or WebP image file.
            x: X offset.
            y: Y offset.
            width: Target width (0 for native width).
            height: Target height (0 for native height).
            fit_mode: Resizing strategy ('cover', 'contain', or 'stretch').
        """
        if not os.path.isfile(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")

        with Image.open(image_path) as img:
            return self.display_pil_image(img, x, y, width, height, fit_mode=fit_mode)

    def display_color(self, color: Tuple[int, int, int]) -> bool:
        """Display a solid RGB color across the entire screen."""
        img = create_solid_image(self.display_width, self.display_height, color)
        return self.display_pil_image(img, fit_mode="stretch")

    # =========================================================================
    # Status & Teardown
    # =========================================================================

    def status(self) -> Dict[str, Any]:
        """Return full hardware and driver status dictionary."""
        if self._transport == "usb":
            port_desc = f"USB:0x{TURZX_VID:04x}:0x{TURZX_PID:04x}"
        elif self._transport == "serial":
            port_desc = str(self.port)
        else:
            port_desc = "none"

        return {
            "connected": self._connected,
            "transport": self._transport,
            "port": port_desc,
            "baudrate": self.baudrate,
            "sub_revision": self._sub_revision.name if self._sub_revision is not None else "UNKNOWN",
            "display_width": self.display_width,
            "display_height": self.display_height,
            "native_width": self.native_width,
            "native_height": self.native_height,
            "orientation": self._orientation,
        }

    def disconnect(self) -> bool:
        """Safely release all USB / serial resources."""
        with self._lock:
            if self._usb_dev and PYUSB_AVAILABLE:
                try:
                    usb.util.release_interface(self._usb_dev, TURZX_USB_INTERFACE)
                except Exception:
                    pass
                try:
                    usb.util.dispose_resources(self._usb_dev)
                except Exception:
                    pass
                self._usb_dev = None

            if self._serial:
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None

            self._connected = False
            self._transport = "none"
            logger.info("✓ Disconnected from TURZX display hardware")
            return True


# Backward-compatible alias for existing code referencing earlier typo
TURXZSerialDriver = TURZXDriver


def quick_connect(
    port: str = "AUTO",
    baudrate: int = 115200,
    prefer_usb: bool = True,
    **kwargs: Any,
) -> Optional[TURZXDriver]:
    """Helper to quickly connect and initialize the TURZX display."""
    driver = TURZXDriver(port=port, baudrate=baudrate, prefer_usb=prefer_usb, **kwargs)
    if driver.connect():
        driver.initialize()
        return driver
    return None
