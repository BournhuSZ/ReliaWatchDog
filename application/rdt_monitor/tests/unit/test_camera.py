# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
from threading import Event, Thread, current_thread

import cv2
import numpy as np
import pytest

from rdt_monitor.camera import CameraFrame, CameraPreview, MarkerTrial, create_camera
from rdt_monitor.domain import SyncConfig, SyncState
from rdt_monitor.replay import MarkerAppearance, Region, ReplayRecipe


@pytest.mark.parametrize("starts_present", [False, True])
def test_actual_pink_mark_sequence_counts_one_fresh_cycle(starts_present):
    directory = Path(__file__).parents[1] / "fixtures"
    reference = cv2.imread(str(directory / "pink-marks-reference.png"))
    observed = cv2.imread(str(directory / "pink-marks-observed.png"))
    assert reference is not None and observed is not None
    recipe = ReplayRecipe(
        SyncConfig(2, 0.2, 4),
        Region(9 / 200, 6 / 185, 46 / 200, 49 / 185),
        Region(152 / 200, 129 / 185, 45 / 200, 47 / 185),
    )
    recipe = ReplayRecipe(
        recipe.synchronization,
        recipe.marker,
        recipe.position,
        marker_appearance=MarkerAppearance.capture(reference, recipe.marker),
        position_appearance=MarkerAppearance.capture(reference, recipe.position),
    )
    blank = observed.copy()
    for region in (recipe.marker, recipe.position):
        left, top, right, bottom = region.pixels(blank)
        blank[top:bottom, left:right] = observed[60 : 60 + bottom - top, 65 : 65 + right - left]
    session = MarkerTrial(recipe)
    snapshots = []
    session.set_cycle_capture(lambda image, result: snapshots.append((image.copy(), result)))
    images = (observed, observed, blank, blank, observed, observed, observed)
    if not starts_present:
        images = (blank, blank, *images)
    for index, image in enumerate(images):
        session.observe(CameraFrame(index, 10 + index / 30, image))
        if index < (2 if starts_present else 4):
            assert session.result.observed_cycles == 0
            assert "both marks" in session.result.reason
    assert session.result.observed_cycles == 1
    assert session.result.inspection_ready is False
    assert any(event.inspection_ready for event in list(session.events.queue))
    assert len(snapshots) == 1
    image, result = snapshots[0]
    assert result.observed_cycles == 1
    assert np.array_equal(image, images[result.frame_id])
    session.set_cycle_capture(None)


def test_actual_mark_reference_rejects_unmarked_surface_and_wrong_shape():
    directory = Path(__file__).parents[1] / "fixtures"
    reference = cv2.imread(str(directory / "pink-marks-reference.png"))
    observed = cv2.imread(str(directory / "pink-marks-observed.png"))
    marker = Region(9 / 200, 6 / 185, 46 / 200, 49 / 185)
    position = Region(152 / 200, 129 / 185, 45 / 200, 47 / 185)
    start = MarkerAppearance.capture(reference, marker)
    other = MarkerAppearance.capture(reference, position)
    assert start.score(observed, marker) > 0.9
    assert other.score(observed, position) > 0.9
    assert start.score(observed, position) < 0.4
    assert other.score(observed, marker) < 0.4
    unmarked = Region(65 / 200, 60 / 185, 46 / 200, 49 / 185)
    assert start.score(observed, unmarked) < 0.4
    assert other.score(observed, unmarked) < 0.4
    with pytest.raises(ValueError, match="insufficient contrast"):
        MarkerAppearance.capture(np.full((185, 200, 3), 210, dtype=np.uint8), marker)
    with pytest.raises(ValueError, match="both"):
        ReplayRecipe(SyncConfig(2, 0.2, 4), marker, position, marker_appearance=start)


@pytest.mark.parametrize(
    "region", [Region(9 / 200, 6 / 185, 46 / 200, 49 / 185), Region(152 / 200, 129 / 185, 45 / 200, 47 / 185)]
)
@pytest.mark.parametrize(
    "angle,scale,shift_x,shift_y", [(0, 1, 3, -2), (12, 1, 0, 0), (0, 0.9, 0, 0), (0, 1.1, 0, 0), (12, 0.9, 2, -1)]
)
def test_captured_marks_allow_bounded_return_pose_changes(region, angle, scale, shift_x, shift_y):
    directory = Path(__file__).parents[1] / "fixtures"
    reference = cv2.imread(str(directory / "pink-marks-reference.png"))
    observed = cv2.imread(str(directory / "pink-marks-observed.png"))
    appearance = MarkerAppearance.capture(reference, region)
    left, top, right, bottom = region.pixels(observed)
    width, height = right - left, bottom - top
    transform = cv2.getRotationMatrix2D(((width - 1) / 2, (height - 1) / 2), angle, scale)
    transform[:, 2] += (shift_x, shift_y)
    observed[top:bottom, left:right] = cv2.warpAffine(
        observed[top:bottom, left:right], transform, (width, height), borderMode=cv2.BORDER_REFLECT_101
    )
    assert appearance.score(observed, region) >= 0.8


