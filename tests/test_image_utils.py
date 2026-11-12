"""Tests for turzx.image_utils module."""

import unittest
from PIL import Image
from turzx.image_utils import (
    image_to_rgb565,
    prepare_image,
    create_solid_image,
    create_test_pattern,
)


class TestImageUtils(unittest.TestCase):
    """Test image transformations, RGB565 encoding, and pattern generators."""

    def test_rgb565_pure_colors(self):
        """Verify RGB565 little-endian encoding for standard primary colors."""
        # 1x1 test images
        red = Image.new("RGB", (1, 1), (255, 0, 0))
        green = Image.new("RGB", (1, 1), (0, 255, 0))
        blue = Image.new("RGB", (1, 1), (0, 0, 255))
        white = Image.new("RGB", (1, 1), (255, 255, 255))
        black = Image.new("RGB", (1, 1), (0, 0, 0))

        # Check exact byte layout (little-endian)
        # Red:   0xF800 -> low: 0x00, high: 0xF8
        # Green: 0x07E0 -> low: 0xE0, high: 0x07
        # Blue:  0x001F -> low: 0x1F, high: 0x00
        # White: 0xFFFF -> low: 0xFF, high: 0xFF
        # Black: 0x0000 -> low: 0x00, high: 0x00
        self.assertEqual(image_to_rgb565(red), b"\x00\xf8")
        self.assertEqual(image_to_rgb565(green), b"\xe0\x07")
        self.assertEqual(image_to_rgb565(blue), b"\x1f\x00")
        self.assertEqual(image_to_rgb565(white), b"\xff\xff")
        self.assertEqual(image_to_rgb565(black), b"\x00\x00")

    def test_rgb565_payload_size(self):
        """Verify converted stream length equals width * height * 2."""
        img = Image.new("RGB", (320, 480), (128, 64, 32))
        stream = image_to_rgb565(img)
        self.assertEqual(len(stream), 320 * 480 * 2)

    def test_rgb565_rgba_alpha_compositing(self):
        """Verify RGBA images with transparency composite onto black background."""
        # Fully transparent red pixel
        transparent_red = Image.new("RGBA", (1, 1), (255, 0, 0, 0))
        stream = image_to_rgb565(transparent_red)
        # Should be black (0x00, 0x00)
        self.assertEqual(stream, b"\x00\x00")

        # Fully opaque red pixel
        opaque_red = Image.new("RGBA", (1, 1), (255, 0, 0, 255))
        self.assertEqual(image_to_rgb565(opaque_red), b"\x00\xf8")

    def test_prepare_image_cover_mode(self):
        """Verify cover fit mode scales and center-crops properly."""
        # Square image into 320x480 portrait target
        square = Image.new("RGB", (500, 500), (100, 100, 100))
        prepared = prepare_image(square, target_width=320, target_height=480, fit_mode="cover")
        self.assertEqual(prepared.size, (320, 480))
        self.assertEqual(prepared.mode, "RGB")

        # Landscape wide image into portrait target
        wide = Image.new("RGB", (1920, 1080), (50, 50, 50))
        prepared_wide = prepare_image(wide, target_width=320, target_height=480, fit_mode="cover")
        self.assertEqual(prepared_wide.size, (320, 480))

    def test_prepare_image_contain_mode(self):
        """Verify contain fit mode letterboxes properly."""
        wide = Image.new("RGB", (640, 320), (255, 255, 255))
        # 640x320 into 320x480: scale is 320/640 = 0.5 -> new size is 320x160
        # Letterboxed vertically: black padding at top and bottom (480 - 160) // 2 = 160
        prepared = prepare_image(wide, target_width=320, target_height=480, fit_mode="contain")
        self.assertEqual(prepared.size, (320, 480))

        # Pixel at (0, 0) should be background (0, 0, 0)
        self.assertEqual(prepared.getpixel((0, 0)), (0, 0, 0))
        # Pixel in center should be white (255, 255, 255)
        self.assertEqual(prepared.getpixel((160, 240)), (255, 255, 255))

    def test_prepare_image_stretch_mode(self):
        """Verify stretch mode directly resizes."""
        img = Image.new("RGB", (100, 200), (10, 20, 30))
        prepared = prepare_image(img, target_width=320, target_height=480, fit_mode="stretch")
        self.assertEqual(prepared.size, (320, 480))

    def test_create_solid_image(self):
        """Verify solid color image generation."""
        img = create_solid_image(320, 480, (12, 34, 56))
        self.assertEqual(img.size, (320, 480))
        self.assertEqual(img.getpixel((0, 0)), (12, 34, 56))
        self.assertEqual(img.getpixel((319, 479)), (12, 34, 56))

    def test_create_test_pattern_bars(self):
        """Verify SMPTE color bars pattern generation."""
        img = create_test_pattern(320, 480, pattern="bars")
        self.assertEqual(img.size, (320, 480))
        self.assertEqual(img.mode, "RGB")
        # Top bar is white
        self.assertEqual(img.getpixel((160, 5)), (255, 255, 255))
        # Bottom bar is black
        self.assertEqual(img.getpixel((160, 475)), (0, 0, 0))

    def test_create_test_pattern_gradient_and_grid(self):
        """Verify gradient and grid test pattern generation."""
        img_grad = create_test_pattern(320, 480, pattern="gradient")
        self.assertEqual(img_grad.size, (320, 480))

        img_grid = create_test_pattern(320, 480, pattern="grid")
        self.assertEqual(img_grid.size, (320, 480))

    def test_prepare_image_invalid_dimensions(self):
        """Verify non-positive target dimensions raise ValueError."""
        img = Image.new("RGB", (100, 100))
        with self.assertRaises(ValueError):
            prepare_image(img, target_width=0, target_height=480)
        with self.assertRaises(ValueError):
            prepare_image(img, target_width=320, target_height=-10)

    def test_image_to_rgb565_la_mode(self):
        """Verify LA (Luminance + Alpha) mode composites properly."""
        la = Image.new("LA", (2, 2), (255, 0))  # Fully transparent white
        stream = image_to_rgb565(la)
        # Composited on black should be 0x0000
        self.assertEqual(stream, b"\x00\x00" * 4)


if __name__ == "__main__":
    unittest.main()
