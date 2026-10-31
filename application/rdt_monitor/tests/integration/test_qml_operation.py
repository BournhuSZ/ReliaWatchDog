# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Confirmed Qt Quick operation through real controller/inspection/evidence boundaries."""

import json
from dataclasses import replace
from queue import Empty, Queue
from time import monotonic
from types import SimpleNamespace

import numpy as np
import pytest
import shiboken6
from PySide6.QtCore import QSize, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import rdt_monitor.controller as coordination
import rdt_monitor.dialogs as native_dialogs
import rdt_monitor.presentation as presentation
from rdt_monitor.camera import CameraFrame, CameraStatus, MarkerTrial, SynchronizationFault
from rdt_monitor.controller import MonitorController
from rdt_monitor.dialogs import SetupChoice
from rdt_monitor.domain import SyncConfig
from rdt_monitor.live import LiveInspection
from rdt_monitor.palette import colors
from rdt_monitor.presentation import MonitorViewModel
from rdt_monitor.qml_app import create_engine
from rdt_monitor.recipes import SpecimenDetails, StoredRecipe, load_recipe, save_inspection_recipe, save_recipe
from rdt_monitor.replay import Region, ReplayRecipe


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


class Camera:
    def __init__(self):
        self.alive = True
        self.clock = monotonic()
        self.status = CameraStatus(
            "Streaming", "Injected acquisition", 0, width=32, height=24, last_received_at=monotonic()
        )
        self.frames = Queue(maxsize=1)
        self.trial = None
        self.frame_id = 0

    def send(self, value):
        self.frame_id += 1
        # Explicitly increasing source times, independent of how quickly tests execute.
        self.clock += 0.05
        now = self.clock
        image = np.full((24, 32, 3), value, dtype=np.uint8)
        frame = CameraFrame(self.frame_id, now, image)
        self.status = replace(self.status, last_received_at=now)
        try:
            if self.trial is not None:
                self.trial.observe(frame)
        finally:
            try:
                self.frames.get_nowait()
            except Empty:
                pass
            self.frames.put_nowait(frame)
        return frame

    def start_trial(self, recipe, capture=None):
        self.trial = MarkerTrial(recipe)
        self.trial.set_cycle_capture(capture)

    def stop_trial(self):
        trial, self.trial = self.trial, None
        if trial is not None:
            trial.request_stop()
        return trial

    def stop(self):
        self.alive = False
        self.status = replace(self.status, state="Stopped")


class Dialogs:
    def __init__(self, camera, recipe):
        self.camera, self.recipe = camera, recipe
        self.selection = None
        self.cancel = False
        self.calls = 0
        self.during = lambda: None

    def apply_theme(self, light):
        pass

    def choose_recipe(self):
        return None

    def confirm_setup(self, recipe, selection, specimen_id, frame):
        self.calls += 1
        self.during()
        if self.cancel:
            return None
        self.selection = selection
        return SetupChoice(specimen_id, self.recipe, SpecimenDetails(), selection, 32, 24)


@pytest.fixture
def operation(application, tmp_path, monkeypatch):
    recipe = ReplayRecipe(SyncConfig(1, 0.2, 4), Region(0, 0, 0.5, 1), Region(0.5, 0, 0.5, 1))
    camera = Camera()
    monkeypatch.setattr(presentation, "monotonic", lambda: camera.clock)
    controller = MonitorController()
    controller.mode, controller.camera = "camera", camera
    dialogs = Dialogs(camera, recipe)
    monitor = MonitorViewModel(controller, start_timer=False, dialogs=dialogs, output_root=tmp_path / "results")
    monitor.setSpecimenId("A-002")
    camera.send(255)
    monitor.poll()
    session = SimpleNamespace(
        monitor=monitor,
        camera=camera,
        controller=controller,
        dialogs=dialogs,
        recipe=recipe,
        root=tmp_path,
        selection=tmp_path / "counting.json",
    )
    save_recipe(session.selection, StoredRecipe("A-001", recipe, 32, 24))
    yield session
    worker = controller.worker or controller.retired_worker
    monitor.stopMonitoring()
    if worker is not None:
        worker.stop()
        assert worker.finished.wait(5)
    monitor.poll()
    camera.stop()
    monitor.dispose()


def complete_cycle(session):
    session.camera.send(255)
    session.camera.send(0)
    return session.camera.send(255)


def wait_result(session):
    worker = session.controller.worker
    result = worker.results.get(timeout=5)
    worker.results.put_nowait(result)
    session.monitor.poll()
    return result


