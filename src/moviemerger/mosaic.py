"""Grid layout and label overlays for the mosaic output."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class Tile:
    """Position and size of one video inside the output canvas, in pixels."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class Layout:
    """Arrangement of ``len(tiles)`` equally sized tiles on a canvas."""

    width: int
    height: int
    tiles: tuple[Tile, ...]

    @classmethod
    def grid(cls, count: int, tile_aspect: float, canvas_width: int, columns: int | None = None) -> Layout:
        """Lay ``count`` tiles out in rows of ``columns``.

        Args:
            count: Number of videos.
            tile_aspect: Width / height of each tile, normally the widest
                source's aspect ratio so nothing is cropped.
            canvas_width: Output width in pixels; the height follows.
            columns: Tiles per row; defaults to the most square grid.

        Tile and canvas sizes are rounded to even numbers, as H.264 requires.
        """
        if count < 1:
            raise ValueError("A mosaic needs at least one video.")
        columns = columns or math.ceil(math.sqrt(count))
        columns = max(1, min(columns, count))
        rows = math.ceil(count / columns)
        tile_w = _even(canvas_width / columns)
        tile_h = _even(tile_w / tile_aspect)
        tiles = tuple(
            Tile(x=(i % columns) * tile_w, y=(i // columns) * tile_h, width=tile_w, height=tile_h) for i in range(count)
        )
        return cls(width=tile_w * columns, height=tile_h * rows, tiles=tiles)

    @classmethod
    def single(cls, width: int, height: int) -> Layout:
        """One full-canvas tile at (even-rounded) native size."""
        width, height = _even(width), _even(height)
        return cls(width=width, height=height, tiles=(Tile(0, 0, width, height),))


def _even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


@dataclass(frozen=True)
class Label:
    """A caption pre-rendered once and alpha-blended onto every frame.

    Attributes:
        rgb: Caption pixels, (h, w, 3) float32.
        alpha: Opacity per pixel, (h, w, 1) float32 in [0, 1].
    """

    rgb: np.ndarray
    alpha: np.ndarray

    @classmethod
    def render(cls, text: str, tile_height: int) -> Label:
        """Render white ``text`` on a translucent dark box, sized to the tile."""
        size = max(12, tile_height // 24)
        font = _font(size)
        padding = max(3, size // 3)
        left, top, right, bottom = font.getbbox(text)
        width, height = right - left + 2 * padding, bottom - top + 2 * padding
        image = Image.new("RGBA", (width, height), (0, 0, 0, 150))
        ImageDraw.Draw(image).text((padding - left, padding - top), text, font=font, fill=(255, 255, 255, 255))
        pixels = np.asarray(image, dtype=np.float32)
        return cls(rgb=pixels[..., :3], alpha=pixels[..., 3:] / 255.0)

    def draw(self, canvas: np.ndarray, tile: Tile) -> None:
        """Blend this label into the top-left corner of ``tile`` on ``canvas``."""
        margin = max(2, tile.height // 60)
        h = min(self.rgb.shape[0], tile.height - margin)
        w = min(self.rgb.shape[1], tile.width - margin)
        if h <= 0 or w <= 0:
            return
        y, x = tile.y + margin, tile.x + margin
        region = canvas[y : y + h, x : x + w].astype(np.float32)
        alpha = self.alpha[:h, :w]
        canvas[y : y + h, x : x + w] = (region * (1 - alpha) + self.rgb[:h, :w] * alpha).astype(np.uint8)


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size)
