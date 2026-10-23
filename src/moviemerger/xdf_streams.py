"""Discovery and loading of the camera and audio streams in an XDF file.

Two kinds of LSL stream matter here:

Camera streams
    Written by TimeShot: one string channel, one sample per recorded video
    frame, whose payload is the zero-based index of that frame in the video
    file. The stream description carries ``video_file``, ``camera_name`` and
    ``measured_fps``. Older TimeShot builds lacked the description, so a
    stream is also accepted when every payload parses as an integer.

Audio streams
    Any numeric stream whose type is ``Audio`` (as sent by AudioStream), or
    whose name mentions audio.

Only those candidate streams are loaded, so EEG or other bulky streams in the
same recording cost nothing beyond the header scan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyxdf

log = logging.getLogger(__name__)

# Timestamp gaps longer than this split an audio stream into separately
# dejittered segments, so a dropout becomes silence instead of being smeared
# over its neighbours. pyxdf's default (1 s) would hide short dropouts.
_JITTER_BREAK_SECONDS = 0.1

# Peak above which float audio cannot be in the conventional [-1, 1] range
# and is assumed to carry 16-bit integer magnitudes instead.
_INT16_FULL_SCALE = 32768.0


@dataclass(frozen=True)
class CameraStream:
    """Frame markers of one TimeShot camera.

    Attributes:
        name: LSL stream name.
        video_file: Video file name TimeShot advertised, if any. TimeShot
            stores it as given in its UI, usually without a directory.
        camera_name: Human-readable camera name TimeShot advertised, if any.
        measured_fps: Frame rate TimeShot measured when opening the camera.
        frame_indices: Frame index carried by each marker (int64).
        time_stamps: LSL time of each marker in seconds (float64), on the
            recording's common clock.
    """

    name: str
    video_file: str | None
    camera_name: str | None
    measured_fps: float | None
    frame_indices: np.ndarray
    time_stamps: np.ndarray

    @property
    def display_name(self) -> str:
        """Best label for this camera: its camera name, else its stream name."""
        return self.camera_name or self.name


@dataclass(frozen=True)
class AudioStream:
    """One audio stream, normalised to float32 samples in [-1, 1].

    Attributes:
        name: LSL stream name.
        sample_rate: Nominal sampling rate in Hz.
        samples: Array of shape (n_samples, n_channels), float32.
        time_stamps: LSL time of each sample in seconds (float64), dejittered
            by pyxdf per contiguous segment.
    """

    name: str
    sample_rate: float
    samples: np.ndarray
    time_stamps: np.ndarray

    @property
    def channel_count(self) -> int:
        return int(self.samples.shape[1])


@dataclass(frozen=True)
class XdfRecording:
    """The camera and audio streams found in one XDF file."""

    path: Path
    cameras: tuple[CameraStream, ...]
    audio: tuple[AudioStream, ...]

    def camera(self, name: str) -> CameraStream:
        """Return the camera stream called ``name``.

        Raises:
            KeyError: If no camera stream has that name.
        """
        for stream in self.cameras:
            if stream.name == name:
                return stream
        raise KeyError(f"No camera stream named {name!r} in {self.path.name}.")

    def audio_stream(self, name: str) -> AudioStream:
        """Return the audio stream called ``name``.

        Raises:
            KeyError: If no audio stream has that name.
        """
        for stream in self.audio:
            if stream.name == name:
                return stream
        raise KeyError(f"No audio stream named {name!r} in {self.path.name}.")


def load_recording(path: str | Path) -> XdfRecording:
    """Load the camera and audio streams of an XDF file.

    Args:
        path: The ``.xdf`` file written by LabRecorder.

    Returns:
        The recording, with cameras and audio streams in file order.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the file holds neither camera nor audio streams.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(path)

    headers = pyxdf.resolve_streams(str(path))
    wanted = [h["stream_id"] for h in headers if _is_camera_candidate(h) or _is_audio_candidate(h)]
    if not wanted:
        raise ValueError(f"{path.name} contains no camera-marker or audio streams.")

    streams, _ = pyxdf.load_xdf(
        str(path),
        select_streams=wanted,
        jitter_break_threshold_seconds=_JITTER_BREAK_SECONDS,
    )

    cameras: list[CameraStream] = []
    audio: list[AudioStream] = []
    for stream in streams:
        info = stream["info"]
        if _info_value(info, "channel_format") == "string":
            camera = _camera_from_stream(stream)
            if camera is not None:
                cameras.append(camera)
        else:
            parsed = _audio_from_stream(stream)
            if parsed is not None:
                audio.append(parsed)

    if not cameras and not audio:
        raise ValueError(f"{path.name} contains no usable camera-marker or audio streams.")
    log.info("Loaded %s: %d camera stream(s), %d audio stream(s).", path.name, len(cameras), len(audio))
    return XdfRecording(path=path, cameras=tuple(cameras), audio=tuple(audio))