def bind_model(session, scores):
    model = session.root / "model.xml"
    model.write_bytes(b"contract model")
    model.with_suffix(".bin").write_bytes(b"contract weights")
    path = session.root / "bound.json"
    save_inspection_recipe(
        path, StoredRecipe("A-001", session.recipe, 32, 24), model, Region(0.25, 0.25, 0.5, 0.5), 0.5
    )
    score_values = iter(scores)

    def predict(image):
        score = next(score_values)
        return SimpleNamespace(
            pred_score=np.array([score], dtype=np.float32), anomaly_map=np.full((1, 1, 4, 4), score, dtype=np.float32)
        )

    session.monitor._worker_factory = lambda binding, recipe_path, folder, **kwargs: LiveInspection(
        binding, recipe_path, folder, loader=lambda _: SimpleNamespace(predict=predict), **kwargs
    )
    session.monitor.loadRecipe(str(path))
    return path


def test_loading_recipe_preserves_serial_and_requires_confirmation(operation):
    vm = operation.monitor
    vm.loadRecipe(str(operation.selection))
    assert vm.specimenId == "A-002"
    assert operation.controller.run_record is None
    assert operation.camera.trial is None
    operation.dialogs.cancel = True
    vm.startInspection()
    assert operation.dialogs.calls == 1
    assert operation.controller.run_record is None
    assert not (operation.root / "results").exists()
    operation.dialogs.cancel = False
    operation.dialogs.during = lambda: vm.startInspection()  # Repeated command while modal.
    vm.startInspection()
    assert operation.dialogs.calls == 2
    assert operation.controller.run_record.specimen_id == "A-002"
    record = operation.controller.run_record
    vm.startInspection()
    assert operation.dialogs.calls == 2 and operation.controller.run_record == record
    saved = next((operation.root / "results").glob("run-*-setup.json"))
    assert json.loads(saved.read_text())["setup"]["specimen_id"] == "A-002"
    assert not vm.status["canEditSpecimen"] and not vm.status["canLoadRecipe"]
    vm.setSpecimenId("changed-during-run")
    assert vm.specimenId == "A-002"
    complete_cycle(operation)
    vm.poll()
    assert vm.status["observed"] == 1 and vm.status["inspected"] == 0
    assert not vm.status["coverageWarning"]
    vm.stopMonitoring()
    assert operation.camera.alive and operation.camera.trial is None
    assert vm.status["runState"] == "Stopped"


def test_picker_cancellation_and_close_during_setup_preserve_session(operation):
    vm = operation.monitor
    vm.loadRecipe(str(operation.selection))
    original = operation.controller.loaded_recipe
    vm.chooseRecipe()
    assert operation.controller.loaded_recipe == original and vm.specimenId == "A-002"
    operation.dialogs.cancel = True
    checked = []

    def during():
        checked.append(vm.requestClose())
        assert not vm.status["canStart"] and not vm.status["canLoadRecipe"]

    operation.dialogs.during = during
    vm.startInspection()
    assert checked == [False]
    assert operation.camera.alive and not operation.controller.closing
    assert operation.controller.run_record is None


def test_synchronization_fault_requires_confirmed_restart_with_new_run_identity(operation):
    vm = operation.monitor
    vm.loadRecipe(str(operation.selection))
    vm.startInspection()
    original = operation.controller.run_record
    # A deterministic source gap faults synchronization without releasing the preview device.
    operation.camera.send(255)
    operation.camera.clock += 1
    with pytest.raises(SynchronizationFault):
        operation.camera.send(0)
    vm.poll()
    assert vm.status["restart"] and vm.status["fault"] and operation.camera.alive
    operation.dialogs.cancel = True
    vm.startInspection()
    assert operation.controller.run_record == original
    operation.dialogs.cancel = False
    vm.startInspection()
    assert operation.controller.run_record.run_id != original.run_id
    assert vm.status["observed"] == 0 and not vm.status["fault"]


@pytest.mark.parametrize("failure", ["write", "source-change", "stale"])
def test_failed_start_never_attaches_or_reports_success(operation, monkeypatch, failure):
    vm = operation.monitor
    vm.loadRecipe(str(operation.selection))
    if failure == "write":

        def fail(*args):
            raise OSError("Injected setup storage failure")

        monkeypatch.setattr(coordination, "save_run_setup", fail)
    elif failure == "source-change":
        operation.dialogs.during = lambda: save_recipe(
            operation.selection, replace(load_recipe(operation.selection), specimen_id="tampered")
        )
    else:
        operation.controller.last_frame = replace(operation.controller.last_frame, received_at=monotonic() - 5)
    vm.startInspection()
    assert operation.controller.run_record is None and operation.camera.trial is None
    assert vm.status["fault"]
    assert vm.status["verdictTone"] == "neutral"
    assert vm.status["evidence"] != "Saved"


