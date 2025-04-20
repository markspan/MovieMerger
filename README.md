# XDF Audio + MP4 Video Synchronizer

Synchronize and merge an MP4 video file with audio data from an XDF file using precise timestamp alignment.  
Designed for use with **TimeShot** (video recording) and **AudioStream** (audio-to-LSL), this tool lets you reconstruct fully synchronized audiovisual data from co-recorded sources.

## Why Use This?

In multimodal experiments, you may:

- Record video using **TimeShot**, which embeds precise LSL timestamps.
- Record microphone audio using **AudioStream**, a simple app that streams live audio into the LSL network.
- Save all LSL streams (including audio and timestamps) into an `.xdf` file using LabRecorder.

This script ensures the audio and video are perfectly aligned by:

- Using LSL/XDF timestamps as the ground truth.
- Finding the overlapping time range between audio and video.
- Trimming and synchronizing both streams using linear interpolation (to handle clock drift or offset).

## How to Use

### Requirements

- Python 3.7+
- `ffmpeg` (must be installed and in your system's PATH)
- Python packages: `numpy`, `soundfile`, `ffmpeg-python`, `pyxdf`

Install dependencies:

```bash
pip install numpy soundfile ffmpeg-python pyxdf
```

### Usage

```bash
python merge_xdf_audio_video.py video.mp4 recording.xdf [video_stream_name] [audio_stream_name]
```

- `video.mp4`: Your video file, ideally recorded using **TimeShot**.
- `recording.xdf`: The XDF file saved from LabRecorder, containing LSL streams from **AudioStream**, TimeShot, and any other sensors.
- `video_stream_name` (optional): Name of the stream containing video frame timestamps (defaults to first stream starting with `'Cam'`).
- `audio_stream_name` (optional): Name of the stream containing audio (defaults to first stream of type `'Audio'`).

### Output

Produces a new video file:

```
video_synced.mp4
```

This file contains the original video and the precisely synchronized and trimmed audio.

## When to Use

- When you record video and audio separately, but synchronize them via LSL.
- When using **TimeShot** for high-fidelity, timestamped video.
- When using **AudioStream** to send live audio into the LSL network.
- When you need accurate, reproducible AV data for behavioral research, human-computer interaction, or cognitive experiments.

---

**TimeShot**: Open-source, low-latency video capture with LSL timestamps.  
**AudioStream**: A lightweight app that streams mic input into LSL.  
**This Script**: Synchronize them into one timeline, perfectly aligned.