from pathlib import Path

import numpy as np

from moviemerger.mosaic import Label, Layout
from moviemerger.pipeline import match_videos
from moviemerger.xdf_streams import CameraStream


def _camera(name, video_file=None, camera_name=None):
    return CameraStream(name, video_file, camera_name, None, np.arange(2), np.array([0.0, 0.1]))


def test_grid_is_square_and_even():
    layout = Layout.grid(5, 4 / 3, 1921)
    assert (layout.width, layout.height) == (1920, 960)
    assert len(layout.tiles) == 5
    assert all(t.width % 2 == 0 and t.height % 2 == 0 for t in layout.tiles)
    assert layout.tiles[3].y == layout.tiles[0].height


def test_grid_respects_columns():
    layout = Layout.grid(4, 16 / 9, 1280, columns=4)
    assert layout.height == layout.tiles[0].height


def test_label_blends_inside_tile():
    layout = Layout.single(64, 48)
    canvas = np.zeros((48, 64, 3), np.uint8)
    Label.render("Cam", layout.tiles[0].height).draw(canvas, layout.tiles[0])
    assert canvas.max() > 0


def test_match_by_advertised_file_then_name_then_order():
    cameras = [
        _camera("Stream_1", video_file=r"C:\rec\Video_1.avi"),
        _camera("Stream_0", video_file="Video_0.avi"),
        _camera("BodyCam"),
        _camera("Spare"),
    ]
    paths = [Path("Video_0.mp4"), Path("Video_1.avi"), Path("subject3_bodycam.avi"), Path("other.avi")]
    matched = [c.name if c else None for c in match_videos(paths, cameras)]
    assert matched == ["Stream_0", "Stream_1", "BodyCam", "Spare"]


def test_name_match_needs_whole_token():
    cameras = [_camera("Cam1"), _camera("Cam10")]
    matched = match_videos([Path("cam10.avi"), Path("cam1.avi")], cameras)
    assert [c.name for c in matched] == ["Cam10", "Cam1"]
