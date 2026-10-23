"""Command-line interface.

Examples::

    moviemerger streams session.xdf
    moviemerger sync session.xdf Video_0.avi Video_1.avi --out-dir synced
    moviemerger mosaic session.xdf Video_*.avi -o mosaic.mp4 --columns 2

Videos are matched to marker streams automatically (see
:func:`moviemerger.pipeline.match_videos`); override with
``--stream FILE=STREAM``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .pipeline import (
    RenderOptions,
    SpanMode,
    VideoInput,
    export_mosaic,
    export_synced_video,
    match_videos,
)
from .timing import MarkerTiming
from .video import EncoderSettings, FfmpegError
from .xdf_streams import AudioStream, load_recording

log = logging.getLogger("moviemerger")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    try:
        return args.handler(args)
    except (FileNotFoundError, KeyError, ValueError, FfmpegError) as error:
        log.error("%s", error)
        return 1
    except KeyboardInterrupt:
        log.error("Interrupted.")
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="moviemerger", description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug output, including ffmpeg commands")
    commands = parser.add_subparsers(dest="command", required=True)

    streams = commands.add_parser("streams", help="list the camera and audio streams in an XDF file")
    streams.add_argument("xdf", type=Path)
    streams.set_defaults(handler=_list_streams)

    sync = commands.add_parser("sync", help="write each video with the synchronised audio")
    _add_common(sync)
    sync.add_argument("--out-dir", type=Path, help="output folder (default: next to each video)")
    sync.add_argument("--suffix", default="_synced", help="added to each output file name (default: %(default)s)")
    sync.set_defaults(handler=_sync)

    mosaic = commands.add_parser("mosaic", help="write a mosaic of all videos with the synchronised audio")
    _add_common(mosaic)
    mosaic.add_argument("-o", "--output", type=Path, required=True, help="output .mp4 file")
    mosaic.add_argument("--width", type=int, default=1920, help="mosaic width in pixels (default: %(default)s)")
    mosaic.add_argument("--columns", type=int, help="tiles per row (default: most square grid)")
    mosaic.add_argument("--no-labels", action="store_true", help="do not caption the tiles")
    mosaic.set_defaults(handler=_mosaic)
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("xdf", type=Path, help="the LabRecorder .xdf file")
    parser.add_argument("videos", type=Path, nargs="+", help="video files written by TimeShot")
    parser.add_argument("--stream", action="append", default=[], metavar="FILE=STREAM",
                        help="pair a video file name with a marker stream (repeatable)")  # fmt: skip
    parser.add_argument("--audio", metavar="STREAM", help="audio stream name (default: the first one)")
    parser.add_argument("--no-audio", action="store_true", help="retime the video only")
    parser.add_argument("--fps", type=float, help="output frame rate (default: the cameras' typical rate)")
    parser.add_argument("--span", choices=[m.value for m in SpanMode], default=SpanMode.UNION.value,
                        help="union: keep everything; overlap: only where all streams recorded")  # fmt: skip
    parser.add_argument("--video-latency", type=float, default=0.0, metavar="MS",
                        help="milliseconds the frame markers lag the capture (default: 0)")  # fmt: skip
    parser.add_argument("--audio-latency", type=float, default=0.0, metavar="MS",
                        help="milliseconds the audio timestamps lag the sound (default: 0)")  # fmt: skip
    parser.add_argument("--crf", type=int, default=18, help="x264 quality, lower is better (default: %(default)s)")


def _list_streams(args: argparse.Namespace) -> int:
    recording = load_recording(args.xdf)
    print(f"Camera streams in {recording.path.name}:")
    for camera in recording.cameras:
        print(f"  {camera.name:<24} {camera.frame_indices.size:>7} frames  video_file={camera.video_file or '-'}")
        print(f"  {'':<24} {MarkerTiming.of(camera.frame_indices, camera.time_stamps).summary()}")
    print("Audio streams:")
    for audio in recording.audio:
        seconds = audio.time_stamps[-1] - audio.time_stamps[0]
        print(f"  {audio.name:<24} {audio.sample_rate:>7.0f} Hz  {audio.channel_count} ch  {seconds:.1f} s")
    return 0


def _sync(args: argparse.Namespace) -> int:
    videos, audio, options = _prepare(args)
    for video in videos:
        folder = args.out_dir or video.path.parent
        output = folder / f"{video.path.stem}{args.suffix}.mp4"
        export_synced_video(video, audio, output, options, _console_progress)
        print()
    return 0


def _mosaic(args: argparse.Namespace) -> int:
    videos, audio, options = _prepare(args)
    export_mosaic(videos, audio, args.output, options, _console_progress)
    print()
    return 0


def _prepare(args: argparse.Namespace) -> tuple[list[VideoInput], AudioStream | None, RenderOptions]:
    recording = load_recording(args.xdf)
    if not recording.cameras:
        raise ValueError(f"{args.xdf.name} has no TimeShot camera streams.")

    overrides = dict(_split_pair(item) for item in args.stream)
    guesses = match_videos(args.videos, recording.cameras)
    videos = []
    for path, guess in zip(args.videos, guesses):
        stream = recording.camera(overrides[path.name]) if path.name in overrides else guess
        if stream is None:
            raise ValueError(f"No marker stream left for {path.name}; pair it with --stream.")
        videos.append(VideoInput(path=path, stream=stream, latency=args.video_latency / 1000.0))

    audio = None
    if not args.no_audio:
        if args.audio:
            audio = recording.audio_stream(args.audio)
        elif recording.audio:
            audio = recording.audio[0]
        else:
            log.warning("%s has no audio stream; writing silent video.", args.xdf.name)
        if audio is not None:
            log.info("Using audio stream %r.", audio.name)

    options = RenderOptions(
        fps=args.fps,
        span_mode=SpanMode(args.span),
        audio_latency=args.audio_latency / 1000.0,
        labels=not getattr(args, "no_labels", False),
        mosaic_width=getattr(args, "width", 1920),
        columns=getattr(args, "columns", None),
        encoder=EncoderSettings(crf=args.crf),
    )
    return videos, audio, options


def _split_pair(item: str) -> tuple[str, str]:
    file_name, sep, stream = item.partition("=")
    if not sep or not file_name or not stream:
        raise ValueError(f"--stream expects FILE=STREAM, got {item!r}.")
    return Path(file_name).name, stream


def _console_progress(fraction: float, message: str) -> None:
    sys.stdout.write(f"\r[{fraction * 100:5.1f}%] {message:<60}")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
