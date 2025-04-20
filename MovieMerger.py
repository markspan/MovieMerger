#!/usr/bin/env python3
"""
merge_xdf_audio_video.py

Synchronizes and merges an MP4 video file with an audio stream from an XDF file
based on their timestamps. Only the overlapping portion between the audio and video
is used to ensure accurate alignment. Output is a synchronized MP4 file with audio.

Usage:
    python merge_xdf_audio_video.py video.mp4 recording.xdf [video_stream_name] [audio_stream_name]

Arguments:
    video.mp4             Path to the video file.
    recording.xdf         Path to the XDF file.
    video_stream_name     (Optional) Stream name to use for video timestamps. Defaults to first stream starting with 'Cam'.
    audio_stream_name     (Optional) Stream name to use for audio. Defaults to stream with type 'Audio'.

Dependencies:
    - numpy
    - soundfile
    - pyxdf
    - ffmpeg-python
    - ffmpeg (must be installed and available on PATH)

Author: ChatGPT
"""

import argparse
import ffmpeg
import tempfile
import soundfile as sf
import numpy as np
import pyxdf
import os
import logging
from typing import List, Dict, Tuple, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def write_wav(audio_stream: Dict, samplerate: int) -> Tuple[str, int, float]:
    """
    Converts an audio stream from XDF into a temporary WAV file.

    Args:
        audio_stream (dict): The audio stream from XDF containing time series and timestamps.
        samplerate (int): The nominal sampling rate of the audio stream.

    Returns:
        Tuple[str, int, float]: Path to the written WAV file, its sample rate,
                                and the starting timestamp of the audio.
    """
    try:
        samples = np.array(audio_stream['time_series'])
        timestamps = np.array(audio_stream['time_stamps'])

        if samples.ndim > 1 and samples.shape[1] == 1:
            samples = samples.flatten()

        if samples.dtype.kind == 'f' and np.max(np.abs(samples)) <= 1.0:
            samples = (samples * 32767).astype(np.int16)

        tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        sf.write(tmpfile.name, samples, samplerate, subtype='PCM_16')
        return tmpfile.name, samplerate, float(timestamps[0])
    except Exception as e:
        logging.error(f"Error writing WAV file: {e}")
        raise

def get_stream_by_name(streams: List[Dict], name: str) -> Dict:
    """
    Retrieves a stream from the list by name.

    Args:
        streams (list): All streams loaded from an XDF file.
        name (str): The desired stream name.

    Returns:
        dict: The matching stream.

    Raises:
        ValueError: If no stream with the given name exists.
    """
    for stream in streams:
        if stream['info']['name'][0] == name:
            return stream
    raise ValueError(f"Stream '{name}' not found in XDF.")

def find_audio_stream_by_type(streams: List[Dict]) -> Dict:
    """
    Finds the audio stream using the 'type' field.

    Args:
        streams (list): All streams loaded from an XDF file.

    Returns:
        dict: The audio stream.

    Raises:
        ValueError: If no stream with type 'Audio' is found.
    """
    for stream in streams:
        if 'type' in stream['info'] and stream['info']['type'][0] == 'Audio':
            return stream
    raise ValueError("No stream with type 'Audio' found in XDF.")

def find_camera_stream_by_prefix(streams: List[Dict]) -> Dict:
    """
    Finds the first video stream whose name starts with 'Cam'.

    Args:
        streams (list): All streams loaded from an XDF file.

    Returns:
        dict: The camera/video stream.

    Raises:
        ValueError: If no such stream is found.
    """
    cam_streams = [s for s in streams if s['info']['name'][0].startswith('Cam')]
    if not cam_streams:
        raise ValueError("No stream name starting with 'Cam' found in XDF.")
    selected = cam_streams[0]
    logging.info(f"Using camera stream with name: {selected['info']['name'][0]}")
    return selected

def merge(mp4_path: str, xdf_path: str,
          video_stream_name: Optional[str] = None,
          audio_stream_name: Optional[str] = None) -> None:
    """
    Synchronizes and merges an MP4 video with an audio stream from XDF.
    Only the overlapping part of the streams is used.

    Args:
        mp4_path (str): Path to the MP4 video.
        xdf_path (str): Path to the XDF file.
        video_stream_name (str, optional): Name of the video stream in XDF.
        audio_stream_name (str, optional): Name of the audio stream in XDF.

    Returns:
        None. The merged file is written to disk with '_synced' appended.
    """
    try:
        streams, _ = pyxdf.load_xdf(xdf_path)

        # Get video and audio streams
        video_stream = get_stream_by_name(streams, video_stream_name) if video_stream_name else find_camera_stream_by_prefix(streams)
        audio_stream = get_stream_by_name(streams, audio_stream_name) if audio_stream_name else find_audio_stream_by_type(streams)

        video_start = float(video_stream['time_stamps'][0])
        video_end = float(video_stream['time_stamps'][-1])
        audio_start = float(audio_stream['time_stamps'][0])
        audio_end = float(audio_stream['time_stamps'][-1])

        # Calculate overlap
        sync_start = max(video_start, audio_start)
        sync_end = min(video_end, audio_end)
        if sync_end <= sync_start:
            raise ValueError("No overlapping time window between audio and video.")

        audio_offset = sync_start - audio_start
        video_offset = sync_start - video_start
        trim_duration = sync_end - sync_start

        logging.info(f"Overlap from {sync_start:.3f}s to {sync_end:.3f}s ({trim_duration:.3f}s)")

        # Write audio to WAV
        audio_samplerate = int(float(audio_stream['info']['nominal_srate'][0]))
        wav_path, _, _ = write_wav(audio_stream, audio_samplerate)

        # Prepare trimmed audio
        audio_input = (
            ffmpeg.input(wav_path)
            .audio
            .filter('atrim', start=audio_offset, duration=trim_duration)
            .filter('asetpts', 'PTS-STARTPTS')
        )

        # Prepare trimmed video
        video_input = (
            ffmpeg.input(mp4_path)
            .trim(start=video_offset, duration=trim_duration)
            .setpts('PTS-STARTPTS')
            .video
        )

        output_path = os.path.splitext(mp4_path)[0] + "_synced.mp4"

        (
            ffmpeg
            .output(video_input, audio_input, output_path,
                    vcodec='copy', acodec='aac', shortest=None)
            .overwrite_output()
            .run()
        )

        logging.info(f"Synchronized output written to: {output_path}")

    except Exception as e:
        logging.error(f"Failed to merge audio and video: {e}")
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synchronize and merge MP4 with audio from XDF.")
    parser.add_argument("mp4filename", help="Path to the MP4 video file")
    parser.add_argument("xdffilename", help="Path to the XDF file")
    parser.add_argument("videostream", nargs="?", default=None, help="(