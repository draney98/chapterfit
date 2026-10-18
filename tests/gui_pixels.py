"""Pixel checks for screenshots. Colours are read from the palette."""
from __future__ import annotations


def _rgb(hex_color: str) -> tuple[int, int, int]:
    text = hex_color.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def _solid_block(image, color: tuple[int, int, int], size: int = 20):
    """Top-left of a size×size block of one colour, or None."""
    width, height = image.size
    pixels = image.load()
    prefix = [[0] * (width + 1) for _ in range(height + 1)]
    for y in range(height):
        for x in range(width):
            prefix[y + 1][x + 1] = (
                prefix[y][x + 1] + prefix[y + 1][x] - prefix[y][x]
                + (pixels[x, y] == color))
    area = size * size
    for y in range(height - size + 1):
        row = prefix[y + size]
        above = prefix[y]
        for x in range(width - size + 1):
            total = row[x + size] - row[x] - above[x + size] + above[x]
            if total == area:
                return x, y
    return None


def assert_ground_colors(png, mode: str) -> None:
    """The window ground is the palette background.

    No block larger than 20×20 is pure black. Dark has no such block of
    pure white either. The corners are the ground in both modes. Light may
    still use pure white inside cards, fields, and the timeline, where the
    palette says ``surface``.
    """
    from PIL import Image

    from chaptertools.gui.theme import DARK, LIGHT

    image = Image.open(png).convert("RGB")
    name = str(mode).lower()
    palette = DARK if name == "dark" else LIGHT
    ground = _rgb(palette["bg"])
    black = _solid_block(image, (0, 0, 0))
    if black is not None:
        raise AssertionError(f"{name}: black area larger than 20×20 at {black}")
    if name == "dark":
        white = _solid_block(image, (255, 255, 255))
        if white is not None:
            raise AssertionError(
                f"{name}: white area larger than 20×20 at {white}")
    width, height = image.size
    pixels = image.load()
    corners = (
        (4, 4), (width - 5, 4), (4, height - 5), (width - 5, height - 5))
    for point in corners:
        if pixels[point] != ground:
            raise AssertionError(
                f"{name}: corner {point} is {pixels[point]}, not the ground")
