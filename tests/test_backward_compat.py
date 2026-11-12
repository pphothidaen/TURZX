"""Tests for backward compatibility shims and legacy entrypoints.

Verifies:
1. Root `serial_driver.py` exports and legacy TURXZ aliases.
2. `backend/services/serial_driver.py` exports and backend compatibility.
3. `send_image_to_display.py` script functionality and option handling.
"""

import importlib
import sys
import unittest
from unittest.mock import MagicMock, patch

import turzx
import serial_driver
import send_image_to_display

backend_serial_driver = importlib.import_module("backend.services.serial_driver")


class TestSerialDriverRootShim(unittest.TestCase):
    """Verify root `serial_driver.py` compatibility layer."""

    def test_reexported_classes_and_enums(self):
        """Ensure core classes and enums are correctly re-exported."""
        self.assertIs(serial_driver.TURZXDriver, turzx.TURZXDriver)
        self.assertIs(serial_driver.TURXZSerialDriver, turzx.TURZXDriver)
        self.assertIs(serial_driver.Command, turzx.Command)
        self.assertIs(serial_driver.Orientation, turzx.Orientation)
        self.assertIs(serial_driver.SubRevision, turzx.SubRevision)
        self.assertIs(serial_driver.quick_connect, turzx.quick_connect)

    def test_reexported_constants(self):
        """Ensure all hardware constants and legacy typo aliases are exported."""
        self.assertEqual(serial_driver.TURZX_VID, 0x1A86)
        self.assertEqual(serial_driver.TURZX_PID, 0x5722)
        self.assertEqual(serial_driver.TURXZ_VID, 0x1A86)
        self.assertEqual(serial_driver.TURXZ_PID, 0x5722)
        self.assertEqual(serial_driver.TURZX_USB_EP_OUT, 0x03)
        self.assertEqual(serial_driver.TURXZ_USB_EP_OUT, 0x03)
        self.assertEqual(serial_driver.TURZX_USB_EP_IN, 0x82)
        self.assertEqual(serial_driver.TURXZ_USB_EP_IN, 0x82)
        self.assertEqual(serial_driver.TURZX_DEFAULT_BAUDRATES, (115200, 9600, 19200, 38400, 57600, 230400))
        self.assertEqual(serial_driver.TURXZ_DEFAULT_BAUD_RATES, [115200, 9600, 19200, 38400, 57600, 230400])

    def test_reexported_exceptions(self):
        """Ensure exception hierarchy and legacy aliases are preserved."""
        self.assertIs(serial_driver.TURZXError, turzx.TURZXError)
        self.assertIs(serial_driver.TURZXConnectionError, turzx.TURZXConnectionError)
        self.assertIs(serial_driver.TURZXProtocolError, turzx.TURZXProtocolError)
        self.assertIs(serial_driver.TURXZSerialError, turzx.TURZXError)
        self.assertIs(serial_driver.TURXZSerialConnectionError, turzx.TURZXConnectionError)
        self.assertIs(serial_driver.TURXZSerialProtocolError, turzx.TURZXProtocolError)

    def test_reexported_codecs_and_image_utils(self):
        """Ensure codec functions and image utilities are available."""
        self.assertIs(serial_driver.encode_command_packet, turzx.encode_command_packet)
        self.assertIs(serial_driver.decode_command_packet, turzx.decode_command_packet)
        self.assertIs(serial_driver.encode_orientation_packet, turzx.encode_orientation_packet)
        self.assertIs(serial_driver.encode_hello_packet, turzx.encode_hello_packet)
        self.assertIs(serial_driver.brightness_to_device_level, turzx.brightness_to_device_level)
        self.assertIs(serial_driver.image_to_rgb565, turzx.image_to_rgb565)
        self.assertIs(serial_driver.prepare_image, turzx.prepare_image)
        self.assertIs(serial_driver.create_solid_image, turzx.create_solid_image)
        self.assertIs(serial_driver.create_test_pattern, turzx.create_test_pattern)

    def test_turxz_serial_driver_instantiation(self):
        """Verify legacy TURXZSerialDriver can be instantiated with original keyword arguments."""
        driver = serial_driver.TURXZSerialDriver(
            port="/dev/ttyUSB0",
            baudrate=115200,
            display_width=320,
            display_height=480,
        )
        self.assertEqual(driver.port, "/dev/ttyUSB0")
        self.assertEqual(driver.baudrate, 115200)
        self.assertEqual(driver.display_width, 320)
        self.assertEqual(driver.display_height, 480)
        self.assertFalse(driver.connected)


class TestBackendSerialDriverShim(unittest.TestCase):
    """Verify backend `backend/services/serial_driver.py` compatibility layer."""

    def test_reexports_match_turzx(self):
        """Ensure backend service shim mirrors the unified driver."""
        self.assertIs(backend_serial_driver.TURZXDriver, turzx.TURZXDriver)
        self.assertIs(backend_serial_driver.TURXZSerialDriver, turzx.TURZXDriver)
        self.assertIs(backend_serial_driver.Command, turzx.Command)
        self.assertIs(backend_serial_driver.Orientation, turzx.Orientation)
        self.assertIs(backend_serial_driver.SubRevision, turzx.SubRevision)
        self.assertIs(backend_serial_driver.image_to_rgb565, turzx.image_to_rgb565)
        self.assertIs(backend_serial_driver.TURXZSerialError, turzx.TURZXError)
        self.assertIs(backend_serial_driver.TURXZSerialProtocolError, turzx.TURZXProtocolError)


class TestSendImageToDisplay(unittest.TestCase):
    """Verify `send_image_to_display.py` functionality and options."""

    def test_find_latest_image(self):
        """Verify search patterns find existing project assets."""
        found = send_image_to_display.find_latest_image()
        # Should resolve to one of the fallback PNGs or Hermes cache
        if found is not None:
            import os
            self.assertTrue(os.path.isfile(found))

    @patch("send_image_to_display.TURZXDriver")
    def test_main_display_workflow(self, mock_driver_cls):
        """Verify complete send_image_to_display main flow with simulated hardware."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver.status.return_value = {
            "transport": "usb",
            "port": "USB:0x1a86:0x5722",
            "sub_revision": "USBMONITOR_3_5",
            "display_width": 320,
            "display_height": 480,
        }
        mock_driver.display_width = 320
        mock_driver.display_height = 480
        mock_driver.display_image_file.return_value = True
        mock_driver_cls.return_value = mock_driver

        test_args = [
            "send_image_to_display.py",
            "35inchENG/3.5/back18.png",
            "-b", "75",
            "-o", "1",
            "--fit", "contain",
            "--clear",
        ]
        with patch.object(sys, "argv", test_args):
            send_image_to_display.main()

        mock_driver.connect.assert_called_once()
        mock_driver.set_orientation.assert_called_once_with(1)
        mock_driver.set_brightness.assert_called_once_with(75)
        mock_driver.clear.assert_called_once()
        mock_driver.display_image_file.assert_called_once_with("35inchENG/3.5/back18.png", fit_mode="contain")
        mock_driver.disconnect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
