"""The application icon: the SVG master, the GUI's window icon and the platform files."""

import importlib.util
import os
import re
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtSvg")

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "src" / "moviemerger" / "resources" / "moviemerger.svg"
ALAKAZAM_BLUE = "#4a7fc9"


def _packaging_icons():
    """Import packaging/icons.py, which is a build script rather than a package module."""
    spec = importlib.util.spec_from_file_location("packaging_icons", ROOT / "packaging" / "icons.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up while it executes
    spec.loader.exec_module(module)
    return module


def test_svg_is_monochrome_alakazam_blue():
    colours = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", SVG.read_text()))
    assert colours == {ALAKAZAM_BLUE}


def test_gui_icon_renders_from_package_resource():
    from PySide6.QtGui import QGuiApplication

    from moviemerger.gui import app_icon

    _app = QGuiApplication.instance() or QGuiApplication([])
    icon = app_icon()
    assert not icon.isNull()
    assert {(s.width(), s.height()) for s in icon.availableSizes()} >= {(16, 16), (256, 256)}
    image = icon.pixmap(64).toImage()
    painted = sum(image.pixelColor(x, y).alpha() > 0 for x in range(64) for y in range(64))
    assert painted > 64 * 64 * 0.2


def test_platform_icon_files(tmp_path):
    icons = _packaging_icons()
    files = icons.render_icons(tmp_path)

    with Image.open(files.ico) as ico:
        assert sorted(ico.info["sizes"]) == [(s, s) for s in icons.ICO_SIZES]
        ico.size = (16, 16)
        small = ico.convert("RGBA")
        # Strongly covered pixels carry the icon blue (anti-aliasing only changes their alpha).
        strokes = [rgba[:3] for _, rgba in small.getcolors(256) if rgba[3] > 128]
        assert strokes and all(max(abs(c - b) for c, b in zip(rgb, (0x4A, 0x7F, 0xC9))) <= 2 for rgb in strokes)

    with Image.open(files.icns) as icns:
        assert (512, 512, 2) in icns.info["sizes"]
        assert icns.size == (1024, 1024)
