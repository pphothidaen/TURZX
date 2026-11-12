#!/usr/bin/env python3
"""Backend Serial Driver (Backward Compatibility Layer).

Re-exports from the core `turzx` package.
"""

import sys
import os

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from turzx import (  # noqa: E402
    TURZXDriver,
    TURXZSerialDriver,
    quick_connect,
    Command,
    Orientation,
    SubRevision,
    TURZX_VID,
    TURZX_PID,
    TURZX_SERIAL_NUMBER,
    TURZX_USB_INTERFACE,
    TURZX_USB_EP_OUT,
    TURZX_USB_EP_IN,
    TURXZ_VID,
    TURXZ_PID,
    TURXZ_SERIAL_NUMBER,
    TURXZ_USB_INTERFACE,
    TURXZ_USB_EP_OUT,
    TURXZ_USB_EP_IN,
    TURZX_DEFAULT_BAUDRATES,
    TURZX_DEFAULT_TIMEOUT,
    TURXZ_DEFAULT_BAUD_RATES,
    TURXZ_SERIAL_TIMEOUT,
    TURZXError,
    TURZXConnectionError,
    TURZXProtocolError,
    TURXZSerialError,
    TURXZSerialConnectionError,
    TURXZSerialProtocolError,
    encode_command_packet,
    decode_command_packet,
    encode_orientation_packet,
    encode_hello_packet,
    brightness_to_device_level,
    image_to_rgb565,
    prepare_image,
    create_solid_image,
    create_test_pattern,
)

__all__ = [
    "TURZXDriver",
    "TURXZSerialDriver",
    "quick_connect",
    "Command",
    "Orientation",
    "SubRevision",
    "TURZX_VID",
    "TURZX_PID",
    "TURZX_SERIAL_NUMBER",
    "TURZX_USB_INTERFACE",
    "TURZX_USB_EP_OUT",
    "TURZX_USB_EP_IN",
    "TURXZ_VID",
    "TURXZ_PID",
    "TURXZ_SERIAL_NUMBER",
    "TURXZ_USB_INTERFACE",
    "TURXZ_USB_EP_OUT",
    "TURXZ_USB_EP_IN",
    "TURZX_DEFAULT_BAUDRATES",
    "TURZX_DEFAULT_TIMEOUT",
    "TURXZ_DEFAULT_BAUD_RATES",
    "TURXZ_SERIAL_TIMEOUT",
    "TURZXError",
    "TURZXConnectionError",
    "TURZXProtocolError",
    "TURXZSerialError",
    "TURXZSerialConnectionError",
    "TURXZSerialProtocolError",
    "encode_command_packet",
    "decode_command_packet",
    "encode_orientation_packet",
    "encode_hello_packet",
    "brightness_to_device_level",
    "image_to_rgb565",
    "prepare_image",
    "create_solid_image",
    "create_test_pattern",
]
