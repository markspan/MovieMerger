"""Mapping between LSL time and video frames.

A :class:`FrameClock` knows, for every frame in a video file, the LSL time at
which that frame was captured. Rendering then works on a regular output grid
(:func:`output_times`): each output frame shows the most recent source frame
captured at or before its time (sample-and-hold), which preserves the real
timing of irregularly delivered webcam frames.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

log = logging.getLogger(__name__)

NO_FRAME = -1
"""Frame number returned where no source frame covers an output time."""


@dataclass(frozen=True)
class TimeSpan:
    """A half-open interval ``[start, end)`` of LSL time, in seconds."""

    start: float
    end: float

    def __post_init__(self) -> None:
        if not self.end > self.start:
            raise ValueError(f"Empty time span [{self.start:.3f}, {self.end:.3f}).")

    @property
    def duration(self) -> float:
        return self.end - self.start

    @staticmethod
    def union(spans: list[TimeSpan]) -> TimeSpan:
        """Smallest span covering every span in ``spans``."""
        return TimeSpan(min(s.start for s in spans), max(s.end for s in spans))

    @staticmethod
    def intersection(spans: list[TimeSpan]) -> TimeSpan:
        """Span common to every span in ``spans``.

        Raises:
            ValueError: If the spans do not all overlap.
        """
        start = max(s.start for s in spans)
        end = min(s.end for s in spans)
        if not end > start:
            raise ValueError("The selected streams do not overlap in time.")
        return TimeSpan(start, end)


@dataclass(frozen=True)
class FrameClock:
    """Capture time of every frame of one video, on the LSL clock.

    Attributes:
        times: ``times[k]`` is the LSL time of frame ``k`` of the video file,
            or NaN when that frame has no known time (it was written before
            the first or after the last marker the recording received).
        frame_interval: Typical spacing of consecutive frames, in seconds.
            The last timed frame is held for this long.
    """

    times: np.ndarray
    frame_interval: float

    @classmethod
    def from_markers(
        cls,
        frame_indices: np.ndarray,
        time_stamps: np.ndarray,
        frame_count: int | None = None,
        latency: float = 0.0,
    ) -> FrameClock:
        """Build a clock from TimeShot's per-frame markers.

        Args:
            frame_indices: Frame index carried by each marker.
            time_stamps: LSL time of each marker.
            frame_count: Number of frames in the video file, if known. Frames
                beyond the last marker get NaN; markers beyond the end of the
                file are ignored.
            latency: Seconds by which the markers lag the actual capture.
                TimeShot stamps a frame after grabbing and encoding it, so a
                positive latency moves every frame earlier.

        Frames between two markers (a marker lost in transit) are placed by
        linear interpolation. Duplicate indices keep their first timestamp.
        """
        indices = np.asarray(frame_indices, dtype=np.int64)
        stamps = np.asarray(time_stamps, dtype=np.float64) - latency
        if indices.shape != stamps.shape:
            raise ValueError("Every frame marker needs exactly one timestamp.")
        keep = indices >= 0
        indices, stamps = indices[keep], stamps[keep]

        order = np.argsort(indices, kind="stable")
        indices, first = np.unique(indices[order], return_index=True)
        stamps = stamps[order][first]
        if indices.size < 2:
            raise ValueError("Need at least two distinct frame markers.")
        if np.any(np.diff(stamps) < 0):
            # Frames are written in index order, so their times cannot run
            # backwards; clamp rather than let a glitch reorder playback.
            log.warning("Frame markers run backwards in time; clamping to keep frames in order.")
            stamps = np.maximum.accumulate(stamps)

        size = int(indices[-1]) + 1 if frame_count is None else int(frame_count)
        if size <= 0:
            raise ValueError("The video file has no frames.")
        times = np.full(size, np.nan)
        lo, hi = int(indices[0]), min(int(indices[-1]), size - 1)
        if hi < lo:
            raise ValueError(f"The markers refer to frames {lo}-{indices[-1]}, but the video has only {size}.")
        known = np.arange(lo, hi + 1)
        times[lo : hi + 1] = np.interp(known, indices, stamps)

        if frame_count is not None and indices[-1] >= frame_count:
            log.warning(
                "Markers reference %d frames but the video holds %d; extra markers ignored.",
                int(indices[-1]) + 1,
                frame_count,
            )
        interval = float(np.median(np.diff(stamps) / np.diff(indices)))
        if not interval > 0:
            raise ValueError("Frame markers carry no usable timing (all share one timestamp).")
        return cls(times=times, frame_interval=interval)

    @property
    def timed_frames(self) -> int:
        """Number of frames with a known capture time."""
        return int(np.count_nonzero(~np.isnan(self.times)))

    @property
    def span(self) -> TimeSpan:
        """Time from the first timed frame until the last one stops showing."""
        valid = self.times[~np.isnan(self.times)]
        return TimeSpan(float(valid[0]), float(valid[-1]) + self.frame_interval)

    @property
    def fps(self) -> float:
        """Typical frame rate, from :attr:`frame_interval`."""
        return 1.0 / self.frame_interval

    def frames_at(self, times: np.ndarray) -> np.ndarray:
        """Frame to display at each of ``times``.

        Returns:
            Array of frame numbers (int64), :data:`NO_FRAME` where ``times``
            fall before the first or after the last timed frame.
        """
        frame_numbers = np.flatnonzero(~np.isnan(self.times))
        valid_times = self.times[frame_numbers]
        slot = np.searchsorted(valid_times, times, side="right") - 1
        result = np.where(slot >= 0, frame_numbers[np.clip(slot, 0, None)], NO_FRAME)
        result[np.asarray(times) >= self.span.end] = NO_FRAME
        return result.astype(np.int64)


@dataclass(frozen=True)
class MarkerTiming:
    """How regularly a camera's frame markers arrived.

    TimeShot stamps a frame when it pushes the marker, after reading the
    frame from the driver, drawing the frame number and encoding it to disk.
    When that loop cannot keep up, frames wait in the driver's buffer and are
    then read (and stamped) in quick bursts. Burst-stamped markers no longer
    reflect when their frames were captured, so it is worth checking.

    Bursts and gaps are judged against the mean interval (the true average
    frame period), because in a bursty stream the median can itself be a
    burst interval.

    Attributes:
        mean_interval: Average time between consecutive frames, seconds.
        median_interval: Median time between consecutive frames, seconds.
        p5_interval: 5th percentile of the intervals.
        p95_interval: 95th percentile of the intervals.
        burst_fraction: Share of intervals shorter than half the mean.
        gaps: Intervals longer than 1.5 times the mean (dropped frames).
    """

    mean_interval: float
    median_interval: float
    p5_interval: float
    p95_interval: float
    burst_fraction: float
    gaps: int

    # Above this burst share, single-frame timing is not trustworthy.
    BURST_WARNING_FRACTION = 0.02

    @classmethod
    def of(cls, frame_indices: np.ndarray, time_stamps: np.ndarray) -> MarkerTiming:
        """Interval statistics of markers for consecutive frames."""
        order = np.argsort(frame_indices, kind="stable")
        indices = np.asarray(frame_indices)[order]
        stamps = np.asarray(time_stamps, dtype=np.float64)[order]
        consecutive = np.diff(indices) == 1
        intervals = np.diff(stamps)[consecutive]
        if intervals.size == 0:
            raise ValueError("No consecutive frame markers to analyse.")
        mean = float(np.mean(intervals))
        return cls(
            mean_interval=mean,
            median_interval=float(np.median(intervals)),
            p5_interval=float(np.percentile(intervals, 5)),
            p95_interval=float(np.percentile(intervals, 95)),
            burst_fraction=float(np.mean(intervals < 0.5 * mean)),
            gaps=int(np.count_nonzero(intervals > 1.5 * mean)),
        )

    @property
    def bursty(self) -> bool:
        return self.burst_fraction > self.BURST_WARNING_FRACTION

    def summary(self) -> str:
        return (
            f"frame interval mean {self.mean_interval * 1000:.1f} ms, median {self.median_interval * 1000:.1f} ms "
            f"(5-95%: {self.p5_interval * 1000:.1f}-{self.p95_interval * 1000:.1f} ms), "
            f"{self.burst_fraction:.1%} burst-read, {self.gaps} gap(s)"
        )


def output_times(span: TimeSpan, fps: float) -> np.ndarray:
    """Times of the frames of a constant-rate output covering ``span``."""
    if fps <= 0:
        raise ValueError("Output frame rate must be positive.")
    count = max(1, round(span.duration * fps))
    return span.start + np.arange(count) / fps