def test_exact_cycle_verdict_heatmap_evidence_and_latch_survive_later_normal(operation):
    vm = operation.monitor
    bind_model(operation, [0.9, 0.1])
    vm.startInspection()
    first = complete_cycle(operation)
    detection = wait_result(operation)
    assert vm.status["verdictTone"] == "defect" and vm.status["defects"] == 1
    assert vm.status["resultFrame"] == first.frame_id
    assert vm.status["evidence"] == "Saved"
    assert detection.evidence.is_file()
    overlay = vm.provider.requestImage(vm.roiSource.removeprefix("image://frames/"), QSize(), QSize())
    vm.setOverlayEnabled(False)
    raw = vm.provider.requestImage(vm.roiSource.removeprefix("image://frames/"), QSize(), QSize())
    assert raw.pixelColor(0, 0).red() == 255 and overlay.pixelColor(0, 0) != raw.pixelColor(0, 0)
    operation.camera.send(0)
    vm.poll()
    assert vm.status["resultFrame"] == first.frame_id
    second = operation.camera.send(255)
    wait_result(operation)
    assert vm.status["verdictTone"] == "normal" and vm.status["defects"] == 1
    assert vm.status["resultFrame"] == second.frame_id and vm.status["inspected"] == 2
    old_roi = vm.roiSource
    vm.stopMonitoring()
    worker = operation.controller.worker
    if worker is not None:
        assert worker.finished.wait(5)
        vm.poll()
    assert vm.roiSource == old_roi and operation.camera.alive
    vm.setSpecimenId("A-003")
    assert not vm.roiSource and vm.status["verdictTone"] == "neutral"
    assert vm.status["defects"] == 1 and operation.controller.run_record.specimen_id == "A-002"


@pytest.mark.parametrize("score", [0.1, 0.9])
def test_evidence_failure_never_gets_normal_or_success_color(operation, score, monkeypatch):
    vm = operation.monitor
    bind_model(operation, [score])
    vm.startInspection()

    def fail(*args, **kwargs):
        raise OSError("Injected disk failure")

    monkeypatch.setattr(operation.controller.worker, "_evidence", fail)
    complete_cycle(operation)
    wait_result(operation)
    assert vm.status["verdictTone"] == "neutral"
    assert vm.status["evidenceTone"] == "review"
    assert vm.status["defects"] == (1 if score > 0.5 else 0)
    assert vm.status["coverageWarning"] and "Evidence incomplete" in vm.status["fault"]


@pytest.mark.parametrize("accepted", [False, True])
def test_native_setup_is_transient_and_applies_only_after_confirmation(operation, monkeypatch, accepted):
    vm = operation.monitor
    vm.loadRecipe(str(operation.selection))
    vm._dialogs = None
    engine = create_engine(vm)
    root = engine.rootObjects()[0]
    calls = []
    # Retain an explicit compatibility regression for the native fallback bridge.
    vm._dialogs = native_dialogs.MonitorDialogs(root)

    def confirm(dialog):
        calls.append(dialog.windowHandle().transientParent() is root)
        assert root.screen().availableGeometry().contains(dialog.frameGeometry())
        assert dialog.specimen_id == "A-002"
        if accepted:
            dialog._confirmed.setChecked(True)
            dialog._save()
        return dialog.result()

    monkeypatch.setattr(native_dialogs.ReplaySettings, "exec", confirm)
    try:
        operation.camera.send(255)
        vm.poll()
        vm.startInspection()
        assert calls == [True]
        assert (operation.controller.run_record is not None) is accepted
    finally:
        vm.stopMonitoring()
        vm.dispose()
        root.close()
        shiboken6.delete(engine)


@pytest.mark.parametrize("light", [False, True])
def test_native_actions_use_existing_palette_and_remain_reachable(operation, light, tmp_path):
    vm = operation.monitor
    vm._light = light  # Render without changing the workstation's saved preference.
    vm.loadRecipe(str(operation.selection))
    engine = create_engine(vm)
    root = engine.rootObjects()[0]
    try:
        for width, height in ((960, 640), (1440, 900)):
            operation.camera.send(255)
            vm.poll()
            root.resize(width, height)
            QTest.qWait(100)
            start = root.findChild(QQuickItem, "startInspectionButton")
            stop = root.findChild(QQuickItem, "stopMonitoringButton")
            assert start.isEnabled() and not stop.isEnabled()
            assert start.property("background").property("color").name().upper() == colors(light)["start"]
            assert stop.property("background").property("color").name().upper() == colors(light)["disabledSurface"]
            position = start.mapToItem(None, 0, 0)
            assert 0 <= position.y() and position.y() + start.height() <= root.height()
            start.forceActiveFocus(Qt.FocusReason.TabFocusReason)
            QTest.qWait(20)
            background = engine.newQObject(start).property("background")
            assert background.property("border").property("width").toInt() == 2
            assert root.grabWindow().save(str(tmp_path / f"operation-{light}-{width}.png"))
    finally:
        vm.dispose()
        root.close()
        shiboken6.delete(engine)
