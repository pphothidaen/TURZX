"""TURZX / Turing Smart Screen Secondary USB-C Monitor Driver.

Standalone, high-performance Python driver and CLI tool for TURZX displays
(3.5" 320x480, 5.0" 480x800, 7.0" 600x1024).

Quick Start:
    >>> from turzx import TURZXDriver
    >>> with TURZXDriver() as display:
    ...     display.display_image_file("wallpaper.png")
"""

__version__ = "1.0.0"
__author__ = "TURZX Community"

from .driver import TURZXDriver, TURXZSerialDriver, quick_connect
from .image_utils import (
    create_solid_image,
    create_test_pattern,
    image_to_rgb565,
    prepare_image,
)
from .protocol import (
    TURXZ_DEFAULT_BAUD_RATES,
    TURXZ_SERIAL_TIMEOUT,
    TURXZ_PID,
    TURXZ_SERIAL_NUMBER,
    TURXZ_USB_EP_IN,
    TURXZ_USB_EP_OUT,
    TURXZ_USB_INTERFACE,
    TURXZ_VID,
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
    TURXZSerialConnectionError,
    TURXZSerialError,
    TURXZSerialProtocolError,
    TURZXConnectionError,
    TURZXError,
    TURZXProtocolError,
    brightness_to_device_level,
    decode_command_packet,
    encode_command_packet,
    encode_hello_packet,
    encode_orientation_packet,
)

__all__ = [
    # Driver
    "TURZXDriver",
    "TURXZSerialDriver",
    "quick_connect",
    # Protocol & Enums
    "Command",
    "Orientation",
    "SubRevision",
    # Constants
    "TURZX_VID",
    "TURZX_PID",
    "TURZX_SERIAL_NUMBER",
    "TURZX_USB_INTERFACE",
    "TURZX_USB_EP_OUT",
    "TURZX_USB_EP_IN",
    "TURZX_DEFAULT_BAUDRATES",
    "TURZX_DEFAULT_TIMEOUT",
    "TURXZ_VID",
    "TURXZ_PID",
    "TURXZ_SERIAL_NUMBER",
    "TURXZ_USB_INTERFACE",
    "TURXZ_USB_EP_OUT",
    "TURXZ_USB_EP_IN",
    "TURXZ_DEFAULT_BAUD_RATES",
    "TURXZ_SERIAL_TIMEOUT",
    # Exceptions
    "TURZXError",
    "TURZXConnectionError",
    "TURZXProtocolError",
    "TURXZSerialError",
    "TURXZSerialConnectionError",
    "TURXZSerialProtocolError",
    # Codecs
    "encode_command_packet",
    "decode_command_packet",
    "encode_orientation_packet",
    "encode_hello_packet",
    "brightness_to_device_level",
    # Image utilities
    "image_to_rgb565",
    "prepare_image",
    "create_solid_image",
    "create_test_pattern",
]
