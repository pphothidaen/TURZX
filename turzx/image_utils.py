"""Image processing utilities for TURZX display hardware.

Provides:
- High-speed RGB888 to RGB565 little-endian conversion (vectorized numpy with pure-Python fallback).
- Aspect-ratio preserving scaling, letterboxing, and cropping (cover, contain, stretch).
- Alpha channel compositing.
- Test pattern generation for display hardware validation and calibration.
"""

from typing import Tuple
from PIL import Image, ImageDraw

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    np = None  # type: ignore[assignment]
    NUMPY_AVAILABLE = False


def image_to_rgb565(image: Image.Image) -> bytes:
    """Convert PIL image to 16-bit RGB565 little-endian byte stream.

    RGB565 layout (16-bit word):
      Bits 15..11: Red (5 bits, 0-31)
      Bits 10..5:  Green (6 bits, 0-63)
      Bits 4..0:   Blue (5 bits, 0-31)

    Little-endian byte layout:
      Byte 0: low 8 bits  (G[2:0] << 5 | B[4:0])
      Byte 1: high 8 bits (R[4:0] << 3 | G[5:3])

    Args:
        image: PIL Image object in any mode (RGB, RGBA, L, P, etc.)

    Returns:
        Raw bytes of length (width * height * 2).
    """
    # 1. Handle color modes and transparency
    if image.mode != "RGB":
        if "A" in image.mode:
            rgba = image.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (0, 0, 0))
            bg.paste(rgba, mask=rgba.split()[3])
            image = bg
        elif image.mode == "P" and "transparency" in image.info:
            rgba = image.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (0, 0, 0))
            bg.paste(rgba, mask=rgba.split()[3])
            image = bg
        else:
            image = image.convert("RGB")

    # 2. Vectorized NumPy conversion (fastest path)
    if NUMPY_AVAILABLE:
        rgb = np.asarray(image, dtype=np.uint16)
        r = rgb[..., 0] >> 3
        g = rgb[..., 1] >> 2
        b = rgb[..., 2] >> 3
        rgb565 = (r << 11) | (g << 5) | b
        return rgb565.astype("<u2").tobytes()

    # 3. Pure Python fallback (portable when numpy is absent)
    width, height = image.size
    pixels = image.getdata()
    out = bytearray(width * height * 2)
    idx = 0
    for pixel in list(pixels):
        r, g, b = pixel[0], pixel[1], pixel[2]
        w = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        out[idx] = w & 0xFF
        out[idx + 1] = (w >> 8) & 0xFF
        idx += 2
    return bytes(out)


def prepare_image(
    image: Image.Image,
    target_width: int = 320,
    target_height: int = 480,
    fit_mode: str = "cover",
    background: Tuple[int, int, int] = (0, 0, 0),
) -> Image.Image:
    """Prepare and resize an image to match the display dimensions.

    Args:
        image: Source PIL image.
        target_width: Desired display width (default 320).
        target_height: Desired display height (default 480).
        fit_mode: Resizing strategy:
            - 'cover': Scale proportionally to cover dimensions, center-crop excess (no black bars).
            - 'contain': Scale proportionally to fit inside dimensions, letterbox with background.
            - 'stretch': Direct non-proportional resize to exact dimensions.
        background: RGB background color for contain letterbox / alpha composite.

    Returns:
        Processed PIL Image with exact dimensions (target_width, target_height) in RGB mode.
    """
    if target_width <= 0 or target_height <= 0:
        raise ValueError(f"target_width and target_height must be positive, got {target_width}x{target_height}")

    # Composite RGBA / alpha channels if needed
    if "A" in image.mode:
        rgba = image.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.split()[3])
        image = bg
    elif image.mode == "P" and "transparency" in image.info:
        rgba = image.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.split()[3])
        image = bg
    elif image.mode != "RGB":
        image = image.convert("RGB")

    orig_w, orig_h = image.size
    if (orig_w, orig_h) == (target_width, target_height):
        return image

    if fit_mode == "stretch":
        return image.resize((target_width, target_height), Image.Resampling.LANCZOS)

    if fit_mode == "contain":
        scale = min(target_width / orig_w, target_height / orig_h)
        new_w = max(1, int(round(orig_w * scale)))
        new_h = max(1, int(round(orig_h * scale)))
        resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

        canvas = Image.new("RGB", (target_width, target_height), background)
        offset_x = (target_width - new_w) // 2
        offset_y = (target_height - new_h) // 2
        canvas.paste(resized, (offset_x, offset_y))
        return canvas

    # Default: 'cover' (scale up to fill and crop excess)
    scale = max(target_width / orig_w, target_height / orig_h)
    new_w = int(round(orig_w * scale))
    new_h = int(round(orig_h * scale))
    resized = image.resize((new_w, new_h), Image.Resampling.LANCZOS)

    left = (new_w - target_width) // 2
    top = (new_h - target_height) // 2
    return resized.crop((left, top, left + target_width, top + target_height))


def create_solid_image(width: int, height: int, color: Tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    """Create a single solid color PIL image."""
    return Image.new("RGB", (width, height), color)


def create_test_pattern(width: int = 320, height: int = 480, pattern: str = "bars") -> Image.Image:
    """Generate diagnostic test patterns for screen testing and color calibration.

    Args:
        width: Screen width (default 320).
        height: Screen height (default 480).
        pattern: Pattern type:
            - 'bars': 8-color test bars (White, Yellow, Cyan, Green, Magenta, Red, Blue, Black).
            - 'gradient': Horizontal RGB color ramp.
            - 'grid': Calibration crosshatch grid with center markings.

    Returns:
        PIL Image with the requested test pattern.
    """
    img = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(img)

    if pattern == "bars":
        colors = [
            (255, 255, 255),  # White
            (255, 255, 0),    # Yellow
            (0, 255, 255),    # Cyan
            (0, 255, 0),      # Green
            (255, 0, 255),    # Magenta
            (255, 0, 0),      # Red
            (0, 0, 255),      # Blue
            (0, 0, 0),        # Black
        ]
        bar_height = height // len(colors)
        for i, color in enumerate(colors):
            y0 = i * bar_height
            y1 = (i + 1) * bar_height if i < len(colors) - 1 else height
            draw.rectangle([0, y0, width, y1], fill=color)

    elif pattern == "gradient":
        for x in range(width):
            ratio = x / max(1, width - 1)
            # Ramp through R -> G -> B
            r = int(255 * (1 - ratio))
            g = int(255 * (1 - abs(ratio - 0.5) * 2))
            b = int(255 * ratio)
            draw.line([(x, 0), (x, height)], fill=(r, g, b))

    elif pattern == "grid":
        grid_size = 32
        for x in range(0, width, grid_size):
            draw.line([(x, 0), (x, height)], fill=(60, 60, 60))
        for y in range(0, height, grid_size):
            draw.line([(0, y), (width, y)], fill=(60, 60, 60))

        # Center crosshair
        cx, cy = width // 2, height // 2
        draw.line([(cx, 0), (cx, height)], fill=(255, 0, 0), width=2)
        draw.line([(0, cy), (width, cy)], fill=(0, 255, 0), width=2)
        draw.ellipse([cx - 30, cy - 30, cx + 30, cy + 30], outline=(0, 150, 255), width=2)

    return img
