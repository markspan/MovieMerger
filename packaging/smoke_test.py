#!/usr/bin/env python3
"""Check a compiled MovieMerger executable end to end.

Usage::

    python packaging/smoke_test.py path/to/MovieMerger[.exe]

Runs the executable's command line on the synthetic recording from
``tests/synthetic.py`` and checks that picture and sound line up in both the
synced video and the mosaic. Any ffmpeg on ``PATH`` is hidden from the
executable, so the check fails unless the bundled ffmpeg is present and
works (including its H.264 and AAC encoders).

Needs the build environment (numpy, and the ``moviemerger`` package for
decoding the outputs), not just the compiled release.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))  # for synthetic.py and xdf_writer.py, imported below

import numpy as np

from moviemerger.mosaic import Layout
from moviemerger.video import probe_video

from synthetic import (
    AUDIO_RATE,
    EVENT_TIME,
    build_recording,
    decode_audio,
    decode_gray,
    onset_seconds,
    tile_centre_crops,
)

FPS = 25.0
TOLERANCE = 1.0 / FPS + 0.005  # see tests/test_end_to_end.py


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    executable = Path(sys.argv[1]).resolve()

    # The GUI renders its window icon from this package data file.
    icon = executable.parent / "moviemerger" / "resources" / "moviemerger.svg"
    if not icon.is_file():
        raise SystemExit(f"The release lacks the icon resource {icon}")

    with tempfile.TemporaryDirectory(prefix="moviemerger-smoke-") as scratch:
        folder = Path(scratch)
        recording = build_recording(folder)
        videos = [str(recording.video_a), str(recording.video_b)]

        print(run(executable, "--version").strip())
        print(run(executable, "streams", str(recording.xdf)))

        run(executable, "sync", str(recording.xdf), *videos, "--fps", "25", "--out-dir", str(folder / "synced"))
        synced = folder / "synced" / "Video_0_synced.mp4"
        flash = onset_seconds(decode_gray(synced, 160, 120), FPS, 150)
        beep = onset_seconds(np.abs(decode_audio(synced)), AUDIO_RATE, 0.3)
        expect_close("sync: flash vs beep", flash, beep, TOLERANCE)
        expect_close("sync: beep position", beep, EVENT_TIME - recording.frame_times_a[0], 0.005)

        mosaic = folder / "mosaic.mp4"
        run(executable, "mosaic", str(recording.xdf), *videos, "--fps", "25", "--width", "320", "-o", str(mosaic))
        info = probe_video(mosaic)
        layout = Layout.grid(2, 16 / 9, 320)
        if (info.width, info.height) != (layout.width, layout.height):
            raise SystemExit(f"mosaic is {info.width}x{info.height}, expected {layout.width}x{layout.height}")
        beep = onset_seconds(np.abs(decode_audio(mosaic)), AUDIO_RATE, 0.3)
        for i, crop in enumerate(tile_centre_crops(layout.tiles)):
            flash = onset_seconds(decode_gray(mosaic, info.width, info.height, crop), FPS, 150)
            expect_close(f"mosaic tile {i}: flash vs beep", flash, beep, TOLERANCE)

    print("Smoke test passed.")
    return 0


def run(executable: Path, *args: str) -> str:
    """Run the compiled CLI with any ffmpeg on PATH hidden; return its stdout."""
    result = subprocess.run(
        [str(executable), *args], env=_env_without_ffmpeg(), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise SystemExit(f"{executable.name} {' '.join(args)} failed ({result.returncode}):\n{result.stderr}")
    return result.stdout


def _env_without_ffmpeg() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("MOVIEMERGER_FFMPEG", "IMAGEIO_FFMPEG_EXE")}
    names = ("ffmpeg.exe", "ffmpeg") if sys.platform == "win32" else ("ffmpeg",)
    folders = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join(d for d in folders if not any((Path(d) / n).exists() for n in names))
    return env


def expect_close(what: str, actual: float, expected: float, tolerance: float) -> None:
    status = "ok" if abs(actual - expected) <= tolerance else "FAILED"
    print(f"{what}: {actual:.3f} s vs {expected:.3f} s (tolerance {tolerance:.3f}) {status}")
    if status != "ok":
        raise SystemExit(f"{what} out of tolerance")


if __name__ == "__main__":
    sys.exit(main())
