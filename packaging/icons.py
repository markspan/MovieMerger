"""Render the MovieMerger SVG icon into the icon formats executables need.

``src/moviemerger/resources/moviemerger.svg`` is the only master image.
Windows and macOS cannot use SVG for an executable's icon, so the build
renders it into

* a Windows ``.ico`` (16 to 256 px) and
* a macOS ``.icns`` (16 to 1024 px),

drawing every size from the vector image instead of scaling one bitmap
down, which keeps the small sizes sharp. Used by ``packaging/build.py``;
run directly to inspect the results::

    python packaging/icons.py out_folder
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ICON_SVG = ROOT / "src" / "moviemerger" / "resources" / "moviemerger.svg"

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


@dataclass(frozen=True)
class IconFiles:
    """The rendered platform icons."""

    ico: Path
    icns: Path


def render_icons(out_dir: Path, svg: Path = ICON_SVG, name: str = "MovieMerger") -> IconFiles:
    """Write ``<name>.ico`` and ``<name>.icns`` rendered from ``svg`` into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    images = {size: render_svg(svg, size) for size in sorted({*ICO_SIZES, *ICNS_SIZES})}

    # Pillow takes the largest image as the base and the others, matched by
    # size, as replacements for what it would otherwise scale down.
    ico = out_dir / f"{name}.ico"
    images[max(ICO_SIZES)].save(
        ico,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=[images[s] for s in ICO_SIZES if s != max(ICO_SIZES)],
    )
    icns = out_dir / f"{name}.icns"
    images[max(ICNS_SIZES)].save(
        icns,
        format="ICNS",
        append_images=[images[s] for s in ICNS_SIZES if s != max(ICNS_SIZES)],
    )
    return IconFiles(ico=ico, icns=icns)


def render_svg(svg: Path, size: int) -> Image.Image:
    """Rasterise ``svg`` into a ``size`` x ``size`` RGBA image with Qt's SVG renderer."""
    # Qt is only needed here, so headless build machines need no display.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    _app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(svg))
    if not renderer.isValid():
        raise ValueError(f"{svg} is not a valid SVG image.")
    image = QImage(size, size, QImage.Format.Format_RGBA8888)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    # RGBA8888 is byte-ordered R, G, B, A on every platform; bytesPerLine may pad rows.
    return Image.frombuffer("RGBA", (size, size), bytes(image.constBits()), "raw", "RGBA", image.bytesPerLine(), 1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    files = render_icons(Path(sys.argv[1]))
    print(f"{files.ico}\n{files.icns}")