class FakeCamera:
    def __init__(self, *, opens=True):
        self.opens = opens
        self.owner = None
        self.entered_read = Event()
        self.finish_read = Event()
        self.closed = Event()
        self.reads = 0
        self.buffer = np.full((24, 32, 3), 80, dtype=np.uint8)

    def open(self):
        self.owner = current_thread()
        return self.opens

    def get_properties(self):
        assert current_thread() is self.owner
        return {"backend": 700, "fps": 0}

    def read(self):
        assert current_thread() is self.owner
        self.reads += 1
        if self.reads > 2:
            self.entered_read.set()
            assert self.finish_read.wait(5)
            raise RuntimeError("USB disconnected")
        return self.buffer

    def close(self):
        assert current_thread() is self.owner
        self.closed.set()


def test_existing_api_is_reused():
    camera = create_camera(0)
    assert type(camera).__name__ == "KeyenceNRCM01Camera"
    assert camera.camera_index == 0


def test_capture_is_worker_owned_and_preview_is_bounded():
    camera = FakeCamera()
    worker = CameraPreview(0, lambda index: camera)
    worker.start()
    try:
        assert camera.entered_read.wait(5)
        assert camera.owner is not current_thread()
        assert worker.status.state == "Streaming"
        assert worker.status.reported_fps == 0
        assert worker.frames.qsize() == 1
        frame = worker.frames.get_nowait()
        assert frame.frame_id == 1
        assert frame.image.shape == (24, 32, 3)
        assert not frame.image.flags.writeable
        camera.buffer[:] = 0
        assert np.all(frame.image == 80)
    finally:
        worker.stop()
        camera.finish_read.set()
        worker.join(5)
    assert camera.closed.is_set()


@pytest.mark.parametrize("failure", ["timeout", "ambiguous", "geometry", "recognition", "backlog"])
def test_synchronization_failure_preserves_capture_and_requires_explicit_restart(failure):
    now = [10.0]
    begin = Event()
    settled = Event()
    finish = Event()

    class ContinuingCamera(FakeCamera):
        def read(self):
            assert current_thread() is self.owner
            self.reads += 1
            now[0] = 10 + self.reads / 10
            if self.reads == 2:
                self.entered_read.set()
                assert begin.wait(5)
            if self.reads == (301 if failure == "backlog" else 51):
                settled.set()
                assert finish.wait(5)
            if failure == "ambiguous" and self.reads >= 3:
                image = self.buffer.copy()
                image[:12, :16] = 255
                return image
            if failure == "geometry" and self.reads >= 3:
                return np.zeros((48, 64, 3), dtype=np.uint8)
            if failure == "backlog":
                return np.full_like(self.buffer, 255 if self.reads % 2 == 0 else 0)
            if failure == "timeout":
                return np.full_like(self.buffer, 0 if self.reads == 3 else 255)
            return self.buffer

        def close(self):
            super().close()
            settled.set()

    camera = ContinuingCamera()
    worker = CameraPreview(0, lambda index: camera, clock=lambda: now[0])
    worker.start()
    try:
        assert camera.entered_read.wait(5)
        recipe = trial_recipe()
        if failure == "recognition":
            recipe = ReplayRecipe(recipe.synchronization, Region(0, 0, 0.0001, 1), recipe.position)
        worker.start_trial(recipe)
        failed_session = worker.trial
        begin.set()
        assert settled.wait(5)
        assert worker.status.state == "Streaming", worker.status.reason
        assert worker.alive and not camera.closed.is_set()
        assert worker.trial is failed_session
        assert failed_session.result.state == SyncState.LOST
        assert not failed_session.result.inspection_ready
        assert worker.frames.get_nowait().frame_id >= 48
        assert failed_session.result.observed_cycles == (129 if failure == "backlog" else int(failure == "timeout"))
        worker.start_trial(trial_recipe())
        assert worker.trial is not failed_session
        assert worker.trial.result is None
    finally:
        worker.stop()
        begin.set()
        finish.set()
        worker.join(5)
    assert camera.closed.is_set()
    assert not worker.alive


