#!/usr/bin/env python3
"""Build a standalone MovieMerger release for the current platform with Nuitka.

Usage::

    pip install -r packaging/requirements-build.txt
    pip install --no-deps .
    python packaging/build.py [--expect-version 2.0.0]

Steps:

1. Compile ``packaging/moviemerger_app.py`` with Nuitka into a standalone
   folder (Windows, Linux) or app bundle (macOS). Nuitka's built-in package
   configuration bundles the static ffmpeg executable of ``imageio-ffmpeg``,
   so the release needs nothing installed on the user's machine (the smoke
   test verifies this).
2. Name the result ``MovieMerger`` and add the license notices.
3. On macOS, ad-hoc sign every binary and then the bundle (Apple Silicon
   refuses to run unsigned code; this is not a notarised Developer ID).
4. Archive it as ``dist/MovieMerger-<version>-<platform>.<zip|tar.gz>``.

Under GitHub Actions the archive and executable paths are written to
``$GITHUB_OUTPUT`` as ``archive`` and ``executable``.
"""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

import moviemerger

APP_NAME = "MovieMerger"
BUNDLE_ID = "nl.rug.moviemerger"
ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "packaging" / "moviemerger_app.py"
NOTICES = ROOT / "packaging" / "THIRD-PARTY-NOTICES.txt"

# First four bytes of Mach-O executables and libraries (thin and universal).
_MACHO_MAGIC = {bytes.fromhex(m) for m in ("feedface", "feedfacf", "cefaedfe", "cffaedfe", "cafebabe", "bebafeca")}


@dataclass(frozen=True)
class Target:
    """The platform being built for."""

    system: str  # "windows", "linux" or "macos"
    arch: str  # "x64" or "arm64"

    @classmethod
    def current(cls) -> Target:
        systems = {"win32": "windows", "linux": "linux", "darwin": "macos"}
        arches = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}
        machine = platform.machine().lower()
        if sys.platform not in systems or machine not in arches:
            raise SystemExit(f"Unsupported build platform: {sys.platform}/{machine}")
        return cls(systems[sys.platform], arches[machine])

    @property
    def tag(self) -> str:
        return f"{self.system}-{self.arch}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--expect-version", help="fail unless the package version equals this (e.g. the git tag)")
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build", help="Nuitka output folder")
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist", help="folder for the release archive")
    args = parser.parse_args()

    version = moviemerger.__version__
    if args.expect_version and args.expect_version.removeprefix("v") != version:
        raise SystemExit(f"Version mismatch: tag says {args.expect_version}, package says {version}.")
    target = Target.current()
    print(f"Building {APP_NAME} {version} for {target.tag}", flush=True)

    shutil.rmtree(args.build_dir, ignore_errors=True)
    try:
        subprocess.run(nuitka_command(target, version, args.build_dir), env=_tool_env(), check=True)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"Nuitka failed with exit code {error.returncode}; see its output above.") from None

    app, executable = arrange_output(target, args.build_dir)
    if target.system == "macos":
        sign_ad_hoc(app)
    archive = make_archive(target, version, app, args.dist_dir)

    print(f"Executable: {executable}\nArchive:    {archive}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as sink:
            sink.write(f"archive={archive}\nexecutable={executable}\n")
    return 0


def nuitka_command(target: Target, version: str, build_dir: Path) -> list[str]:
    """The Nuitka invocation for ``target``."""
    command = [
        sys.executable, "-m", "nuitka",
        # A standalone folder on Windows and Linux, an app bundle on macOS.
        "--mode=app-dist",
        "--enable-plugin=pyside6",
        f"--output-dir={build_dir}",
        f"--output-folder-name={APP_NAME}",
        f"--output-filename={APP_NAME}",
        f"--report={build_dir / 'compilation-report.xml'}",
        "--assume-yes-for-downloads",
    ]  # fmt: skip
    if target.system == "windows":
        command += [
            # No console window when double-clicked, but CLI output still
            # appears when started from a terminal.
            "--windows-console-mode=attach",
            "--company-name=Mark Span",
            f"--product-name={APP_NAME}",
            f"--file-version={version}",
            f"--product-version={version}",
            "--file-description=Synchronise TimeShot videos with XDF audio",
            "--copyright=GPL-2.0-only",
        ]
    elif target.system == "macos":
        command += [
            f"--macos-app-name={APP_NAME}",
            f"--macos-app-version={version}",
            f"--macos-signed-app-name={BUNDLE_ID}",
        ]
    return [*command, str(LAUNCHER)]


def _tool_env() -> dict[str, str]:
    """Environment in which tools installed next to this Python (patchelf) are found.

    Without this, running the build with a virtual environment's Python
    without activating it would hide the pip-installed patchelf from Nuitka.
    """
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([sysconfig.get_path("scripts"), env.get("PATH", "")])
    return env


def arrange_output(target: Target, build_dir: Path) -> tuple[Path, Path]:
    """Drop the ``.dist`` suffix from Nuitka's folder and add the notices.

    Returns:
        The folder or app bundle to ship, and the executable inside it.
    """
    if target.system == "macos":
        app = build_dir / f"{APP_NAME}.app"
        with open(app / "Contents" / "Info.plist", "rb") as plist:
            executable = app / "Contents" / "MacOS" / plistlib.load(plist)["CFBundleExecutable"]
        resources = app / "Contents" / "Resources"
        resources.mkdir(exist_ok=True)
    else:
        app = build_dir / APP_NAME
        (build_dir / f"{APP_NAME}.dist").rename(app)
        executable = app / (f"{APP_NAME}.exe" if target.system == "windows" else APP_NAME)
        resources = app
    if not executable.is_file():
        raise SystemExit(f"Nuitka did not produce {executable}")
    shutil.copy2(ROOT / "LICENSE", resources / "LICENSE.txt")
    shutil.copy2(NOTICES, resources / NOTICES.name)
    return app, executable


def sign_ad_hoc(app: Path) -> None:
    """Ad-hoc sign every Mach-O file in ``app``, innermost first, then the bundle.

    Nuitka signs its own output, but adding the notices afterwards breaks
    the bundle's seal, so it is signed again.
    ``codesign --deep`` does not reliably reach executables kept in ordinary
    sub-folders (such as the bundled ffmpeg), so each binary is signed
    explicitly first.
    """
    binaries = [p for p in app.rglob("*") if p.is_file() and not p.is_symlink() and _is_macho(p)]
    for binary in sorted(binaries, key=lambda p: len(p.parts), reverse=True):
        subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(binary)], check=True)
    subprocess.run(["codesign", "--force", "--sign", "-", "--timestamp=none", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--verbose=2", str(app)], check=True)


def _is_macho(path: Path) -> bool:
    with open(path, "rb") as f:
        return f.read(4) in _MACHO_MAGIC


def make_archive(target: Target, version: str, app: Path, dist_dir: Path) -> Path:
    """Pack ``app`` into the release archive for ``target``."""
    dist_dir.mkdir(parents=True, exist_ok=True)
    base = dist_dir / f"{APP_NAME}-{version}-{target.tag}"
    if target.system == "macos":
        # ditto keeps symlinks, permissions and code signatures intact.
        archive = base.with_suffix(".zip")
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(app), str(archive)], check=True)
        return archive
    if target.system == "linux":
        # tar keeps the executable bits that zip would lose.
        return Path(shutil.make_archive(str(base), "gztar", app.parent, app.name))
    return Path(shutil.make_archive(str(base), "zip", app.parent, app.name))


if __name__ == "__main__":
    sys.exit(main())
