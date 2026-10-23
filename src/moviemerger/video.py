"""Thin, explicit wrappers around the ffmpeg command-line tool.

Decoding and encoding run in ffmpeg subprocesses that exchange raw RGB24
frames with Python through pipes. That keeps frame selection and compositing
in plain numpy, where the timing logic is easy to verify, while ffmpeg does
the codec work it is good at.

Only the ``ffmpeg`` executable is needed (no ``ffprobe``). It is found, in
order of preference, via:

1. the ``MOVIEMERGER_FFMPEG`` environment variable;
2. the static build shipped in the ``imageio-ffmpeg`` package, a dependency
   of MovieMerger and the copy bundled into the compiled releases, known to
   include the H.264 (libx264) and AAC encoders;
3. ``ffmpeg`` on ``PATH``, for platforms ``imageio-ffmpeg`` has no build for.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Lines of ffmpeg's stderr kept for error messages.
_STDERR_TAIL_LINES = 20

# Prevents a console window flashing up per subprocess on Windows GUIs.
_POPEN_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

FFMPEG_ENV_VAR = "MOVIEMERGER_FFMPEG"

# The header line in which ffmpeg's framecrc muxer reports a stream's size.
_FRAMECRC_DIMENSIONS = re.compile(rb"^#dimensions \d+:\s*(\d+)x(\d+)", re.MULTILINE)


class FfmpegError(RuntimeError):
    """An ffmpeg invocation failed, or ffmpeg could not be found."""


@cache
def ffmpeg_binary() -> str:
    """Path of the ffmpeg executable (see the module docstring for the search order).

    Raises:
        FfmpegError: If ffmpeg cannot be found.
    """
    configured = os.environ.get(FFMPEG_ENV_VAR)
    if configured:
        return configured
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError) as error:
        log.debug("No ffmpeg from imageio-ffmpeg (%s); trying PATH.", error)
    found = shutil.which("ffmpeg")
    if found is None:
        raise FfmpegError(f"ffmpeg was not found. Install imageio-ffmpeg or ffmpeg, or set {FFMPEG_ENV_VAR}.")
    return found


@dataclass(frozen=True)
class VideoInfo:
    """Properties of the first video stream of a file."""

    width: int
    height: int
    frame_count: int

    @property
    def aspect(self) -> float:
        return self.width / self.height


def probe_video(path: Path) -> VideoInfo:
    """Read size and exact frame count of the first video stream in ``path``.

    The stream is copied, without decoding, into ffmpeg's ``framecrc``
    muxer, which writes a header with the stream's dimensions and then one
    line per packet. Counting packets is exact where the container's own
    frame count is often wrong (AVI writers rarely update it), and for the
    intra-only codecs TimeShot writes (MJPEG) one packet is one frame.

    Raises:
        FfmpegError: If the file cannot be read or has no video stream.
    """
    command = [
        ffmpeg_binary(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path),
        "-map", "0:v:0", "-c", "copy", "-f", "framecrc", "-",
    ]  # fmt: skip
    result = subprocess.run(command, capture_output=True, check=False, creationflags=_POPEN_FLAGS)
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        raise FfmpegError(f"ffmpeg could not read the video stream of {path.name}: {message}")
    size = _FRAMECRC_DIMENSIONS.search(result.stdout)
    if size is None:
        raise FfmpegError(f"{path.name} contains no video stream with known dimensions.")
    packets = sum(1 for line in result.stdout.splitlines() if line and not line.startswith(b"#"))
    return VideoInfo(width=int(size[1]), height=int(size[2]), frame_count=packets)


class _Subprocess:
    """An ffmpeg subprocess whose stderr is drained in the background.

    Draining stops ffmpeg blocking on a full stderr pipe and keeps the last
    lines for error messages.
    """

    def __init__(self, command: list[str], *, stdin: bool, stdout: bool) -> None:
        log.debug("Running %s", subprocess.list2cmdline(command))
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
            stdout=subprocess.PIPE if stdout else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=_POPEN_FLAGS,
        )
        self._stderr: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
        self._drain = threading.Thread(target=self._drain_stderr, daemon=True)
        self._drain.start()

    def _drain_stderr(self) -> None:
        assert self._process.stderr is not None
        for line in iter(self._process.stderr.readline, b""):
            self._stderr.append(line.decode(errors="replace").rstrip())

    @property
    def stdin(self):
        return self._process.stdin

    @property
    def stdout(self):
        return self._process.stdout

    def error_text(self) -> str:
        self._drain.join(timeout=1.0)
        return "\n".join(self._stderr)

    def wait(self) -> int:
        code = self._process.wait()
        self._drain.join(timeout=1.0)
        return code

    def kill(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
        self._process.wait()


class FrameReader:
    """Sequential access to the frames of a video, scaled to a fixed size.

    Each frame is letterboxed into ``width`` x ``height`` (aspect ratio kept,
    black bars added) so it can be pasted straight into a mosaic tile.

    Frames must be requested in non-decreasing order, which is all playback
    needs; requesting the same frame again is free.
    """

    def __init__(self, path: Path, width: int, height: int, frame_count: int) -> None:
        self.path = path
        self.width = width
        self.height = height
        self.frame_count = frame_count
        self._frame_bytes = width * height * 3
        self._position = -1
        self._current: np.ndarray | None = None
        scale = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,format=rgb24"
        )
        self._process = _Subprocess(
            [
                ffmpeg_binary(), "-nostdin", "-v", "error", "-i", str(path),
                "-map", "0:v:0", "-vf", scale, "-fps_mode", "passthrough",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            stdin=False,
            stdout=True,
        )  # fmt: skip

    def frame(self, number: int) -> np.ndarray | None:
        """Return frame ``number`` as an (height, width, 3) uint8 array.

        Returns:
            The frame, or None if the file ends before it.

        Raises:
            ValueError: If ``number`` is earlier than the last frame returned.
            FfmpegError: If decoding fails.
        """
        if number < self._position:
            raise ValueError(f"Frames must be read in order ({number} requested after {self._position}).")
        if number >= self.frame_count:
            return None
        while self._position < number:
            data = self._read_exactly(self._frame_bytes)
            if data is None:
                self.frame_count = self._position + 1
                return None
            self._position += 1
            if self._position == number:
                self._current = np.frombuffer(data, np.uint8).reshape(self.height, self.width, 3)
        return self._current

    def _read_exactly(self, size: int) -> bytes | None:
        stdout = self._process.stdout
        chunks = bytearray()
        while len(chunks) < size:
            chunk = stdout.read(size - len(chunks))
            if not chunk:
                if chunks:
                    raise FfmpegError(f"Truncated frame while decoding {self.path.name}.")
                if self._process.wait() != 0:
                    raise FfmpegError(f"Decoding {self.path.name} failed:\n{self._process.error_text()}")
                return None
            chunks += chunk
        return bytes(chunks)

    def close(self) -> None:
        """Stop the decoder."""
        self._process.kill()


@dataclass(frozen=True)
class EncoderSettings:
    """Codec settings for the output file.

    Attributes:
        crf: x264 constant rate factor; lower is better quality (18 is
            visually lossless for most footage).
        preset: x264 speed/size trade-off.
        audio_bitrate: AAC bitrate, e.g. ``"192k"``.
    """

    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"


class VideoEncoder:
    """Writes raw RGB frames, plus an optional audio file, to an MP4.

    The output uses H.264 video (yuv420p, playable everywhere) and AAC audio.
    """

    def __init__(
        self,
        path: Path,
        width: int,
        height: int,
        fps: float,
        audio_args: list[str] | None = None,
        settings: EncoderSettings | None = None,
    ) -> None:
        settings = settings or EncoderSettings()
        if width % 2 or height % 2:
            raise ValueError("H.264 output needs even frame dimensions.")
        self.path = path
        self._frame_shape = (height, width, 3)
        command = [
            ffmpeg_binary(), "-nostdin", "-y", "-v", "error",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
            "-framerate", f"{fps:.6g}", "-i", "-",
        ]  # fmt: skip
        if audio_args:
            command += audio_args
        command += ["-map", "0:v:0"]
        if audio_args:
            command += ["-map", "1:a:0", "-c:a", "aac", "-b:a", settings.audio_bitrate]
        command += [
            "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
        ]  # fmt: skip
        self._process = _Subprocess(command, stdin=True, stdout=False)

    def write(self, frame: np.ndarray) -> None:
        """Append one (height, width, 3) uint8 frame.

        Raises:
            FfmpegError: If the encoder has exited.
        """
        if frame.shape != self._frame_shape:
            raise ValueError(f"Frame shape {frame.shape} does not match encoder {self._frame_shape}.")
        try:
            self._process.stdin.write(np.ascontiguousarray(frame).tobytes())
        except (BrokenPipeError, OSError) as error:
            raise FfmpegError(f"Encoder stopped unexpectedly:\n{self._process.error_text()}") from error

    def finish(self) -> None:
        """Flush and close the output file.

        Raises:
            FfmpegError: If encoding failed.
        """
        try:
            self._process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        if self._process.wait() != 0:
            raise FfmpegError(f"Encoding {self.path.name} failed:\n{self._process.error_text()}")

    def abort(self) -> None:
        """Stop encoding and delete the incomplete output file."""
        self._process.kill()
        self.path.unlink(missing_ok=True)
