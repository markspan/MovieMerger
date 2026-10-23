import numpy as np
import pytest

from moviemerger.audio import render_audio
from moviemerger.timing import TimeSpan
from moviemerger.xdf_streams import AudioStream, normalise_samples


def _render(tmp_path, stream, span, latency=0.0):
    rendered = render_audio(stream, span, tmp_path / "out.f32", latency)
    data = np.fromfile(rendered.path, "<f4").reshape(-1, rendered.channels)
    return rendered, data


def test_samples_are_placed_by_timestamp(tmp_path):
    rate = 100.0
    stamps = 5.0 + np.arange(100) / rate
    stream = AudioStream("a", rate, np.ones((100, 1), np.float32), stamps)
    rendered, data = _render(tmp_path, stream, TimeSpan(4.5, 6.5))
    assert rendered.sample_rate == 100
    assert data.shape == (200, 1)
    assert not data[:50].any(), "silence before the stream starts"
    assert np.allclose(data[50:150], 1.0)
    assert not data[151:].any(), "silence after the stream ends"


def test_dropouts_become_silence(tmp_path):
    rate = 100.0
    stamps = np.arange(200) / rate
    keep = (stamps < 0.5) | (stamps >= 1.0)
    stream = AudioStream("a", rate, np.ones((keep.sum(), 1), np.float32), stamps[keep])
    _, data = _render(tmp_path, stream, TimeSpan(0.0, 2.0))
    assert np.allclose(data[:50], 1.0)
    assert not data[51:100].any()
    assert np.allclose(data[100:199], 1.0)


def test_clock_drift_is_corrected(tmp_path):
    # Device clock 1 % fast: samples arrive denser than nominal.
    nominal, real = 1000.0, 1010.0
    stamps = np.arange(10100) / real
    signal = (stamps >= 9.0).astype(np.float32)[:, np.newaxis]
    _, data = _render(tmp_path, AudioStream("a", nominal, signal, stamps), TimeSpan(0.0, 10.0))
    assert np.flatnonzero(data[:, 0] > 0.5)[0] == pytest.approx(9000, abs=1)


def test_latency_shifts_sound_earlier(tmp_path):
    stamps = np.arange(1000) / 1000.0
    signal = (stamps >= 0.5).astype(np.float32)[:, np.newaxis]
    _, data = _render(tmp_path, AudioStream("a", 1000.0, signal, stamps), TimeSpan(0.0, 1.0), latency=0.1)
    assert np.flatnonzero(data[:, 0] > 0.5)[0] == pytest.approx(400, abs=1)


@pytest.mark.parametrize(
    ("raw", "expected_peak"),
    [
        (np.array([[32767], [-32767]], np.int16), 1.0),
        (np.array([[0.5], [-0.25]], np.float32), 0.5),
        (np.array([[16384.0], [0.0]], np.float32), 0.5),
    ],
)
def test_normalise_samples(raw, expected_peak):
    out = normalise_samples(raw)
    assert out.dtype == np.float32
    assert np.abs(out).max() == pytest.approx(expected_peak, rel=1e-3)
