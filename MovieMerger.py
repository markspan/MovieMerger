import argparse
import ffmpeg
import tempfile
import soundfile as sf
import numpy as np
import pyxdf
import os

def write_wav(audio_stream, samplerate):
    """
    Converts an audio stream from XDF into a WAV file.

    Args:
        audio_stream (dict): The audio stream from XDF containing the audio time series and timestamps.
        samplerate (int): The sampling rate of the audio data.

    Returns:
        str: Path to the generated WAV file.
        int: The samplerate of the audio.
        float: The timestamp of the audio start.
    """
    samples = np.array(audio_stream['time_series'])
    timestamps = np.array(audio_stream['time_stamps'])

    # Flatten samples if there is only one channel
    if samples.ndim > 1 and samples.shape[1] == 1:
        samples = samples.flatten()

    # Convert to int16 PCM if samples are float and normalized
    if samples.dtype.kind == 'f' and np.max(np.abs(samples)) <= 1.0:
        samples = (samples * 32767).astype(np.int16)

    tmpfile = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    sf.write(tmpfile.name, samples, samplerate, subtype='PCM_16')
    return tmpfile.name, samplerate, float(timestamps[0])

def get_stream_by_name(streams, name):
    """
    Fetches a stream from XDF by its name.

    Args:
        streams (list): A list of streams loaded from XDF.
        name (str): The name of the stream to retrieve.

    Returns:
        dict: The stream that matches the given name.

    Raises:
        ValueError: If the stream with the given name is not found in the XDF.
    """
    for stream in streams:
        if stream['info']['name'][0] == name:
            return stream
    raise ValueError(f"Stream '{name}' not found in XDF.")

def find_audio_stream_by_type(streams):
    """
    Finds the audio stream from the XDF by its type.

    Args:
        streams (list): A list of streams loaded from XDF.

    Returns:
        dict: The stream with type 'Audio'.

    Raises:
        ValueError: If no stream with type 'Audio' is found in the XDF.
    """
    for stream in streams:
        if 'type' in stream['info'] and stream['info']['type'][0] == 'Audio':
            return stream
    raise ValueError("No stream with type 'Audio' found in XDF.")

def find_camera_stream_by_prefix(streams):
    """
    Finds the first camera stream from the XDF by checking for a name prefix.

    Args:
        streams (list): A list of streams loaded from XDF.

    Returns:
        dict: The first camera stream found.

    Raises:
        ValueError: If no stream with a name starting with 'Cam' is found in the XDF.
    """
    cam_streams = [s for s in streams if s['info']['name'][0].startswith('Cam')]
    if not cam_streams:
        raise ValueError("No stream name starting with 'Cam' found in XDF.")
    selected = cam_streams[0]
    print(f"[Info] Using camera stream with name: {selected['info']['name'][0]}")
    return selected

def merge(mp4_path, xdf_path, video_stream_name=None, audio_stream_name=None):
    """
    Merges an MP4 video file with an audio stream from an XDF file.

    Args:
        mp4_path (str): Path to the MP4 video file.
        xdf_path (str): Path to the XDF file containing the streams.
        video_stream_name (str, optional): The name of the video stream in the XDF file. Defaults to None.
        audio_stream_name (str, optional): The name of the audio stream in the XDF file. Defaults to None.

    Returns:
        None: This function creates the merged MP4 file and saves it.
    """
    # Load streams from XDF
    streams, _ = pyxdf.load_xdf(xdf_path)

    # Get video stream by name or use camera stream if not provided
    if video_stream_name:
        video_stream = get_stream_by_name(streams, video_stream_name)
    else:
        video_stream = find_camera_stream_by_prefix(streams)
        video_stream_name = video_stream['info']['name'][0]

    # Get audio stream by name or use default 'Audio' stream
    if audio_stream_name:
        audio_stream = get_stream_by_name(streams, audio_stream_name)
    else:
        audio_stream = find_audio_stream_by_type(streams)
        audio_stream_name = audio_stream['info']['name'][0]
        print(f"[Info] Using audio stream with name: {audio_stream_name}")

    # Calculate the time offsets between the video and audio streams
    video_start = float(video_stream['time_stamps'][0])
    audio_start = float(audio_stream['time_stamps'][0])
    offset = audio_start - video_start

    # Write audio to a WAV file
    audio_samplerate = int(float(audio_stream['info']['nominal_srate'][0]))
    wav_path, samplerate, _ = write_wav(audio_stream, audio_samplerate)

    # Create FFmpeg input streams
    video_input = ffmpeg.input(mp4_path)
    audio_input = ffmpeg.input(wav_path)

    # Apply audio offset or trim audio if needed
    if offset >= 0:
        delay_ms = int(offset * 1000)
        print(f"[Info] Applying audio delay: {delay_ms}ms")
        audio = audio_input.audio.filter('adelay', delays=f'{delay_ms}|{delay_ms}')
        video = video_input.video
    else:
        trim_secs = -offset
        print(f"[Info] Trimming audio by: {trim_secs:.3f}s")
        audio = (
            audio_input.audio
            .filter('atrim', start=trim_secs)
            .filter('asetpts', 'PTS-STARTPTS')
        )
        video = video_input.video

    # Define output path and run FFmpeg command
    output_path = os.path.splitext(mp4_path)[0] + "_merged.mp4"

    (
        ffmpeg
        .output(video, audio, output_path,
                vcodec='copy', acodec='aac', shortest=None)
        .overwrite_output()
        .run()
    )

    print(f"✅ Merged file created: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge MP4 and LSL audio from XDF.")
    parser.add_argument("mp4filename", help="Path to the MP4 video file")
    parser.add_argument("xdffilename", help="Path to the XDF file")
    parser.add_argument("videostream", nargs="?", default=None, help="(Optional) Stream name for video timestamps in the XDF")
    parser.add_argument("audiostream", nargs="?", default=None, help="(Optional) Stream name for audio in the XDF")

    args = parser.parse_args()
    merge(args.mp4filename, args.xdffilename, args.videostream, args.audiostream)