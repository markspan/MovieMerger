"""Release packaging helpers (packaging/build.py is a script, so it is loaded by path)."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def build():
    sys.path.insert(0, str(ROOT / "packaging"))  # build.py imports its sibling icons.py
    try:
        spec = importlib.util.spec_from_file_location("packaging_build", ROOT / "packaging" / "build.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # dataclasses look their module up while it executes
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(ROOT / "packaging"))


@pytest.mark.parametrize(
    ("system", "arch", "name"),
    [
        ("windows", "x64", "MovieMerger-2.0.0-windows-x64.zip"),
        ("linux", "x64", "MovieMerger-2.0.0-linux-x64.tar.gz"),
        ("macos", "x64", "MovieMerger-2.0.0-macos-x64.zip"),
    ],
)
def test_archive_names_keep_the_full_version(build, tmp_path, system, arch, name):
    assert build.archive_path(build.Target(system, arch), "2.0.0", tmp_path) == tmp_path / name


@pytest.mark.parametrize(("system", "extension"), [("windows", ".zip"), ("linux", ".tar.gz")])
def test_make_archive_writes_the_named_file(build, tmp_path, system, extension):
    app = tmp_path / "build" / "MovieMerger"
    app.mkdir(parents=True)
    (app / "MovieMerger").write_text("binary")
    archive = build.make_archive(build.Target(system, "x64"), "2.0.0", app, tmp_path / "dist")
    assert archive.name == f"MovieMerger-2.0.0-{system}-x64{extension}"
    assert archive.is_file()
