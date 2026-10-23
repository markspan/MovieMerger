"""MovieMerger: synchronise LSL-timestamped videos with XDF audio.

The package turns a LabRecorder ``.xdf`` file plus the video files that
TimeShot wrote into

* one video per camera with the scene audio synchronised to it, and
* a mosaic of all cameras with the synchronised audio.

Synchronisation is driven entirely by LSL timestamps: every video frame is
placed at the time its TimeShot marker was stamped, and every audio sample at
its own (dejittered) XDF timestamp. Nominal frame rates stored in the video
files are never trusted, because webcams deliver frames irregularly.

The public API is re-exported here; see :mod:`moviemerger.pipeline` for the
high-level entry points.
"""

from .pipeline import (
    Cancelled,
    RenderOptions,
    SpanMode,
    VideoInput,
    default_output_fps,
    export_mosaic,
    export_synced_video,
    match_videos,
)
from .xdf_streams import AudioStream, CameraStream, XdfRecording, load_recording

__all__ = [
    "AudioStream",
    "CameraStream",
    "Cancelled",
    "RenderOptions",
    "SpanMode",
    "VideoInput",
    "XdfRecording",
    "default_output_fps",
    "export_mosaic",
    "export_synced_video",
    "load_recording",
    "match_videos",
]

__version__ = "2.0.0"
