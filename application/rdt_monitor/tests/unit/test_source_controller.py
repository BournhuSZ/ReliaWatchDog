# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Source ownership, exact-event polling and configuration commit regressions."""

import json
from dataclasses import FrozenInstanceError, replace
from queue import Queue
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

import rdt_monitor.controller as coordination
from rdt_monitor.camera import CameraFrame, CameraStatus, MarkerTrial
from rdt_monitor.controller import MonitorController
from rdt_monitor.domain import SyncConfig, SyncResult, SyncState
from rdt_monitor.lifecycle import InspectionOutcome, RunMode, RunState
from rdt_monitor.live import CycleDetection
from rdt_monitor.recipes import SpecimenDetails, StoredRecipe, save_inspection_recipe, save_recipe
from rdt_monitor.replay import Region, ReplayPipeline, ReplayRecipe, SyntheticSource


@pytest.fixture
def recipe():
    return ReplayRecipe(SyncConfig(1, 0.2, 4), Region(0, 0, 0.5, 1), Region(0.5, 0, 0.5, 1))


class Camera:
    def __init__(self, index):
        self.status = CameraStatus("Streaming", "Preview only", index, width=32, height=24)
        self.frames = Queue(maxsize=1)
        self.trial = None
        self.alive = False
        self.releases = 0
        self.attach_error = False

    def start(self):
        self.alive = True

    def stop(self):
        self.releases += 1
        self.alive = False
        self.status = replace(self.status, state="Stopped")

    def start_trial(self, recipe, capture=None):
        if self.attach_error:
            raise RuntimeError("Injected source attachment failure")
        self.trial = MarkerTrial(recipe)
        self.trial.set_cycle_capture(capture)

    def stop_trial(self):
        trial = self.trial
        self.trial = None
        if trial is not None:
            trial.request_stop()
        return trial


@pytest.fixture
def source(recipe):
    controller = MonitorController()
    assert controller.connect_camera(2, factory=Camera)
    return controller


def setup(recipe):
    return StoredRecipe("actual-specimen", recipe, 32, 24)


def event(number):
    return SyncResult(number * 10, float(number), SyncState.TRACKING, number, True, True, "Cycle complete")


def test_exclusive_source_commands_and_immediate_disconnect_state(source, recipe):
    assert not source.connect_camera(3, factory=Camera)
    source.source = "Synthetic marker fixture / not camera footage"
    source.synthetic = True
    assert source.start_replay("replay", recipe, 1) is None
    assert source.camera.status.index == 2 and source.pipeline is None
    assert source.disconnect_camera(1)
    assert source.snapshot().can_connect and source.snapshot().can_load_recipe
    assert source.camera.releases == 1
    assert not source.disconnect_camera(2)


def test_setup_is_committed_before_any_cycle_callback(source, recipe, tmp_path):
    original_attach = source.camera.start_trial
    committed = []

    def attach(settings, capture=None):
        files = list(tmp_path.glob("run-*-setup.json"))
        assert len(files) == 1
        committed.append(json.loads(files[0].read_text()))
        original_attach(settings, capture)

    source.camera.start_trial = attach
    record = source.start_camera_run(setup(recipe), None, tmp_path)
    assert committed[0]["run_id"] == record.run_id
    assert committed[0]["setup"]["specimen_id"] == "actual-specimen"
    assert committed[0]["qualified"] is False
    assert source.snapshot().run.mode == RunMode.DEVELOPMENT
    assert not source.snapshot().can_start
    assert source.snapshot().can_stop


