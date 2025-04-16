# MovieMerger
## Merge MP4 and LSL Audio from XDF

This simple script is designed to merge an MP4 video file with an audio stream from an XDF file. It leverages the capabilities of [Lab Streaming Layer (LSL)](https://labstreaminglayer.readthedocs.io/info/what_is_lsl.html) to handle synchronized data streams, [TimeShot](https://github.com/markspan/TimeShot) for precise timestamping, and [AudioCapture](https://github.com/labstreaminglayer/App-AudioCapture) for capturing audio data.

## Overview

The script performs the following steps:
1. Loads the XDF file containing the audio and video streams.
2. Extracts the specified audio and video streams from the XDF file.
3. Converts the audio stream into a WAV file.
4. Merges the MP4 video file with the WAV audio file, synchronizing them based on their timestamps.
5. Saves the merged file as a new MP4 file.

## Prerequisites

Before running the script, ensure you have the following dependencies installed:
- `ffmpeg`
- `soundfile`
- `numpy`
- `pyxdf`

You can install these dependencies using pip:
```
pip install ffmpeg soundfile numpy pyxdf
```

## Usage

To use the script, run it from the command line with the following arguments:

```
python merge_script.py <mp4filename> <xdffilename> [videostream] [audiostream]
```

- `<mp4filename>`: Path to the MP4 video file.
- `<xdffilename>`: Path to the XDF file containing the streams.
- `[videostream]`: (Optional) Stream name for video timestamps in the XDF.
- `[audiostream]`: (Optional) Stream name for audio in the XDF.

## Example

```
python merge_script.py video.mp4 data.xdf Cam1 Audio1
```

In this example:
- `video.mp4` is the path to the MP4 video file.
- `data.xdf` is the path to the XDF file.
- `Cam1` is the name of the video stream in the XDF file.
- `Audio1` is the name of the audio stream in the XDF file.

## Options

- **MP4 Filename**: The path to the MP4 video file you want to merge with the audio stream.
- **XDF Filename**: The path to the XDF file containing the audio and video streams.
- **Video Stream Name**: (Optional) The name of the video stream in the XDF file. If not provided, the script will automatically select the first stream with a name starting with 'Cam'.
- **Audio Stream Name**: (Optional) The name of the audio stream in the XDF file. If not provided, the script will automatically select the first stream with the type 'Audio'.

## Output

The script will create a new MP4 file with the merged video and audio streams. The output file will be named with a `_merged` suffix before the `.mp4` extension. For example, if the input MP4 file is named `video.mp4`, the output file will be named `video_merged.mp4`.

## Logging

The script uses logging to provide information about the merging process. Logs will be displayed in the console, indicating the progress and any errors encountered.

## References

- [Lab Streaming Layer (LSL)](https://labstreaminglayer.readthedocs.io/info/what_is_lsl.html): A system for the unified collection of measurement time series in research.
- [TimeShot](https://github.com/markspan/TimeShot): An application for precise timestamping of video frames using LSL.
- [AudioCapture](https://github.com/labstreaminglayer/App-AudioCapture): An application for capturing audio data using LSL.

This script is intended for users who need to synchronize and merge video and audio data streams for analysis.



