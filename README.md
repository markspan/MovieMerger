# <img src="src/moviemerger/resources/moviemerger.svg" alt="" width="48" align="top"> MovieMerger

Synchronise videos recorded with **[TimeShot](https://github.com/markspan/TimeShot)** with the scene audio
in a LabRecorder `.xdf` file, using LabStreamingLayer timestamps as the ground truth.

MovieMerger produces:

1. **One synchronised video per camera**: each video retimed by its own LSL frame markers, with the audio
   from the XDF file placed sample-accurately underneath it.
2. **A mosaic** of all cameras in one picture, with the same synchronised audio.

Both are available from a desktop GUI and from the command line.

## How synchronisation works

TimeShot writes each camera to an MJPEG `.avi` file and, for every frame it writes, pushes an LSL marker whose
payload is that frame's index. LabRecorder stores those markers next to the audio stream (for example from
AudioStream) in one `.xdf` file, all on a common clock.

MovieMerger uses that information directly:

* **Video.** The frame rate stored in the `.avi` is only TimeShot's estimate at start-up; webcams deliver
  frames irregularly and occasionally stall. Every frame is therefore placed at the time of its own marker.
  The output has a constant frame rate, and each output frame shows the most recent camera frame captured at
  or before that moment. Frames recorded while LabRecorder was not running have no marker and are left out.
* **Audio.** Samples are placed by their own (dejittered) XDF timestamps rather than by counting samples from
  the start. This corrects for an audio clock that runs slightly fast or slow, and turns dropouts into
  silence instead of shifting everything after them.
