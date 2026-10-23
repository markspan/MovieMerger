"""Shared fixtures: the synthetic recording described in ``synthetic.py``."""

from __future__ import annotations

import pytest

from moviemerger.video import FfmpegError, ffmpeg_binary

from synthetic import Recording, build_recording


def _has_ffmpeg() -> bool:
    try:
        ffmpeg_binary()
    except FfmpegError:
        return False
    return True


requires_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="ffmpeg is not available")


@pytest.fixture(scope="session")
def recording(tmp_path_factory: pytest.TempPathFactory) -> Recording:
    if not _has_ffmpeg():
        pytest.skip("ffmpeg is not available")
    return build_recording(tmp_path_factory.mktemp("recording"))