def test_open_failure_releases_device_without_auto_reconnect():
    camera = FakeCamera(opens=False)
    worker = CameraPreview(0, lambda index: camera)
    worker.start()
    worker.join(5)
    assert worker.status.state == "Fault"
    assert "cannot open" in worker.status.reason
    assert camera.closed.is_set()
    assert camera.reads == 0


def test_disconnect_fault_is_latched():
    camera = FakeCamera()
    worker = CameraPreview(0, lambda index: camera)
    worker.start()
    assert camera.entered_read.wait(5)
    camera.finish_read.set()
    worker.join(5)
    assert worker.status.state == "Fault"
    assert "USB disconnected" in worker.status.reason
    assert camera.closed.is_set()
    with pytest.raises(RuntimeError, match="new connection"):
        worker.start()


def test_stalled_read_is_faulted_without_waiting_for_driver():
    now = [10.0]
    camera = FakeCamera()
    worker = CameraPreview(0, lambda index: camera, clock=lambda: now[0], stale_seconds=2)
    worker.start()
    try:
        assert camera.entered_read.wait(5)
        now[0] = 12.1
        assert worker.status.state == "Fault"
        assert "stale" in worker.status.reason
        assert worker.alive
    finally:
        camera.finish_read.set()
        worker.join(5)
    assert camera.closed.is_set()
    assert worker.status.state == "Fault"


def test_stalled_open_is_visible_without_blocking_ui_status():
    now = [10.0]
    entered = Event()
    finish = Event()

    class OpeningCamera(FakeCamera):
        def open(self):
            self.owner = current_thread()
            entered.set()
            assert finish.wait(5)
            return True

    camera = OpeningCamera()
    worker = CameraPreview(0, lambda index: camera, clock=lambda: now[0], opening_seconds=10)
    worker.start()
    try:
        assert entered.wait(5)
        now[0] = 20.1
        assert worker.status.state == "Fault"
        assert "opening timed out" in worker.status.reason
        assert worker.alive
        assert worker.frames.empty()
    finally:
        finish.set()
        worker.join(5)
    assert camera.closed.is_set()
    assert camera.reads == 0


@pytest.mark.parametrize("index", [-1, True, 0.5])
def test_invalid_camera_index_rejected(index):
    with pytest.raises(ValueError):
        CameraPreview(index)


def trial_recipe():
    return ReplayRecipe(SyncConfig(1, 0.2, 4), Region(0, 0, 0.5, 1), Region(0.5, 0, 0.5, 1))


def marker_frame(index, *, present=False, position=False, timestamp=None):
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    if present:
        image[:, :16] = 255
    if position:
        image[:, 16:] = 255
    image.flags.writeable = False
    return CameraFrame(index, timestamp if timestamp is not None else 10 + index / 30, image)


def test_trial_requires_fresh_transition_and_emits_position_once():
    trial = MarkerTrial(trial_recipe())
    trial.observe(marker_frame(0, present=True, position=True))
    assert trial.result.observed_cycles == 0
    assert trial.events.empty()
    trial.observe(marker_frame(1))
    trial.observe(marker_frame(2, present=True, position=True))
    event = trial.events.get_nowait()
    assert event.cycle_started and event.inspection_ready
    trial.observe(marker_frame(3, present=True, position=True))
    assert trial.result.observed_cycles == 1
    assert trial.events.empty()


def test_stop_bars_future_observations_and_capture_callbacks():
    trial = MarkerTrial(trial_recipe())
    trial.observe(marker_frame(0, present=True, position=True))
    trial.observe(marker_frame(1))
    before = trial.result
    trial.request_stop()
    assert trial.stopped
    trial.observe(marker_frame(2, present=True, position=True))
    assert trial.result == before
    assert trial.events.empty()
    with pytest.raises(RuntimeError, match="stopped trial"):
        trial.set_cycle_capture(lambda image, result: None)


def test_stop_does_not_wait_for_inflight_callback_and_preserves_accepted_event():
    trial = MarkerTrial(trial_recipe())
    entered = Event()
    release = Event()
    completed = Event()

    def capture(image, result):
        entered.set()
        assert release.wait(5)
        completed.set()

    trial.set_cycle_capture(capture)
    trial.observe(marker_frame(0, present=True, position=True))
    trial.observe(marker_frame(1))
    worker = Thread(target=trial.observe, args=(marker_frame(2, present=True, position=True),))
    worker.start()
    try:
        assert entered.wait(5)
        assert trial.result.observed_cycles == 1
        trial.request_stop()
        trial.request_stop()
        assert not trial.stopped
        assert trial.events.get_nowait().observed_cycles == 1
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert completed.is_set() and trial.stopped
    before = trial.result
    trial.observe(marker_frame(3))
    assert trial.result == before