* **Latency.** LSL timestamps record when a sample was *pushed*, which is somewhat later than when it was
  captured (see [Timestamp accuracy](#timestamp-accuracy)). Each camera and the audio have a latency
  setting (milliseconds, positive = move earlier) to compensate for a constant offset.

The test suite builds a deliberately hostile recording (irregular frames with a stall, a second camera that
starts later at another rate, an audio clock 1 % fast with a dropout) and checks that a flash and a beep that
happened together end up within one frame of each other in the output.

## Installation

### Ready-made executables

Download the archive for your system from the
[Releases page](https://github.com/markspan/MovieMerger/releases): Windows (x64), macOS (Apple Silicon and
Intel, macOS 12+) and Linux (x64, glibc 2.35+). Everything is included, ffmpeg too. One executable is both the
desktop app (started without arguments) and the command-line tool (started with arguments). The release notes
explain the one-time security prompt of each operating system.

### From source

Requirements: Python 3.10+. A static ffmpeg build (with H.264 and AAC encoders) comes with the
[`imageio-ffmpeg`](https://pypi.org/project/imageio-ffmpeg/) dependency, so nothing else needs installing.
Set `MOVIEMERGER_FFMPEG` to use a different ffmpeg; `ffmpeg` on `PATH` is the fallback on platforms
`imageio-ffmpeg` has no build for.

```bash
git clone https://github.com/markspan/MovieMerger.git
cd MovieMerger
pip install ".[gui]"
```

## The GUI

```bash
moviemerger-gui          # or: python -m moviemerger
```

1. **Open XDF…** and choose the LabRecorder recording. The audio stream list fills in.
2. **Add videos…** (or drag `.xdf` and video files onto the window). Each video is paired with its
   TimeShot marker stream automatically: by the file name TimeShot recorded, then by stream or camera name,
   then in order. Change a pairing with the drop-down in the table if needed.
3. Optionally set a mosaic label and a latency per camera, the audio latency, the frame rate (Auto picks
   the fastest camera's typical rate), the time span, quality and mosaic size.
4. Click **Export synced videos** (writes `<video>_synced.mp4` per camera) or **Export mosaic…**.

The table order is the mosaic order, left to right and top to bottom. Exports run in the background and can
be cancelled; an incomplete file is removed.

## The command line

```bash
# What is in the recording, including a frame-timing check per camera
moviemerger streams session.xdf

# One synchronised video per camera
moviemerger sync session.xdf Video_0.avi Video_1.avi --out-dir synced

# A 2-column mosaic, 1920 px wide
moviemerger mosaic session.xdf Video_*.avi -o mosaic.mp4 --columns 2
```

Common options:

| Option | Meaning |
| --- | --- |
| `--stream FILE=STREAM` | Pair a video with a marker stream explicitly (repeatable). |
| `--audio STREAM` / `--no-audio` | Choose the audio stream, or retime the video only. |
| `--fps N` | Output frame rate (default: the fastest camera's typical rate). |
| `--span union\|overlap` | Keep everything (gaps become black/silence), or only the time all streams recorded. |
| `--video-latency MS`, `--audio-latency MS` | Constant offsets to compensate, positive = move earlier. |
| `--crf N` | x264 quality, lower is better (default 18). |

Mosaic only: `--width`, `--columns`, `--no-labels`. Sync only: `--out-dir`, `--suffix`.

The original invocation still works:

```bash
python MovieMerger.py video.avi recording.xdf [video_stream_name] [audio_stream_name]
```

## Timestamp accuracy

TimeShot calls `push_sample` without a timestamp, so LSL stamps each frame when the marker is pushed. In
TimeShot's capture loop that happens after `capture.Read()` returned the frame, the frame number was drawn
and the frame was MJPEG-encoded and written to disk. A marker therefore lags the actual exposure by:

* a roughly **constant** part: the camera and USB/driver pipeline (typically tens of milliseconds), and
* a **variable** part: drawing, encoding and disk I/O for that frame (milliseconds, varying per frame).

The constant part also applies to the audio (buffering in the sound card and the sender), with a different
value. Because only the *difference* matters, calibrate it once per setup: record a clap or a clapperboard
visible to every camera, export, and adjust the latency settings until picture and sound coincide.

If TimeShot's loop cannot keep up with the camera, frames queue in the driver's buffer and are then read, and
stamped, in quick bursts; their markers no longer reflect when they were captured. MovieMerger checks for this:
`moviemerger streams` and every export log each camera's frame-interval statistics and warn when a noticeable
share of frames was burst-read.

## Development

```bash
pip install -e ".[gui,test]"
pytest
```

### Building the executables

Releases are compiled with [Nuitka](https://nuitka.net/) by the *Build and release* GitHub workflow
(`.github/workflows/release.yml`). For each platform it compiles, runs `packaging/smoke_test.py` against
the executable (with any system ffmpeg hidden, so the bundled one must work) and uploads the archive.
To publish a release, either run the workflow from the *Actions* tab with *publish* ticked (it tags the
selected branch as `v<moviemerger.__version__>`), or push a tag matching that version yourself:

```bash
git tag v2.0.0
git push origin v2.0.0
```

To build locally for the current platform (needs a C compiler; Nuitka explains what it wants):

```bash
pip install -r packaging/requirements-build.txt
pip install --no-deps .
python packaging/build.py                 # -> dist/MovieMerger-<version>-<platform>.zip / .tar.gz
python packaging/smoke_test.py build/MovieMerger/MovieMerger
```

`packaging/requirements-build.txt` pins every dependency of the release builds; bump it deliberately.

Package layout (`src/moviemerger`):

| Module | Responsibility |
| --- | --- |
| `xdf_streams.py` | Find and load TimeShot marker streams and audio streams from an XDF file. |
| `timing.py` | Frame clocks (frame number to LSL time), time spans, marker-timing diagnostics. |
| `audio.py` | Resample audio onto the output timeline by timestamp. |
| `video.py` | ffmpeg wrappers: probing, frame-accurate decoding, H.264/AAC encoding. |
| `mosaic.py` | Grid layout and tile captions. |
| `pipeline.py` | Export jobs: stream matching, synced single videos, mosaics. |
| `cli.py`, `gui.py` | Command line and PySide6 GUI. |
| `resources/moviemerger.svg` | The app icon (master image; the build renders `.ico`/`.icns` from it). |

## License

[GPL-2.0-only](LICENSE)

## Author

**Mark Span** ([@markspan](https://github.com/markspan))
