import numpy as np
import pytest

from moviemerger.timing import NO_FRAME, FrameClock, MarkerTiming, TimeSpan, output_times


def test_frames_follow_marker_times_not_nominal_rate():
    clock = FrameClock.from_markers(np.arange(4), np.array([10.0, 10.1, 10.5, 10.6]))
    shown = clock.frames_at(np.array([9.9, 10.0, 10.05, 10.3, 10.55, 10.65, 10.71]))
    assert shown.tolist() == [NO_FRAME, 0, 0, 1, 2, 3, NO_FRAME]


def test_lost_marker_is_interpolated():
    clock = FrameClock.from_markers(np.array([0, 1, 3]), np.array([0.0, 0.1, 0.3]))
    assert clock.times[2] == pytest.approx(0.2)


def test_frames_outside_marker_range_are_untimed():
    clock = FrameClock.from_markers(np.array([2, 3, 4]), np.array([1.0, 1.1, 1.2]), frame_count=7)
    assert np.isnan(clock.times[[0, 1, 5, 6]]).all()
    assert clock.timed_frames == 3
    assert clock.frames_at(np.array([1.05])).tolist() == [2]


def test_latency_moves_frames_earlier():
    clock = FrameClock.from_markers(np.arange(3), np.array([1.0, 1.1, 1.2]), latency=0.05)
    assert clock.span.start == pytest.approx(0.95)


def test_duplicate_and_backwards_markers_are_tolerated():
    clock = FrameClock.from_markers(np.array([0, 1, 1, 2]), np.array([0.0, 0.1, 0.9, 0.05]))
    assert np.all(np.diff(clock.times) >= 0)


def test_span_set_operations():
    a, b = TimeSpan(0, 10), TimeSpan(5, 20)
    assert TimeSpan.union([a, b]) == TimeSpan(0, 20)
    assert TimeSpan.intersection([a, b]) == TimeSpan(5, 10)
    with pytest.raises(ValueError):
        TimeSpan.intersection([TimeSpan(0, 1), TimeSpan(2, 3)])


def test_output_times_are_regular():
    times = output_times(TimeSpan(100.0, 101.0), 25)
    assert times.size == 25
    assert np.allclose(np.diff(times), 0.04)


def test_marker_timing_flags_burst_reads():
    steady = np.arange(100) / 30.0
    assert not MarkerTiming.of(np.arange(100), steady).bursty
    # Every other frame read straight from the buffer, 1 ms after the previous one.
    bursty = np.repeat(np.arange(50) / 15.0, 2) + np.tile([0.0, 0.001], 50)
    timing = MarkerTiming.of(np.arange(100), bursty)
    assert timing.bursty
    assert timing.burst_fraction == pytest.approx(0.5, abs=0.02)


def test_markers_beyond_the_file_are_rejected():
    with pytest.raises(ValueError, match="only 3"):
        FrameClock.from_markers(np.array([5, 6]), np.array([0.0, 0.1]), frame_count=3)