# ── stream classification ────────────────────────────────────────────────


def _is_camera_candidate(header: dict[str, Any]) -> bool:
    return header.get("channel_format") == "string" and int(header.get("channel_count", 0)) == 1


def _is_audio_candidate(header: dict[str, Any]) -> bool:
    if header.get("channel_format") == "string":
        return False
    kind = str(header.get("type", "")).lower()
    name = str(header.get("name", "")).lower()
    return kind == "audio" or "audio" in name


def _camera_from_stream(stream: dict[str, Any]) -> CameraStream | None:
    """Build a :class:`CameraStream`, or return None for non-camera markers."""
    info = stream["info"]
    name = _info_value(info, "name") or "unnamed"
    desc = _desc(info)
    video_file = _desc_value(desc, "video_file")

    payloads = [str(sample[0]).strip() for sample in stream["time_series"]]
    try:
        indices = np.array([int(p) for p in payloads], dtype=np.int64)
    except ValueError:
        if video_file is not None:
            log.warning("Camera stream %r has non-integer markers; skipped.", name)
        return None
    if indices.size < 2:
        log.warning("Stream %r has fewer than two frame markers; skipped.", name)
        return None

    measured = _desc_value(desc, "measured_fps")
    try:
        measured_fps = float(measured) if measured is not None else None
    except ValueError:
        measured_fps = None

    return CameraStream(
        name=name,
        video_file=video_file,
        camera_name=_desc_value(desc, "camera_name"),
        measured_fps=measured_fps,
        frame_indices=indices,
        time_stamps=np.asarray(stream["time_stamps"], dtype=np.float64),
    )


def _audio_from_stream(stream: dict[str, Any]) -> AudioStream | None:
    info = stream["info"]
    name = _info_value(info, "name") or "unnamed"
    stamps = np.asarray(stream["time_stamps"], dtype=np.float64)
    raw = np.asarray(stream["time_series"])
    if stamps.size < 2 or raw.size == 0:
        log.warning("Audio stream %r is empty; skipped.", name)
        return None
    if raw.ndim == 1:
        raw = raw[:, np.newaxis]

    nominal = float(_info_value(info, "nominal_srate") or 0.0)
    if nominal <= 0:
        nominal = float(stream["info"].get("effective_srate") or 0.0)
    if nominal <= 0:
        nominal = (stamps.size - 1) / (stamps[-1] - stamps[0])
        log.warning("Audio stream %r has no nominal rate; estimated %.1f Hz.", name, nominal)

    return AudioStream(name=name, sample_rate=nominal, samples=normalise_samples(raw, name), time_stamps=stamps)


def normalise_samples(raw: np.ndarray, name: str = "audio") -> np.ndarray:
    """Scale raw audio samples to float32 in [-1, 1].

    Integer samples are scaled by their type's full scale. Float samples are
    left alone when already within [-1, 1]; floats carrying 16-bit magnitudes
    (a common shortcut in audio senders) are scaled as such, and anything
    louder is peak-normalised with a warning.
    """
    if np.issubdtype(raw.dtype, np.integer):
        return (raw.astype(np.float32) / float(np.iinfo(raw.dtype).max)).clip(-1.0, 1.0)

    samples = raw.astype(np.float32, copy=False)
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak <= 1.0:
        return samples
    if peak <= _INT16_FULL_SCALE * 1.01:
        return (samples / _INT16_FULL_SCALE).clip(-1.0, 1.0)
    log.warning("Audio stream %r peaks at %.3g; peak-normalised.", name, peak)
    return samples / peak


# ── pyxdf info helpers ───────────────────────────────────────────────────
# pyxdf stores every XML element as a list holding one value (or None).


def _info_value(info: dict[str, Any], key: str) -> str | None:
    value = info.get(key)
    if isinstance(value, list):
        value = value[0] if value else None
    return None if value is None else str(value)


def _desc(info: dict[str, Any]) -> dict[str, Any]:
    desc = info.get("desc")
    if isinstance(desc, list):
        desc = desc[0] if desc else None
    return desc if isinstance(desc, dict) else {}


def _desc_value(desc: dict[str, Any], key: str) -> str | None:
    value = desc.get(key)
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None or isinstance(value, dict):
        return None
    text = str(value).strip()
    return text or None