def test_setup_write_failure_never_attaches_or_starts(source, recipe, tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("Injected storage failure")

    monkeypatch.setattr(coordination, "save_run_setup", fail)
    with pytest.raises(OSError, match="storage failure"):
        source.start_camera_run(setup(recipe), None, tmp_path)
    assert source.camera.trial is None
    assert source.run_record is None
    assert source.snapshot().run.state == RunState.STOPPED


def test_recipe_selection_and_canceled_candidate_preserve_actual_specimen(source, recipe, tmp_path):
    path = tmp_path / "recipe.json"
    original = StoredRecipe("training-specimen", recipe, 32, 24)
    save_recipe(path, original)
    source.select_recipe(path)
    candidate = source.prepare_setup("actual-specimen", recipe, 32, 24, SpecimenDetails(), source.loaded_recipe)
    assert candidate.specimen_id == "actual-specimen"
    assert source.loaded_recipe[0] == original
    assert source.run_record is None and source.camera.trial is None
    save_recipe(path, replace(original, specimen_id="changed"))
    with pytest.raises(ValueError, match="changed during setup"):
        source.start_camera_run(candidate, source.loaded_recipe, tmp_path / "results")
    assert source.run_record is None and source.camera.trial is None


def test_camera_poll_admits_events_even_without_preview_and_only_once(source, recipe, tmp_path):
    source.start_camera_run(setup(recipe), None, tmp_path)
    for number in range(1, 4):
        source.camera.trial.events.put_nowait(event(number))
    batch = source.poll_camera()
    assert [update.record.cycle for update in batch.observations] == [1, 2, 3]
    assert batch.frame is None
    assert source.lifecycle.snapshot.observed == source.lifecycle.snapshot.uninspected == 3
    assert source.poll_camera().observations == ()
    assert source.lifecycle.snapshot.observed == 3


def test_accounting_error_is_explicit_without_losing_other_events(source, recipe, tmp_path):
    source.start_camera_run(setup(recipe), None, tmp_path)
    trial = source.camera.trial
    for observation in (event(1), event(1), event(2)):
        trial.events.put_nowait(observation)
    batch = source.poll_camera()
    assert batch.observations[0].record.cycle == 1
    assert "sequential" in batch.observations[1].error
    assert batch.observations[2].record is None
    assert "not accepting" in batch.observations[2].error
    assert source.lifecycle.snapshot.state == RunState.DEGRADED
    assert source.lifecycle.snapshot.observed == 1


def test_disconnect_waits_for_callback_barrier_then_releases_once(source, recipe, tmp_path):
    source.start_camera_run(setup(recipe), None, tmp_path)
    # Inject the source's nonblocking stop barrier, independently of a driver thread.
    trial = SimpleNamespace(events=Queue(maxsize=4), stopped=False)
    trial.events.put_nowait(event(1))
    source.camera.stop_trial = lambda: trial
    assert source.disconnect_camera(1)
    assert source.camera.alive
    first = source.poll_stopping(2)
    assert first.stopped is None and len(first.observations) == 1
    assert not source.snapshot().can_start and not source.snapshot().can_connect
    trial.stopped = True
    final = source.poll_stopping(2)
    assert final.stopped.snapshot.observed == 1
    assert source.camera.releases == 1
    assert source.poll_stopping(3).stopped is None
    assert source.camera.releases == 1


def test_replay_uses_same_admission_and_ends_without_camera(recipe, monkeypatch):
    # One complete 90-frame fixture cycle finishes its return by frame 60.
    # Bound generation to its first 80 frames; this is an admission/EOF check, not a throughput test.
    monkeypatch.setattr(coordination, "SyntheticSource", lambda: SyntheticSource(frame_count=80))
    controller = MonitorController()
    controller.source = "Synthetic fixture"
    controller.synthetic = True
    recipe = replace(recipe, marker=Region(0.05, 0.1, 0.1, 0.1), position=Region(0.8, 0.1, 0.1, 0.1))
    record = controller.start_replay(
        "replay-specimen",
        recipe,
        7,
        factory=lambda factory, settings: ReplayPipeline(factory, settings, realtime=False),
    )
    controller.pipeline.join(5)
    assert not controller.pipeline.alive
    batch = controller.poll_replay(1)
    assert record.recipe_version == "replay-v7"
    assert batch.observations and batch.preview is not None
    assert controller.lifecycle.snapshot.observed == 1
    assert controller.lifecycle.snapshot.inspected == 0
    assert controller.lifecycle.snapshot.state == RunState.STOPPED
    assert controller.camera is None


def test_snapshot_is_immutable_and_closing_rejects_new_commands(source, recipe, tmp_path):
    snapshot = source.snapshot()
    with pytest.raises(FrozenInstanceError):
        snapshot.can_start = False
    source.closing = True
    assert not source.snapshot().can_start
    assert not source.connect_camera(3, factory=Camera)
    with pytest.raises(RuntimeError, match="not ready"):
        source.start_camera_run(setup(recipe), None, tmp_path)


def test_close_budget_is_bounded_and_repeated_requests_do_not_extend_deadline(source):
    source.camera.stop = lambda: None  # Simulate a driver that has not exited yet.
    assert not source.close_ready(1)
    assert source.request_close(1)
    assert not source.request_close(2)
    assert not source.snapshot().can_connect
    assert not source.close_ready(3.9)
    assert source.close_ready(4)
    assert source.close_requested_at == 1
    source.camera.alive = False
    assert source.close_ready(2)
    assert not source.close_ready(2, collecting=True)


def test_model_worker_is_retained_if_camera_attachment_fails(source, recipe, tmp_path):
    model = tmp_path / "trained.xml"
    model.write_bytes(b"contract model")
    model.with_suffix(".bin").write_bytes(b"contract weights")
    path = tmp_path / "recipe.json"
    save_inspection_recipe(path, setup(recipe), model, Region(0.1, 0.1, 0.8, 0.8), 0.5)
    bound = source.select_recipe(path)
    source.camera.attach_error = True
    calls = []
    worker = SimpleNamespace(
        start=lambda: None, stop=lambda **kwargs: calls.append(kwargs), capture=lambda *args: None, finished=Event()
    )
    with pytest.raises(RuntimeError, match="attachment failure"):
        source.start_camera_run(
            bound, source.loaded_recipe, tmp_path / "results", worker_factory=lambda *a, **kw: worker
        )
    assert calls == [{"cancel": True}]
    assert source.retired_worker is worker
    assert source.run_record is None
    assert not source.snapshot().can_start


def test_poll_admits_ticket_before_settling_queued_result(source, recipe, tmp_path):
    model = tmp_path / "trained.xml"
    model.write_bytes(b"contract model")
    model.with_suffix(".bin").write_bytes(b"contract weights")
    path = tmp_path / "recipe.json"
    save_inspection_recipe(path, setup(recipe), model, Region(0.1, 0.1, 0.8, 0.8), 0.5)
    bound = source.select_recipe(path)
    worker = SimpleNamespace(
        binding=bound.detector,
        start=lambda: None,
        stop=lambda **kwargs: None,
        capture=lambda *args: None,
        finished=Event(),
        results=Queue(maxsize=4),
        failure="",
    )
    source.start_camera_run(bound, source.loaded_recipe, tmp_path / "results", worker_factory=lambda *a, **kw: worker)
    observation = event(1)
    source.camera.trial.events.put_nowait(observation)
    worker.results.put_nowait(CycleDetection(observation, SimpleNamespace(score=0.1), tmp_path / "result.json"))
    pixels = np.zeros((24, 32, 3), dtype=np.uint8)
    source.camera.frames.put_nowait(CameraFrame(99, 9, pixels))
    batch = source.poll_camera()
    assert batch.observations[0].record.frame_id == 10
    assert batch.inspections.updates[0].ticket.frame_id == 10
    assert batch.inspections.updates[0].verdict == InspectionOutcome.NO_DEFECT
    assert not batch.inspections.rejected
    assert batch.frame.frame_id == 99
    assert source.last_frame is batch.frame
    assert source.lifecycle.snapshot.inspected == 1
