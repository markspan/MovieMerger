#!/usr/bin/env python3
"""Backwards-compatible entry point for the original MovieMerger script.

Usage (unchanged)::

    python MovieMerger.py video.avi recording.xdf [video_stream_name] [audio_stream_name]

Writes ``video_synced.mp4`` next to the video. This is now a thin wrapper
around ``moviemerger sync``; see README.md for the full CLI and the GUI.
It runs straight from a clone, without installing the package.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from moviemerger.cli import main


def _legacy_arguments(argv: list[str]) -> list[str]:
    """Translate the original positional arguments into ``moviemerger sync`` ones."""
    parser = argparse.ArgumentParser(description="Synchronise one video with the audio in an XDF file.")
    parser.add_argument("video", type=Path, help="video file written by TimeShot")
    parser.add_argument("xdf", type=Path, help="the LabRecorder .xdf file")
    parser.add_argument("videostream", nargs="?", help="marker stream of this video (default: matched automatically)")
    parser.add_argument("audiostream", nargs="?", help="audio stream (default: the first one)")
    args = parser.parse_args(argv)

    forwarded = ["sync", str(args.xdf), str(args.video)]
    if args.videostream:
        forwarded += ["--stream", f"{args.video.name}={args.videostream}"]
    if args.audiostream:
        forwarded += ["--audio", args.audiostream]
    return forwarded


if __name__ == "__main__":
    sys.exit(main(_legacy_arguments(sys.argv[1:])))
