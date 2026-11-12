"""Tests for turzx.protocol module."""

import unittest
from turzx.protocol import (
    Command,
    Orientation,
    SubRevision,
    TURZX_VID,
    TURZX_PID,
    TURZX_USB_INTERFACE,
    TURZX_USB_EP_OUT,
    TURZX_USB_EP_IN,
    encode_command_packet,
    decode_command_packet,
    encode_orientation_packet,
    encode_hello_packet,
    brightness_to_device_level,
)


class TestProtocolCodecs(unittest.TestCase):
    """Test packet encoders, decoders, and protocol math."""

    def test_constants(self):
        """Verify hardware constants match device specs."""
        self.assertEqual(TURZX_VID, 0x1A86)
        self.assertEqual(TURZX_PID, 0x5722)
        self.assertEqual(TURZX_USB_INTERFACE, 1)
        self.assertEqual(TURZX_USB_EP_OUT, 0x03)
        self.assertEqual(TURZX_USB_EP_IN, 0x82)

    def test_encode_decode_roundtrip_edge_cases(self):
        """Test command packet bit-packing roundtrip across boundary coordinates."""
        test_cases = [
            (0, 0, 0, 0, Command.RESET),
            (0, 0, 319, 479, Command.DISPLAY_BITMAP),
            (0, 0, 479, 799, Command.DISPLAY_BITMAP),
            (0, 0, 599, 1023, Command.DISPLAY_BITMAP),
            (100, 200, 300, 400, Command.CLEAR),
            (128, 0, 0, 0, Command.SET_BRIGHTNESS),
            (1023, 1023, 1023, 1023, Command.SCREEN_OFF),
        ]

        for x, y, ex, ey, cmd in test_cases:
            pkt = encode_command_packet(x, y, ex, ey, cmd)
            self.assertEqual(len(pkt), 6)
            rx, ry, rex, rey, rcmd = decode_command_packet(pkt)
            self.assertEqual((rx, ry, rex, rey, rcmd), (x, y, ex, ey, int(cmd)))

    def test_encode_command_packet_invalid_coords(self):
        """Verify out-of-range coordinates raise ValueError."""
        with self.assertRaises(ValueError):
            encode_command_packet(-1, 0, 10, 10, Command.CLEAR)
        with self.assertRaises(ValueError):
            encode_command_packet(0, 1024, 10, 10, Command.CLEAR)
        with self.assertRaises(ValueError):
            encode_command_packet(0, 0, -5, 10, Command.CLEAR)
        with self.assertRaises(ValueError):
            encode_command_packet(0, 0, 10, 2000, Command.CLEAR)

    def test_decode_command_packet_invalid_length(self):
        """Verify invalid packet length raises ValueError."""
        with self.assertRaises(ValueError):
            decode_command_packet(b"\x00" * 5)
        with self.assertRaises(ValueError):
            decode_command_packet(b"\x00" * 7)

    def test_encode_orientation_packet(self):
        """Verify 16-byte orientation packet structure."""
        pkt = encode_orientation_packet(Orientation.PORTRAIT, 320, 480)
        self.assertEqual(len(pkt), 16)
        # Bytes 0-4 are zeros
        self.assertEqual(pkt[:5], b"\x00" * 5)
        # Byte 5 is Command.SET_ORIENTATION (121)
        self.assertEqual(pkt[5], Command.SET_ORIENTATION)
        # Byte 6 is orientation + 100
        self.assertEqual(pkt[6], 100)
        # Bytes 7-8: width (320 = 0x0140)
        self.assertEqual(pkt[7], 0x01)
        self.assertEqual(pkt[8], 0x40)
        # Bytes 9-10: height (480 = 0x01E0)
        self.assertEqual(pkt[9], 0x01)
        self.assertEqual(pkt[10], 0xE0)
        # Bytes 11-15: padding zeros
        self.assertEqual(pkt[11:], b"\x00" * 5)

        # Test landscape mode
        pkt_land = encode_orientation_packet(Orientation.LANDSCAPE, 480, 320)
        self.assertEqual(pkt_land[6], 101)
        self.assertEqual(pkt_land[7:9], (480).to_bytes(2, "big"))
        self.assertEqual(pkt_land[9:11], (320).to_bytes(2, "big"))

    def test_encode_hello_packet(self):
        """Verify HELLO handshake packet is 6 bytes of 0x45 (69)."""
        pkt = encode_hello_packet()
        self.assertEqual(pkt, bytes([69] * 6))

    def test_subrevision_from_response(self):
        """Verify model detection from HELLO response."""
        self.assertEqual(SubRevision.from_response(bytes([0x01] * 6)), SubRevision.USBMONITOR_3_5)
        self.assertEqual(SubRevision.from_response(bytes([0x02] * 6)), SubRevision.USBMONITOR_5)
        self.assertEqual(SubRevision.from_response(bytes([0x03] * 6)), SubRevision.USBMONITOR_7)
        self.assertEqual(SubRevision.from_response(b""), SubRevision.TURING_3_5)
        self.assertEqual(SubRevision.from_response(bytes([0xFF] * 6)), SubRevision.TURING_3_5)

        # Test descriptor parsing fallback
        self.assertEqual(SubRevision.from_response(b"", descriptor="USB35INCHIPSV2"), SubRevision.USBMONITOR_3_5)
        self.assertEqual(SubRevision.from_response(b"", descriptor="USB50INCHIPSV2"), SubRevision.USBMONITOR_5)
        self.assertEqual(SubRevision.from_response(b"", descriptor="USB70INCHIPSV2"), SubRevision.USBMONITOR_7)
        self.assertEqual(SubRevision.from_response(b"", descriptor="unknown_device"), SubRevision.TURING_3_5)

        # Test resolution property
        self.assertEqual(SubRevision.USBMONITOR_3_5.resolution, (320, 480))
        self.assertEqual(SubRevision.USBMONITOR_5.resolution, (480, 800))
        self.assertEqual(SubRevision.USBMONITOR_7.resolution, (600, 1024))
        self.assertEqual(SubRevision.TURING_3_5.resolution, (320, 480))

    def test_brightness_to_device_level(self):
        """Verify human percentage to hardware scale conversion."""
        self.assertEqual(brightness_to_device_level(0), 255)    # 0% is darkest (255)
        self.assertEqual(brightness_to_device_level(100), 0)   # 100% is brightest (0)
        self.assertEqual(brightness_to_device_level(50), 128)  # 50% is midpoint

        with self.assertRaises(ValueError):
            brightness_to_device_level(-1)
        with self.assertRaises(ValueError):
            brightness_to_device_level(101)


if __name__ == "__main__":
    unittest.main()
