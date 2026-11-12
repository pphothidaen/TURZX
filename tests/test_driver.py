"""Tests for turzx.driver module with mock USB and Serial interfaces."""

import unittest
from unittest.mock import MagicMock, patch
from PIL import Image

from turzx.driver import TURZXDriver
from turzx.protocol import (
    Command,
    Orientation,
    SubRevision,
    TURZXProtocolError,
)


class FakeUsbDevice:
    """Mock PyUSB Device."""
    def __init__(self):
        self.product = "TURZX Fake Display"
        self.written_chunks = []
        self._claimed = False

    def set_configuration(self):
        pass

    def is_kernel_driver_active(self, interface):
        return False

    def detach_kernel_driver(self, interface):
        pass

    def write(self, endpoint, data, timeout=None):
        self.written_chunks.append((endpoint, bytes(data)))
        return len(data)

    def read(self, endpoint, size, timeout=None):
        # Return 6 bytes of 0x01 (UsbMonitor 3.5")
        return bytes([0x01] * 6)


class TestTURZXDriverUnit(unittest.TestCase):
    """Unit tests for driver lifecycle, state management, and command packets."""

    def test_driver_initial_state(self):
        """Verify driver starts disconnected with default config."""
        driver = TURZXDriver(prefer_usb=False)
        self.assertFalse(driver.connected)
        self.assertEqual(driver.transport, "none")
        self.assertEqual(driver.display_width, 320)
        self.assertEqual(driver.display_height, 480)

    def test_send_command_unconnected_raises_error(self):
        """Verify sending command while disconnected raises TURZXProtocolError."""
        driver = TURZXDriver()
        with self.assertRaises(TURZXProtocolError):
            driver.set_brightness(50)
        with self.assertRaises(TURZXProtocolError):
            driver.set_orientation(0)
        with self.assertRaises(TURZXProtocolError):
            driver.clear()

    @patch("turzx.driver.PYUSB_AVAILABLE", True)
    @patch("turzx.driver.usb.core.find")
    @patch("turzx.driver.usb.util.claim_interface")
    @patch("turzx.driver.usb.util.release_interface")
    @patch("turzx.driver.usb.util.dispose_resources")
    def test_driver_usb_connection_lifecycle(self, mock_dispose, mock_release, mock_claim, mock_find):
        """Verify USB connection, initialization, command execution, and teardown."""
        fake_dev = FakeUsbDevice()
        mock_find.return_value = fake_dev

        driver = TURZXDriver(prefer_usb=True)
        connected = driver.connect()
        self.assertTrue(connected)
        self.assertTrue(driver.connected)
        self.assertEqual(driver.transport, "usb")
        self.assertEqual(driver.sub_revision, SubRevision.USBMONITOR_3_5)

        # Verify status output
        st = driver.status()
        self.assertTrue(st["connected"])
        self.assertEqual(st["transport"], "usb")
        self.assertEqual(st["display_width"], 320)
        self.assertEqual(st["display_height"], 480)

        # Test setting brightness
        self.assertTrue(driver.set_brightness(75))

        # Test setting orientation
        self.assertTrue(driver.set_orientation(Orientation.LANDSCAPE))
        self.assertEqual(driver.orientation, int(Orientation.LANDSCAPE))

        # Test clear
        self.assertTrue(driver.clear())

        # Test power
        self.assertTrue(driver.screen_off())
        self.assertTrue(driver.screen_on())

        # Test display PIL image
        test_img = Image.new("RGB", (320, 480), (255, 0, 0))
        self.assertTrue(driver.display_pil_image(test_img))

        # Test disconnect
        self.assertTrue(driver.disconnect())
        self.assertFalse(driver.connected)
        self.assertEqual(driver.transport, "none")

    @patch("turzx.driver.PYUSB_AVAILABLE", True)
    @patch("turzx.driver.usb.core.find", return_value=None)
    @patch("turzx.driver.PYSERIAL_AVAILABLE", True)
    @patch("turzx.driver.serial.Serial")
    def test_driver_serial_fallback(self, mock_serial_class, mock_find):
        """Verify driver falls back to serial when USB device is not found."""
        fake_ser = MagicMock()
        fake_ser.read.return_value = bytes([0x01] * 6)
        mock_serial_class.return_value = fake_ser

        driver = TURZXDriver(port="/dev/tty.fake", prefer_usb=True)
        connected = driver.connect()
        self.assertTrue(connected)
        self.assertEqual(driver.transport, "serial")
        self.assertEqual(driver.sub_revision, SubRevision.USBMONITOR_3_5)

        # Send command over serial
        self.assertTrue(driver.set_brightness(50))
        fake_ser.write.assert_called()

        driver.disconnect()
        fake_ser.close.assert_called()

    @patch("turzx.driver.PYUSB_AVAILABLE", True)
    @patch("turzx.driver.usb.core.find")
    @patch("turzx.driver.usb.util.claim_interface")
    @patch("turzx.driver.usb.util.release_interface")
    @patch("turzx.driver.usb.util.dispose_resources")
    def test_context_manager(self, mock_dispose, mock_release, mock_claim, mock_find):
        """Verify context manager cleanly connects and disconnects."""
        fake_dev = FakeUsbDevice()
        mock_find.return_value = fake_dev

        with TURZXDriver(prefer_usb=True) as d:
            self.assertTrue(d.connected)
            self.assertEqual(d.transport, "usb")

        self.assertFalse(d.connected)

    @patch("turzx.driver.PYUSB_AVAILABLE", True)
    @patch("turzx.driver.usb.core.find")
    @patch("turzx.driver.usb.util.claim_interface")
    @patch("turzx.driver.usb.util.release_interface")
    @patch("turzx.driver.usb.util.dispose_resources")
    def test_orientation_dimension_swapping(self, mock_dispose, mock_release, mock_claim, mock_find):
        """Verify orientation change dynamically adjusts display_width/height and packet parameters."""
        fake_dev = FakeUsbDevice()
        mock_find.return_value = fake_dev

        with TURZXDriver(prefer_usb=True) as d:
            # Default portrait
            self.assertEqual(d.display_width, 320)
            self.assertEqual(d.display_height, 480)

            # Switch to LANDSCAPE
            fake_dev.written_chunks.clear()
            self.assertTrue(d.set_orientation(Orientation.LANDSCAPE))
            self.assertEqual(d.display_width, 480)
            self.assertEqual(d.display_height, 320)
            self.assertEqual(d.native_width, 320)
            self.assertEqual(d.native_height, 480)

            # Check that written orientation packet contains 480 (0x01E0) width and 320 (0x0140) height
            last_pkt = fake_dev.written_chunks[-1][1]
            self.assertEqual(len(last_pkt), 16)
            self.assertEqual(last_pkt[6], 101)  # LANDSCAPE + 100
            self.assertEqual(last_pkt[7:9], (480).to_bytes(2, "big"))
            self.assertEqual(last_pkt[9:11], (320).to_bytes(2, "big"))

            # Switch back to PORTRAIT
            self.assertTrue(d.set_orientation(Orientation.PORTRAIT))
            self.assertEqual(d.display_width, 320)
            self.assertEqual(d.display_height, 480)
            last_pkt_port = fake_dev.written_chunks[-1][1]
            self.assertEqual(last_pkt_port[6], 100)
            self.assertEqual(last_pkt_port[7:9], (320).to_bytes(2, "big"))
            self.assertEqual(last_pkt_port[9:11], (480).to_bytes(2, "big"))

    @patch("turzx.driver.PYUSB_AVAILABLE", True)
    @patch("turzx.driver.usb.core.find")
    @patch("turzx.driver.usb.util.claim_interface")
    @patch("turzx.driver.usb.util.release_interface")
    @patch("turzx.driver.usb.util.dispose_resources")
    def test_display_image_validation(self, mock_dispose, mock_release, mock_claim, mock_find):
        """Verify display_image payload and boundary validations."""
        fake_dev = FakeUsbDevice()
        mock_find.return_value = fake_dev

        with TURZXDriver(prefer_usb=True) as d:
            # 1. Size mismatch (short payload)
            with self.assertRaises(ValueError):
                d.display_image(b"\x00" * 10, 0, 0, 320, 480)

            # 2. Coordinates out of range
            valid_payload = b"\x00" * (10 * 10 * 2)
            with self.assertRaises(ValueError):
                d.display_image(valid_payload, 1020, 0, 10, 10)  # 1020 + 10 - 1 = 1029 > 1023

            # 3. Non-positive dimensions
            with self.assertRaises(ValueError):
                d.display_image(b"", 0, 0, 0, -5)

    @patch("turzx.driver.PYUSB_AVAILABLE", True)
    @patch("turzx.driver.usb.core.find")
    @patch("turzx.driver.usb.util.claim_interface")
    @patch("turzx.driver.usb.util.release_interface")
    @patch("turzx.driver.usb.util.dispose_resources")
    def test_clear_hardware_black(self, mock_dispose, mock_release, mock_claim, mock_find):
        """Verify clear((0, 0, 0)) sends fast hardware TO_BLACK opcode 103."""
        fake_dev = FakeUsbDevice()
        mock_find.return_value = fake_dev

        with TURZXDriver(prefer_usb=True) as d:
            fake_dev.written_chunks.clear()
            self.assertTrue(d.clear((0, 0, 0)))
            # Second packet sent was Command.TO_BLACK
            black_pkt = [c[1] for c in fake_dev.written_chunks if c[1][5] == Command.TO_BLACK]
            self.assertEqual(len(black_pkt), 1)

    def test_status_turing_3_5_falsy_bug(self):
        """Verify SubRevision.TURING_3_5 (value 0) does not get evaluated as UNKNOWN."""
        d = TURZXDriver(prefer_usb=False)
        d._sub_revision = SubRevision.TURING_3_5
        st = d.status()
        self.assertEqual(st["sub_revision"], "TURING_3_5")

    def test_status_disconnected_port_none(self):
        """Verify disconnected driver reports port as 'none', not USB VID:PID."""
        d = TURZXDriver(prefer_usb=False)
        st = d.status()
        self.assertFalse(st["connected"])
        self.assertEqual(st["transport"], "none")
        self.assertEqual(st["port"], "none")

    @patch("turzx.driver.PYUSB_AVAILABLE", True)
    @patch("turzx.driver.usb.core.find")
    @patch("turzx.driver.usb.util.claim_interface")
    @patch("turzx.driver.usb.util.release_interface")
    @patch("turzx.driver.usb.util.dispose_resources")
    def test_driver_custom_resolution_preserved(self, mock_dispose, mock_release, mock_claim, mock_find):
        """Verify explicitly provided 5.0\" resolution is not overwritten by HELLO fallback."""
        fake_dev = FakeUsbDevice()
        # Mock empty HELLO response
        fake_dev.read = lambda ep, size, timeout=None: b""
        fake_dev.product = "Custom Display"
        mock_find.return_value = fake_dev

        driver = TURZXDriver(display_width=480, display_height=800, prefer_usb=True)
        self.assertTrue(driver.connect())
        # Custom dimensions should be preserved even though HELLO returned no revision
        self.assertEqual(driver.display_width, 480)
        self.assertEqual(driver.display_height, 800)
        self.assertEqual(driver.native_width, 480)
        self.assertEqual(driver.native_height, 800)
        driver.disconnect()

    @patch("turzx.driver.PYSERIAL_AVAILABLE", True)
    @patch("turzx.driver.serial.tools.list_ports.comports")
    def test_auto_detect_port_robust_matching(self, mock_comports):
        """Verify auto-detection matches serial number substring, VID/PID, or device name."""
        port1 = MagicMock()
        port1.device = "/dev/cu.usbmodemUSB35INCHIPSV21"
        port1.serial_number = ""
        port1.vid = 0
        port1.pid = 0
        port1.description = "UsbMonitor Rev A"
        port1.hwid = ""

        mock_comports.return_value = [port1]
        driver = TURZXDriver(port="AUTO", prefer_usb=False)
        detected = driver._auto_detect_port()
        self.assertEqual(detected, "/dev/cu.usbmodemUSB35INCHIPSV21")


if __name__ == "__main__":
    unittest.main()
