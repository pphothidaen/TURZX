"""Tests for turzx.cli module."""

import unittest
from unittest.mock import MagicMock, patch
from turzx.cli import main, parse_color


class TestTURZXCLI(unittest.TestCase):
    """Test CLI parsing, subcommands, and error handling."""

    def test_parse_color(self):
        """Verify hex, RGB tuple, and named color parsing."""
        self.assertEqual(parse_color("#FF0000"), (255, 0, 0))
        self.assertEqual(parse_color("#00ff00"), (0, 255, 0))
        self.assertEqual(parse_color("#00f"), (0, 0, 255))
        self.assertEqual(parse_color("255, 128, 0"), (255, 128, 0))
        self.assertEqual(parse_color("black"), (0, 0, 0))
        self.assertEqual(parse_color("white"), (255, 255, 255))

        with self.assertRaises(Exception):
            parse_color("invalid_color_xyz")

    @patch("turzx.cli.TURZXDriver")
    def test_cli_brightness_validation(self, mock_driver_cls):
        """Verify brightness out-of-range returns non-zero error without traceback."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver_cls.return_value = mock_driver

        # Valid brightness
        ret_ok = main(["brightness", "75"])
        self.assertEqual(ret_ok, 0)
        mock_driver.set_brightness.assert_called_with(75)

        # Invalid brightness (too high)
        ret_high = main(["brightness", "150"])
        self.assertEqual(ret_high, 1)

        # Invalid brightness (negative)
        ret_neg = main(["brightness", "-5"])
        self.assertEqual(ret_neg, 1)

    @patch("turzx.cli.TURZXDriver")
    def test_cli_status(self, mock_driver_cls):
        """Verify status command executes and returns 0."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver.display_width = 320
        mock_driver.display_height = 480
        mock_driver.status.return_value = {
            "connected": True,
            "transport": "usb",
            "port": "USB:0x1a86:0x5722",
            "baudrate": 115200,
            "sub_revision": "USBMONITOR_3_5",
            "display_width": 320,
            "display_height": 480,
            "orientation": 0,
        }
        mock_driver.available_ports.return_value = []
        mock_driver_cls.return_value = mock_driver

        ret = main(["status"])
        self.assertEqual(ret, 0)

    @patch("turzx.cli.TURZXDriver")
    def test_cli_clear(self, mock_driver_cls):
        """Verify clear command handles default and custom colors."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver_cls.return_value = mock_driver

        # Clear white
        ret_white = main(["clear"])
        self.assertEqual(ret_white, 0)
        mock_driver.clear.assert_called_with(color=None)

        # Clear black
        ret_black = main(["clear", "--color", "black"])
        self.assertEqual(ret_black, 0)
        mock_driver.clear.assert_called_with(color=(0, 0, 0))

    @patch("turzx.cli.TURZXDriver")
    def test_cli_orientation(self, mock_driver_cls):
        """Verify orientation command sets mode and returns 0."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver_cls.return_value = mock_driver

        ret = main(["orientation", "1"])
        self.assertEqual(ret, 0)
        mock_driver.set_orientation.assert_called_with(1)

    @patch("turzx.cli.TURZXDriver")
    def test_cli_test_pattern(self, mock_driver_cls):
        """Verify test command renders diagnostic pattern."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver.display_pil_image.return_value = True
        mock_driver.display_width = 320
        mock_driver.display_height = 480
        mock_driver_cls.return_value = mock_driver

        ret = main(["test", "--pattern", "gradient"])
        self.assertEqual(ret, 0)
        mock_driver.display_pil_image.assert_called()

    @patch("turzx.cli.TURZXDriver")
    def test_cli_power(self, mock_driver_cls):
        """Verify power command controls panel state."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver_cls.return_value = mock_driver

        ret_on = main(["power", "on"])
        self.assertEqual(ret_on, 0)
        mock_driver.screen_on.assert_called()

        ret_off = main(["power", "off"])
        self.assertEqual(ret_off, 0)
        mock_driver.screen_off.assert_called()

    @patch("turzx.cli.TURZXDriver")
    def test_cli_reset(self, mock_driver_cls):
        """Verify reset command triggers hardware reset."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver_cls.return_value = mock_driver

        ret = main(["reset"])
        self.assertEqual(ret, 0)
        mock_driver.reset.assert_called()

    @patch("turzx.cli.TURZXDriver")
    @patch("os.path.isfile", return_value=True)
    def test_cli_send_image(self, mock_isfile, mock_driver_cls):
        """Verify send command handles image transfer."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver.display_width = 320
        mock_driver.display_height = 480
        mock_driver.status.return_value = {
            "transport": "usb",
            "port": "USB:0x1a86:0x5722",
            "sub_revision": "USBMONITOR_3_5",
            "display_width": 320,
            "display_height": 480,
        }
        mock_driver.display_image_file.return_value = True
        mock_driver_cls.return_value = mock_driver

        ret = main(["send", "/fake/path/test.png", "-b", "80", "-o", "1", "--clear"])
        self.assertEqual(ret, 0)
        mock_driver.set_orientation.assert_called_with(1)
        mock_driver.set_brightness.assert_called_with(80)
        mock_driver.clear.assert_called()
        mock_driver.display_image_file.assert_called_with("/fake/path/test.png", fit_mode="cover")

    @patch("turzx.cli.TURZXDriver")
    def test_cli_port_and_baudrate_args(self, mock_driver_cls):
        """Verify --port and --baudrate flags are passed to driver."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = True
        mock_driver.display_width = 320
        mock_driver.display_height = 480
        mock_driver.status.return_value = {
            "connected": True,
            "transport": "serial",
            "port": "/dev/ttyUSB0",
            "baudrate": 230400,
            "sub_revision": "USBMONITOR_3_5",
            "display_width": 320,
            "display_height": 480,
            "orientation": 0,
        }
        mock_driver.available_ports.return_value = []
        mock_driver_cls.return_value = mock_driver

        ret = main(["status", "-p", "/dev/ttyUSB0", "--baudrate", "230400"])
        self.assertEqual(ret, 0)
        mock_driver_cls.assert_called_with(port="/dev/ttyUSB0", baudrate=230400, prefer_usb=True)

    @patch("turzx.cli.TURZXDriver")
    def test_cli_connection_failure(self, mock_driver_cls):
        """Verify failure to connect returns exit code 1."""
        mock_driver = MagicMock()
        mock_driver.connect.return_value = False
        mock_driver_cls.return_value = mock_driver

        self.assertEqual(main(["clear"]), 1)
        self.assertEqual(main(["orientation", "0"]), 1)
        self.assertEqual(main(["power", "on"]), 1)
        self.assertEqual(main(["reset"]), 1)


if __name__ == "__main__":
    unittest.main()