def test_uncertain_position_does_not_establish_disappearance():
    trial = MarkerTrial(trial_recipe())
    trial.observe(marker_frame(0, present=True, position=True))
    uncertain = marker_frame(1).image.copy()
    uncertain[:12, 16:] = 255
    trial.observe(CameraFrame(1, 10 + 1 / 30, uncertain))
    trial.observe(marker_frame(2, present=True, position=True))
    assert trial.result.observed_cycles == 0
    assert trial.events.empty()


@pytest.mark.parametrize("kind", ["timing", "identity", "ambiguous", "timeout", "geometry"])
def test_trial_faults_are_latched(kind):
    trial = MarkerTrial(trial_recipe())
    trial.observe(marker_frame(0, present=True, position=True))
    frame = marker_frame(2 if kind == "identity" else 1, timestamp=15 if kind == "timing" else None)
    if kind == "ambiguous":
        image = frame.image.copy()
        image[:12, :16] = 255
        for index in range(1, 41):
            trial.observe(CameraFrame(index, 10 + index / 10, image))
        frame = CameraFrame(41, 14.15, image)
    if kind == "geometry":
        frame = CameraFrame(1, frame.received_at, np.zeros((48, 64, 3), dtype=np.uint8))
    if kind == "timeout":
        trial.observe(marker_frame(1))
        trial.observe(marker_frame(2, present=True, position=True))
        for index in range(3, 121):
            trial.observe(marker_frame(index, present=True, position=True))
        frame = marker_frame(121, timestamp=14.15, present=True, position=True)
    with pytest.raises(RuntimeError):
        trial.observe(frame)
    assert trial.result.state == SyncState.LOST
    with pytest.raises(RuntimeError):
        trial.observe(marker_frame(122))


def test_trial_event_overflow_is_not_silent():
    trial = MarkerTrial(trial_recipe(), event_capacity=1)
    trial.observe(marker_frame(0, present=True, position=True))
    trial.observe(marker_frame(1))
    trial.observe(marker_frame(2, present=True, position=True))
    trial.observe(marker_frame(3))
    with pytest.raises(RuntimeError, match="backlog overflow"):
        trial.observe(marker_frame(4, present=True, position=True))
    assert trial.result.state == SyncState.LOST
    assert trial.events.qsize() == 1


def test_trial_must_be_explicit_and_cannot_replace_locked_settings():
    camera = FakeCamera()
    worker = CameraPreview(0, lambda index: camera)
    assert worker.trial is None
    with pytest.raises(RuntimeError, match="streaming"):
        worker.start_trial(trial_recipe())
    worker.start()
    try:
        assert camera.entered_read.wait(5)
        worker.start_trial(trial_recipe())
        assert worker.trial.recipe == trial_recipe()
        with pytest.raises(AttributeError):
            worker.trial.recipe = trial_recipe()
        stopped = worker.stop_trial()
        assert stopped is not None and stopped.recipe == trial_recipe()
        assert worker.trial is None
        worker.start_trial(trial_recipe())
        assert worker.trial.recipe == trial_recipe()
        with pytest.raises(RuntimeError, match="reconnect"):
            worker.start_trial(trial_recipe())
    finally:
        worker.stop()
        camera.finish_read.set()
        worker.join(5)


def test_worker_observes_every_trial_frame_before_preview_replacement():
    now = [10.0]
    begin = Event()
    processed = Event()
    finish = Event()
    observations = [marker_frame(index, present=index in (0, 2, 4), position=index in (0, 2, 4)) for index in range(5)]

    class SequenceCamera(FakeCamera):
        def read(self):
            assert current_thread() is self.owner
            self.reads += 1
            now[0] = 10 + self.reads / 30
            if self.reads == 1:
                return self.buffer
            if self.reads == 2:
                self.entered_read.set()
                assert begin.wait(5)
            if self.reads <= 6:
                return observations[self.reads - 2].image
            processed.set()
            assert finish.wait(5)
            return self.buffer

    camera = SequenceCamera()
    worker = CameraPreview(0, lambda index: camera, clock=lambda: now[0])
    worker.start()
    try:
        assert camera.entered_read.wait(5)
        worker.start_trial(trial_recipe())
        begin.set()
        assert processed.wait(5)
        assert worker.trial.result.observed_cycles == 2
        assert worker.trial.events.qsize() == 2
        assert worker.frames.qsize() == 1
        assert worker.frames.get_nowait().frame_id == 5
    finally:
        worker.stop()
        begin.set()
        finish.set()
        worker.join(5)
    assert camera.closed.is_set()
