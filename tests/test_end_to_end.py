"""Full pipeline on the synthetic recording described in synthetic.py."""

import threading

import numpy as np
import pytest

from moviemerger import (
    Cancelled,
    RenderOptions,
    SpanMode,
    VideoInput,
    default_output_fps,
    export_mosaic,
    export_synced_video,
    load_recording,
    match_videos,
)
from moviemerger.cli import main as cli_main
from moviemerger.mosaic import Layout

from conftest import requires_ffmpeg
from synthetic import EVENT_TIME, decode_audio, decode_gray, onset_seconds, tile_centre_crops

pytestmark = requires_ffmpeg

FPS = 25.0
# The flash frame is held until the next frame arrives, and output frames
# sample that at 1/FPS, so picture and sound agree to within one frame.
TOLERANCE = 1.0 / FPS + 0.005


def _inputs(recording):
    xdf = load_recording(recording.xdf)
    paths = [recording.video_a, recording.video_b]
    return xdf, [VideoInput(p, s) for p, s in zip(paths, match_videos(paths, xdf.cameras))]


def test_recording_is_parsed(recording):
    xdf = load_recording(recording.xdf)
    assert sorted(c.name for c in xdf.cameras) == ["Stream_0", "Stream_1"]  # "Triggers" rejected
    assert [a.name for a in xdf.audio] == ["Mic"]
    camera = xdf.camera("Stream_0")
    assert camera.video_file == "Video_0.avi"
    assert camera.frame_indices.size == recording.frame_times_a.size
    assert np.abs(xdf.audio[0].samples).max() <= 1.0


def test_matching_and_default_fps(recording):
    xdf, videos = _inputs(recording)
    assert [v.stream.name for v in videos] == ["Stream_0", "Stream_1"]
    assert default_output_fps(xdf.cameras) == 25.0


def test_synced_video_aligns_flash_and_beep(recording, tmp_path):
    xdf, videos = _inputs(recording)
    output = export_synced_video(videos[0], xdf.audio[0], tmp_path / "a.mp4", RenderOptions(fps=FPS))

    brightness = decode_gray(output, 160, 120)
    sound = np.abs(decode_audio(output))
    flash = onset_seconds(brightness, FPS, 150)
    beep = onset_seconds(sound, 8000, 0.3)

    start = recording.frame_times_a[0]
    assert brightness.size == pytest.approx((recording.frame_times_a[-1] - start + 0.04) * FPS, abs=2)
    assert beep == pytest.approx(EVENT_TIME - start, abs=0.005)
    assert flash == pytest.approx(beep, abs=TOLERANCE)


def test_mosaic_aligns_all_cameras_with_audio(recording, tmp_path):
    xdf, videos = _inputs(recording)
    options = RenderOptions(fps=FPS, mosaic_width=320, labels=False)
    output = export_mosaic(videos, xdf.audio[0], tmp_path / "mosaic.mp4", options)

    # Tiles take the widest camera's aspect ratio (camera B is 16:9).
    layout = Layout.grid(2, 16 / 9, 320)
    sound = np.abs(decode_audio(output))
    beep = onset_seconds(sound, 8000, 0.3)
    for crop in tile_centre_crops(layout.tiles):
        flash = onset_seconds(decode_gray(output, layout.width, layout.height, crop), FPS, 150)
        assert flash == pytest.approx(beep, abs=TOLERANCE)
    assert beep == pytest.approx(EVENT_TIME - recording.frame_times_a[0], abs=0.005)


def test_overlap_mode_trims_to_common_time(recording, tmp_path):
    xdf, videos = _inputs(recording)
    options = RenderOptions(fps=FPS, mosaic_width=320, span_mode=SpanMode.OVERLAP)
    output = export_mosaic(videos, xdf.audio[0], tmp_path / "overlap.mp4", options)
    layout = Layout.grid(2, 16 / 9, 320)
    frames = decode_gray(output, layout.width, layout.height).size
    common = min(recording.frame_times_a[-1], recording.frame_times_b[-1]) - recording.frame_times_b[0]
    assert frames == pytest.approx(common * FPS, abs=3)


def test_cancel_removes_partial_output(recording, tmp_path):
    _, videos = _inputs(recording)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        export_mosaic(videos, None, tmp_path / "cancelled.mp4", RenderOptions(fps=FPS), cancel=cancel)
    assert not (tmp_path / "cancelled.mp4").exists()


def test_cli_sync_and_mosaic(recording, tmp_path):
    common = [str(recording.xdf), str(recording.video_a), str(recording.video_b), "--fps", "25"]
    assert cli_main(["sync", *common, "--out-dir", str(tmp_path)]) == 0
    assert (tmp_path / "Video_0_synced.mp4").is_file()
    assert (tmp_path / "Video_1_synced.mp4").is_file()
    assert cli_main(["mosaic", *common, "-o", str(tmp_path / "m.mp4"), "--width", "320"]) == 0
    assert (tmp_path / "m.mp4").is_file()
    assert cli_main(["streams", str(recording.xdf)]) == 0
