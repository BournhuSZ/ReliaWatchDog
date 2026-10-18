from queue import Empty

import cv2
import numpy as np
import pytest

from rdt_monitor.domain import MarkerState, SyncConfig, SyncState
from rdt_monitor.replay import Region, ReplayPipeline, ReplayRecipe, SyntheticSource, VideoSource


@pytest.fixture
def recipe():
    return ReplayRecipe(SyncConfig(2, 0.2, 4), Region(0.05, 0.1, 0.1, 0.1), Region(0.8, 0.1, 0.1, 0.1))


def test_preview_drops_do_not_drop_cycle_starts(recipe):
    pipeline = ReplayPipeline(SyntheticSource, recipe, realtime=False)
    pipeline.start()
    pipeline.join(10)
    assert not pipeline.alive
    assert pipeline.status.state == "Ended"
    assert pipeline.previews.qsize() == 1
    preview = pipeline.previews.get_nowait()
    assert preview.frame.frame_id == 359
    assert preview.result.observed_cycles == 4
    assert not preview.frame.image.flags.writeable
    events = []
    while True:
        try:
            events.append(pipeline.events.get_nowait())
        except Empty:
            break
    assert sum(event.cycle_started for event in events) == 4
    assert sum(event.inspection_ready for event in events) == 4


def test_event_overflow_is_not_hidden_by_preview_replacement(recipe):
    pipeline = ReplayPipeline(SyntheticSource, recipe, event_capacity=1, realtime=False)
    pipeline.start()
    pipeline.join(10)
    assert not pipeline.alive
    assert pipeline.status.state == "Fault"
    assert "overflow" in pipeline.status.reason
    assert pipeline.events.qsize() == 1
    assert pipeline.previews.get_nowait().result.state == SyncState.LOST


def test_source_error_is_visible_and_source_runs_off_caller_thread(recipe):
    from threading import current_thread

    caller = current_thread()

    def unavailable():
        assert current_thread() is not caller
        raise OSError("Camera adapter intentionally absent")

    pipeline = ReplayPipeline(unavailable, recipe, realtime=False)
    pipeline.start()
    pipeline.join(10)
    assert pipeline.status.state == "Fault"
    assert "intentionally absent" in pipeline.status.reason


def test_stop_is_interruptible_and_does_not_auto_restart(recipe):
    pipeline = ReplayPipeline(SyntheticSource, recipe)
    pipeline.start()
    pipeline.stop()
    pipeline.join(10)
    assert not pipeline.alive
    assert pipeline.status.state == "Stopped"
    with pytest.raises(RuntimeError):
        pipeline.start()


def test_brightness_classifier_has_an_explicit_ambiguous_band(recipe):
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    region = Region(0, 0, 1, 1)
    assert recipe.classify(image, region) == MarkerState.ABSENT
    image[:50] = 255
    assert recipe.classify(image, region) == MarkerState.AMBIGUOUS
    image[:] = 255
    assert recipe.classify(image, region) == MarkerState.PRESENT


@pytest.mark.parametrize("values", [(-0.1, 0, 0.1, 0.1), (0, 0, 0, 0.1), (0.9, 0, 0.2, 0.1)])
def test_invalid_regions_rejected(values):
    with pytest.raises(ValueError):
        Region(*values)


def test_missing_recording_does_not_open(tmp_path):
    with pytest.raises(OSError):
        VideoSource(str(tmp_path / "missing.avi"))


def test_local_recording_replays_sequentially_with_source_time(tmp_path, recipe):
    path = tmp_path / "markers.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (960, 540))
    assert writer.isOpened()
    fixture = SyntheticSource(frame_count=90)
    while (frame := fixture.read()) is not None:
        writer.write(frame.image)
    writer.release()
    source = VideoSource(str(path))
    first, second = source.read(), source.read()
    assert first.frame_id == 0 and first.timestamp == 0
    assert second.frame_id == 1 and second.timestamp == pytest.approx(1 / 30)
    source.close()
    pipeline = ReplayPipeline(lambda: VideoSource(str(path)), recipe, realtime=False)
    pipeline.start()
    pipeline.join(10)
    assert pipeline.status.state == "Ended"
    preview = pipeline.previews.get_nowait()
    assert preview.frame.frame_id == 89
    assert preview.result.observed_cycles == 1


def test_source_release_failure_is_visible(recipe):
    class BrokenRelease(SyntheticSource):
        def close(self):
            raise OSError("release failed")

    pipeline = ReplayPipeline(lambda: BrokenRelease(frame_count=0), recipe, realtime=False)
    pipeline.start()
    pipeline.join(10)
    assert pipeline.status.state == "Fault"
    assert "release failed" in pipeline.status.reason


def test_second_cycle_reopens_position_observation(recipe):
    pipeline = ReplayPipeline(lambda: SyntheticSource(frame_count=180), recipe, realtime=False)
    pipeline.start()
    pipeline.join(10)
    results = []
    while not pipeline.events.empty():
        results.append(pipeline.events.get_nowait())
    assert [result.observed_cycles for result in results if result.inspection_ready] == [1, 2]
