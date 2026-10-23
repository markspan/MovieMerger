"""High-level export jobs: synchronised single videos and mosaics.

Both jobs share one renderer. It walks a constant-rate output timeline, asks
each video's :class:`~moviemerger.timing.FrameClock` which source frame was
on screen at that moment, composes the frames into the output canvas and
streams the result to ffmpeg alongside the audio rendered for the same span.
A synchronised single video is simply a one-tile mosaic at native size.

All jobs accept a progress callback and a cancellation event so that the GUI
can drive them from a worker thread.
"""

from __future__ import annotations

import logging
import math
import re
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PureWindowsPath

import numpy as np

from .audio import RenderedAudio, render_audio
from .mosaic import Label, Layout
from .timing import NO_FRAME, FrameClock, MarkerTiming, TimeSpan, output_times
from .video import EncoderSettings, FrameReader, VideoEncoder, VideoInfo, probe_video
from .xdf_streams import AudioStream, CameraStream

log = logging.getLogger(__name__)

ProgressCallback = Callable[[float, str], None]
"""Receives overall progress in [0, 1] and a short status message."""

# Share of a job's progress bar spent rendering audio (video dominates).
_AUDIO_PROGRESS_SHARE = 0.05

# Frame rates within this fraction of a whole number are rounded to it.
_FPS_ROUNDING_TOLERANCE = 0.015


class Cancelled(Exception):
    """Raised when a job stops because its cancel event was set."""


class SpanMode(str, Enum):
    """Which stretch of time an export covers.

    ``UNION`` keeps everything: a single video spans its own recording, a
    mosaic spans from the first camera's start to the last camera's end, and
    missing pictures or sound are filled with black or silence.
    ``OVERLAP`` keeps only the time during which every selected video and
    the audio were all recording.
    """

    UNION = "union"
    OVERLAP = "overlap"


@dataclass(frozen=True)
class VideoInput:
    """A video file paired with the XDF stream holding its frame markers.

    Attributes:
        path: The video file TimeShot wrote (or a frame-exact conversion).
        stream: Its TimeShot marker stream.
        label: Caption for the mosaic; defaults to the camera's name.
        latency: Seconds by which this camera's markers lag the capture.
    """

    path: Path
    stream: CameraStream
    label: str | None = None
    latency: float = 0.0

    @property
    def caption(self) -> str:
        return self.label if self.label is not None else self.stream.display_name


@dataclass(frozen=True)
class RenderOptions:
    """Settings shared by all exports.

    Attributes:
        fps: Output frame rate; None picks :func:`default_output_fps`.
        span_mode: Which stretch of time to export.
        audio_latency: Seconds by which audio timestamps lag the sound.
        labels: Whether to caption each tile of a mosaic.
        mosaic_width: Mosaic width in pixels; the height follows the layout.
        columns: Mosaic tiles per row; None picks the most square grid.
        encoder: Codec settings.
    """

    fps: float | None = None
    span_mode: SpanMode = SpanMode.UNION
    audio_latency: float = 0.0
    labels: bool = True
    mosaic_width: int = 1920
    columns: int | None = None
    encoder: EncoderSettings = field(default_factory=EncoderSettings)


# ── matching videos to streams ───────────────────────────────────────────


def match_videos(paths: Sequence[Path], cameras: Sequence[CameraStream]) -> list[CameraStream | None]:
    """Guess which camera stream belongs to each video file.

    Tried in order, each stream being used at most once:

    1. The file name equals the ``video_file`` TimeShot advertised.
    2. The file stem equals it (the video was converted, e.g. AVI to MP4).
    3. The stream or camera name appears in the file stem.
    4. Remaining files take remaining streams in order.

    Returns:
        One entry per path; None when streams ran out.
    """
    result: list[CameraStream | None] = [None] * len(paths)
    free = list(cameras)

    def advertised(camera: CameraStream) -> PureWindowsPath | None:
        # PureWindowsPath splits on both "/" and "\", whatever the host OS.
        return PureWindowsPath(camera.video_file) if camera.video_file else None

    rules: list[Callable[[Path, CameraStream], bool]] = [
        lambda p, c: (a := advertised(c)) is not None and a.name.lower() == p.name.lower(),
        lambda p, c: (a := advertised(c)) is not None and a.stem.lower() == p.stem.lower(),
        lambda p, c: any(_contains_token(p.stem, name) for name in (c.name, c.camera_name) if name),
    ]
    for rule in rules:
        for i, path in enumerate(paths):
            if result[i] is not None:
                continue
            match = next((c for c in free if rule(Path(path), c)), None)
            if match is not None:
                result[i] = match
                free.remove(match)
    for i in range(len(paths)):
        if result[i] is None and free:
            result[i] = free.pop(0)
    return result


