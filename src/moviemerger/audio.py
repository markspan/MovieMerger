"""Rendering an XDF audio stream onto a regular output timeline.

Audio samples are placed by their own timestamps rather than by counting
samples from the first one. That corrects for the audio device's clock
running slightly fast or slow relative to LSL, and turns dropouts (gaps in
the timestamps) into silence instead of shifting everything after them.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .timing import TimeSpan
from .xdf_streams import AudioStream

# Output samples computed per block; bounds memory for long recordings.
_BLOCK_SAMPLES = 1 << 20

# Two neighbouring samples further apart than this many sample periods are
# treated as the two sides of a dropout, and the gap is rendered silent.
_GAP_TOLERANCE_PERIODS = 2.0


@dataclass(frozen=True)
class RenderedAudio:
    """Raw audio written by :func:`render_audio`.

    The file holds interleaved little-endian float32 samples (ffmpeg's
    ``f32le`` format) starting exactly at the span's start.
    """

    path: Path
    sample_rate: int
    channels: int

    def ffmpeg_input_args(self) -> list[str]:
        """ffmpeg arguments that open this file as an input."""
        return ["-f", "f32le", "-ar", str(self.sample_rate), "-ac", str(self.channels), "-i", str(self.path)]


def render_audio(
    stream: AudioStream,
    span: TimeSpan,
    path: Path,
    latency: float = 0.0,
    cancel: threading.Event | None = None,
) -> RenderedAudio:
    """Resample ``stream`` onto a uniform grid covering ``span``.

    Args:
        stream: The audio stream to render.
        span: LSL time range of the output.
        path: Where to write the raw float32 samples.
        latency: Seconds by which the audio timestamps lag the sound. A
            positive value moves the sound earlier.
        cancel: Checked between blocks; when set, rendering stops early and
            the partially written file is left for the caller to discard.

    Returns:
        A description of the written file. Output times not covered by the
        stream (before it starts, after it ends, or inside a dropout) are
        silent.
    """
    rate = round(stream.sample_rate)
    stamps = stream.time_stamps - latency
    samples = stream.samples
    period = 1.0 / stream.sample_rate
    max_gap = _GAP_TOLERANCE_PERIODS * period
    total = max(1, round(span.duration * rate))

    with open(path, "wb") as sink:
        for first in range(0, total, _BLOCK_SAMPLES):
            if cancel is not None and cancel.is_set():
                break
            n = np.arange(first, min(first + _BLOCK_SAMPLES, total))
            block = _resample_block(stamps, samples, span.start + n / rate, period, max_gap)
            sink.write(block.astype("<f4", copy=False).tobytes())

    return RenderedAudio(path=path, sample_rate=rate, channels=stream.channel_count)


def _resample_block(
    stamps: np.ndarray, samples: np.ndarray, times: np.ndarray, period: float, max_gap: float
) -> np.ndarray:
    """Linearly interpolate ``samples`` (at ``stamps``) at ``times``.

    Args:
        stamps: Sample times, ascending.
        samples: Sample values, (len(stamps), channels).
        times: Times to evaluate at.
        period: Nominal sample period, how long a lone sample lasts.
        max_gap: Neighbours further apart than this are not interpolated.

    Returns:
        float32 array of shape (len(times), channels); zero where ``times``
        are outside the stream or inside a gap longer than ``max_gap``.
    """
    right = np.searchsorted(stamps, times, side="right")
    left = right - 1
    left_c = np.clip(left, 0, stamps.size - 1)
    right_c = np.clip(right, 0, stamps.size - 1)
    spacing = stamps[right_c] - stamps[left_c]
    since_left = times - stamps[left_c]

    # Between two close samples: interpolate. Next to a gap or the stream's
    # end, a sample still covers its own period, so hold it that long.
    interpolate = (left >= 0) & (right < stamps.size) & (spacing <= max_gap)
    hold = (left >= 0) & ~interpolate & (since_left < period)

    with np.errstate(invalid="ignore", divide="ignore"):
        weight = np.where(interpolate & (spacing > 0), since_left / spacing, 0.0)
    weight = weight[:, np.newaxis].astype(np.float32)
    out = samples[left_c] * (1.0 - weight) + samples[right_c] * weight
    out[~(interpolate | hold)] = 0.0
    return out.astype(np.float32, copy=False)
