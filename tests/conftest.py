"""Synthetic TimeShot recordings for end-to-end tests.

The scene: two cameras and a microphone record one event, a flash with a
simultaneous beep at LSL time ``EVENT_TIME``. The recording is deliberately
hostile to naive synchronisation:

* camera A delivers frames irregularly, with a long stall, while its AVI
  claims a flat 30 fps;
* camera B starts later and runs at a different rate;
* the microphone's clock runs 1 % fast relative to LSL and drops out for
  half a second before the event.

A correct merge shows the flash and the beep together; any approach that
trusts nominal rates or sample counts misses by hundreds of milliseconds.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from xdf_writer import XdfStream, write_xdf

EVENT_TIME = 1008.0
FLASH_LEVEL = 250
DARK_LEVEL = 40
AUDIO_RATE = 8000
BEEP_SECONDS = 0.2

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg is not installed"
)


@dataclass(frozen=True)
class Recording:
    xdf: Path
    video_a: Path
    video_b: Path
    frame_times_a: np.ndarray
    frame_times_b: np.ndarray


def _camera_times(
    rng: np.random.Generator, start: float, fps: float, duration: float, stall_at: float | None
) -> np.ndarray:
    """Irregular frame times: jittered intervals plus an optional 400 ms stall."""
    intervals = 1.0 / fps + rng.uniform(-0.25, 0.25, int(duration * fps * 1.2)) / fps
    times = start + np.concatenate([[0.0], np.cumsum(intervals)])
    if stall_at is not None:
        times[times > stall_at] += 0.4
    return times[times < start + duration]


def _write_video(path: Path, frame_times: np.ndarray, size: tuple[int, int] = (160, 120)) -> None:
    """MJPEG AVI whose frames are dark, except the one showing at EVENT_TIME."""
    width, height = size
    flash = int(np.searchsorted(frame_times, EVENT_TIME, side="right") - 1)
    process = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}",
         "-framerate", "30", "-i", "-", "-c:v", "mjpeg", "-q:v", "3", str(path)],
        stdin=subprocess.PIPE,
    )  # fmt: skip
    for k in range(frame_times.size):
        level = FLASH_LEVEL if k == flash else DARK_LEVEL
        process.stdin.write(np.full((height, width), level, np.uint8).tobytes())
    process.stdin.close()
    assert process.wait() == 0


def _audio(rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Microphone samples and timestamps: 1 % fast clock, dropout before the event."""
    real_rate = AUDIO_RATE * 1.01
    stamps = 995.0 + np.arange(int(25 * real_rate)) / real_rate
    keep = ~((stamps > EVENT_TIME - 2.0) & (stamps < EVENT_TIME - 1.5))
    stamps = stamps[keep]
    beep = (stamps >= EVENT_TIME) & (stamps < EVENT_TIME + BEEP_SECONDS)
    samples = np.where(beep, 0.8 * np.sin(2 * np.pi * 440 * stamps), 0.0) + rng.normal(0, 0.001, stamps.size)
    return stamps, (samples * 32767).astype(np.int16)[:, np.newaxis]


@pytest.fixture(scope="session")
def recording(tmp_path_factory: pytest.TempPathFactory) -> Recording:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is not installed")
    folder = tmp_path_factory.mktemp("recording")
    rng = np.random.default_rng(7)
    times_a = _camera_times(rng, start=1000.0, fps=25.0, duration=14.0, stall_at=1005.0)
    times_b = _camera_times(rng, start=1003.0, fps=15.0, duration=9.0, stall_at=None)
    video_a, video_b = folder / "Video_0.avi", folder / "Video_1.avi"
    _write_video(video_a, times_a)
    _write_video(video_b, times_b, size=(160, 90))
    audio_stamps, audio_samples = _audio(rng)

    def camera(name: str, video: Path, times: np.ndarray) -> XdfStream:
        return XdfStream(
            name=name, type="Markers", channel_format="string", nominal_srate=0, time_stamps=times,
            values=[str(k) for k in range(times.size)],
            desc={"video_file": video.name, "camera_name": f"{name} camera", "measured_fps": "30.00"},
        )  # fmt: skip

    write_xdf(
        folder / "session.xdf",
        [
            # Listed out of file order on purpose, to exercise matching.
            camera("Stream_1", video_b, times_b),
            XdfStream("Mic", "Audio", "int16", AUDIO_RATE, audio_stamps, audio_samples),
            XdfStream("Triggers", "Markers", "string", 0, np.array([1001.0, 1002.0]), ["start", "stop"]),
            camera("Stream_0", video_a, times_a),
        ],
    )
    return Recording(folder / "session.xdf", video_a, video_b, times_a, times_b)


def decode_gray(path: Path, width: int, height: int, crop: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """Mean brightness of every frame of ``path`` (optionally within a crop x, y, w, h)."""
    vf = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"{vf}format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    w, h = (crop[2], crop[3]) if crop else (width, height)
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).mean(axis=(1, 2))


def decode_audio(path: Path, rate: int = AUDIO_RATE) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(rate), "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, "<f4")


def onset_seconds(signal: np.ndarray, rate: float, threshold: float) -> float:
    """Time of the first value of ``signal`` above ``threshold``."""
    above = np.flatnonzero(signal > threshold)
    assert above.size, "event not found"
    return above[0] / rate