def _contains_token(text: str, token: str) -> bool:
    """True if ``token`` occurs in ``text`` not flanked by letters or digits."""
    pattern = rf"(?<![0-9a-z]){re.escape(token.lower())}(?![0-9a-z])"
    return re.search(pattern, text.lower()) is not None


def default_output_fps(streams: Sequence[CameraStream]) -> float:
    """A sensible constant output rate for ``streams``.

    The fastest camera's typical rate, so no camera loses frames, rounded to
    a whole number when it is within 1.5 % of one.
    """
    if not streams:
        raise ValueError("No camera streams given.")
    fps = max(FrameClock.from_markers(s.frame_indices, s.time_stamps).fps for s in streams)
    whole = round(fps)
    if whole > 0 and abs(fps - whole) / whole <= _FPS_ROUNDING_TOLERANCE:
        return float(whole)
    return round(fps, 2)


# ── export jobs ──────────────────────────────────────────────────────────


def export_synced_video(
    video: VideoInput,
    audio: AudioStream | None,
    output: Path,
    options: RenderOptions | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Write ``video`` retimed by its markers, with ``audio`` synchronised.

    Args:
        video: The video and its marker stream.
        audio: Scene audio to add, or None for a silent retimed video.
        output: Destination ``.mp4``.
        options: Export settings (mosaic settings are ignored); defaults if None.
        progress: Optional progress callback.
        cancel: Optional event that aborts the job when set.

    Returns:
        ``output``.

    Raises:
        Cancelled: If ``cancel`` was set; the partial output is deleted.
    """
    options = options or RenderOptions()
    prepared = _PreparedVideo.load(video)
    span = prepared.clock.span
    if options.span_mode is SpanMode.OVERLAP and audio is not None:
        span = TimeSpan.intersection([span, _audio_span(audio, options.audio_latency)])
    fps = options.fps or default_output_fps([video.stream])
    layout = Layout.single(prepared.info.width, prepared.info.height)
    _render([prepared], layout, span, fps, audio, output, options, labels=False, progress=progress, cancel=cancel)
    return output


def export_mosaic(
    videos: Sequence[VideoInput],
    audio: AudioStream | None,
    output: Path,
    options: RenderOptions | None = None,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Write a grid of ``videos`` with ``audio`` synchronised.

    Args:
        videos: Videos in the order they fill the grid (row by row).
        audio: Scene audio to add, or None for a silent mosaic.
        output: Destination ``.mp4``.
        options: Export settings; defaults if None.
        progress: Optional progress callback.
        cancel: Optional event that aborts the job when set.

    Returns:
        ``output``.

    Raises:
        Cancelled: If ``cancel`` was set; the partial output is deleted.
    """
    if not videos:
        raise ValueError("Select at least one video for the mosaic.")
    options = options or RenderOptions()
    prepared = [_PreparedVideo.load(v) for v in videos]
    spans = [p.clock.span for p in prepared]
    if options.span_mode is SpanMode.OVERLAP:
        if audio is not None:
            spans.append(_audio_span(audio, options.audio_latency))
        span = TimeSpan.intersection(spans)
    else:
        span = TimeSpan.union(spans)

    fps = options.fps or default_output_fps([v.stream for v in videos])
    aspect = max(p.info.aspect for p in prepared)
    layout = Layout.grid(len(prepared), aspect, options.mosaic_width, options.columns)
    _render(prepared, layout, span, fps, audio, output, options, options.labels, progress, cancel)
    return output


# ── shared renderer ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class _PreparedVideo:
    """A video input with its probed properties and frame clock."""

    input: VideoInput
    info: VideoInfo
    clock: FrameClock

    @classmethod
    def load(cls, video: VideoInput) -> _PreparedVideo:
        info = probe_video(video.path)
        stream = video.stream
        clock = FrameClock.from_markers(stream.frame_indices, stream.time_stamps, info.frame_count, video.latency)
        untimed = info.frame_count - clock.timed_frames
        if untimed > 0:
            log.warning(
                "%s: %d of %d frames have no marker in the XDF (recorded while LabRecorder was not "
                "running) and are left out.",
                video.path.name, untimed, info.frame_count,
            )  # fmt: skip
        timing = MarkerTiming.of(stream.frame_indices, stream.time_stamps)
        log.info(
            "%s <- stream %r: %d frames, %.1f s; %s.",
            video.path.name, stream.name, clock.timed_frames, clock.span.duration, timing.summary(),
        )  # fmt: skip
        if timing.bursty:
            log.warning(
                "%s: many frames were read from the driver's buffer in bursts, so their markers lag "
                "their capture by a varying amount. Expect frame-level timing errors up to the burst length.",
                video.path.name,
            )  # fmt: skip
        return cls(input=video, info=info, clock=clock)


def _audio_span(audio: AudioStream, latency: float) -> TimeSpan:
    stamps = audio.time_stamps - latency
    return TimeSpan(float(stamps[0]), float(stamps[-1]) + 1.0 / audio.sample_rate)


def _render(
    videos: Sequence[_PreparedVideo],
    layout: Layout,
    span: TimeSpan,
    fps: float,
    audio: AudioStream | None,
    output: Path,
    options: RenderOptions,
    labels: bool,
    progress: ProgressCallback | None,
    cancel: threading.Event | None,
) -> None:
    report = progress or (lambda fraction, message: None)
    cancel = cancel or threading.Event()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    times = output_times(span, fps)
    selections = [video.clock.frames_at(times) for video in videos]
    captions = [Label.render(v.input.caption, tile.height) if labels else None for v, tile in zip(videos, layout.tiles)]
    log.info(
        "Rendering %s: %dx%d, %.6g fps, %d frames (%.1f s).",
        output.name, layout.width, layout.height, fps, times.size, span.duration,
    )  # fmt: skip

    with tempfile.TemporaryDirectory(prefix="moviemerger-") as scratch:
        rendered: RenderedAudio | None = None
        if audio is not None:
            report(0.0, f"Rendering audio for {output.name}")
            rendered = render_audio(audio, span, Path(scratch) / "audio.f32", options.audio_latency, cancel)
            if cancel.is_set():
                raise Cancelled()

        readers = [
            FrameReader(v.input.path, tile.width, tile.height, v.info.frame_count)
            for v, tile in zip(videos, layout.tiles)
        ]
        encoder = VideoEncoder(
            output, layout.width, layout.height, fps,
            rendered.ffmpeg_input_args() if rendered else None, options.encoder,
        )  # fmt: skip
        try:
            canvas = np.zeros((layout.height, layout.width, 3), np.uint8)
            report_every = max(1, math.ceil(fps))
            for k in range(times.size):
                if cancel.is_set():
                    raise Cancelled()
                for reader, tile, selection, caption in zip(readers, layout.tiles, selections, captions):
                    frame = reader.frame(int(selection[k])) if selection[k] != NO_FRAME else None
                    target = canvas[tile.y : tile.y + tile.height, tile.x : tile.x + tile.width]
                    if frame is None:
                        target[:] = 0
                    else:
                        target[:] = frame
                    if caption is not None:
                        caption.draw(canvas, tile)
                encoder.write(canvas)
                if k % report_every == 0:
                    fraction = _AUDIO_PROGRESS_SHARE + (1 - _AUDIO_PROGRESS_SHARE) * k / times.size
                    report(fraction, f"{output.name}: {k / fps:.0f} of {span.duration:.0f} s")
            encoder.finish()
        except BaseException:
            encoder.abort()
            raise
        finally:
            for reader in readers:
                reader.close()

    report(1.0, f"Wrote {output.name}")
    log.info("Wrote %s", output)
