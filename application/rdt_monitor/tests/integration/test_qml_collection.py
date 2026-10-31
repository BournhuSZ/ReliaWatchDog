# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""QML collection/review/training parity using generated candidates and contract exports."""

import json
import shutil
import sys
from dataclasses import replace
from datetime import UTC, datetime
from queue import Empty, Queue
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import shiboken6
from PySide6.QtCore import QObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import rdt_monitor.collection_presentation as collection_ui
import rdt_monitor.presentation as presentation
from rdt_monitor.camera import CameraFrame, CameraStatus, MarkerTrial
from rdt_monitor.collection import CandidateCollection, CollectionSettings, load_document
from rdt_monitor.domain import RunRecord, SyncConfig
from rdt_monitor.lifecycle import Readiness, RunMode
from rdt_monitor.presentation import MonitorViewModel
from rdt_monitor.qml_app import create_engine
from rdt_monitor.recipes import StoredRecipe, load_recipe, save_recipe
from rdt_monitor.replay import Region, ReplayRecipe


@pytest.fixture(scope="module")
def application():
    instance = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Fusion")
    return instance


class Camera:
    def __init__(self, clock):
        self.clock = clock
        self.alive = True
        self.trial = None
        self.frames = Queue(maxsize=1)
        self.number = 0
        self.status = CameraStatus("Streaming", "Generated source", 0, width=32, height=24, last_received_at=clock[0])

    def send(self, image):
        self.clock[0] += 0.05
        self.number += 1
        frame = CameraFrame(self.number, self.clock[0], image.copy())
        self.status = replace(self.status, last_received_at=self.clock[0])
        if self.trial:
            self.trial.observe(frame)
        try:
            self.frames.get_nowait()
        except Empty:
            pass
        self.frames.put_nowait(frame)
        return frame

    def stop_trial(self):
        trial, self.trial = self.trial, None
        if trial:
            trial.request_stop()
        return trial

    def stop(self):
        self.alive = False
        self.status = replace(self.status, state="Stopped")


@pytest.fixture
def session(application, tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(collection_ui, "monotonic", lambda: clock[0])
    monkeypatch.setattr(presentation, "monotonic", lambda: clock[0])
    vm = MonitorViewModel(start_timer=False, output_root=tmp_path / "inspections")
    camera = Camera(clock)
    vm.controller.camera, vm.controller.mode = camera, "camera"
    vm.setSpecimenId("GENERATED-001")
    camera.send(np.full((24, 32, 3), 100, np.uint8))
    vm.poll()
    model = vm.collection
    model.editField("folder", str(tmp_path))
    model.editField("mode", "manual")
    model.editField("target", 20)
    model.editField("python", sys.executable)
    model.useCameraFrame()
    model.setRegion(0.25, 0.25, 0.5, 0.5)
    yield SimpleNamespace(vm=vm, model=model, camera=camera, clock=clock, root=tmp_path)
    writer = model._collection
    model.stopCollection()
    if writer:
        assert writer.finished.wait(5)
        vm.poll()
    model.cancelTraining()
    if model.training:
        assert model.process.waitForFinished(5000)
    camera.stop()
    vm.dispose()


def until(session, condition, seconds=8):
    deadline = monotonic() + seconds
    while not condition() and monotonic() < deadline:
        session.vm.poll()
        QTest.qWait(1)
        sleep(0.002)  # Let Python writer/decoder threads run between native event-processing calls.
    assert condition(), session.model.status


def review_all(session):
    until(session, lambda: session.model.status["canReview"])
    for index in range(1, session.model.status["samples"] + 1):
        session.model.selectSample(index)
        until(session, lambda: session.model.status["canReview"])
    assert session.model.status["canConfirm"]


def finish_manual(session):
    model = session.model
    model.collect()
    writer = model._collection
    for cycle in range(1, 21):
        session.camera.send(np.full((24, 32, 3), cycle, np.uint8))
        session.vm.poll()
        assert model.status["canCapture"]
        model.captureCycle()
        assert not model.status["canCapture"]
        until(session, lambda cycle=cycle: len(writer.document.records) == cycle)
    assert writer.finished.wait(5)
    until(session, lambda: model.status["canReview"])
    return writer


@pytest.fixture(scope="module")
def candidate_template(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("qml-candidate-template")
    settings = CollectionSettings(
        "GENERATED-REVIEW", "generated-run", "manual-capture-v1", Region(0.25, 0.25, 0.5, 0.5), 32, 24, 20, "manual"
    )
    writer = CandidateCollection(tmp_path, settings, capacity=32)
    writer.start()
    for index in range(20):
        assert writer.capture_manual(np.full((24, 32, 3), index + 1, np.uint8), index + 1, float(index + 1))
    assert writer.finished.wait(5)
    return writer.folder / "collection.json"


@pytest.fixture
def candidates(tmp_path, candidate_template):
    folder = tmp_path / candidate_template.parent.name
    shutil.copytree(candidate_template.parent, folder)
    return folder / "collection.json"


def test_safe_startup_without_camera_and_no_automatic_jobs(application):
    vm = MonitorViewModel(start_timer=False)
    try:
        assert not vm.collection.status["canCollect"]
        assert not vm.collection.status["canTrain"]
        assert not vm.collection.training
        vm.collection.collect()
        vm.collection.train()
        assert not vm.collection.capture_active and not vm.collection.training
        assert vm.controller.camera is None
    finally:
        vm.dispose()


def test_manual_exact_frames_target_review_and_explicit_training(session):
    writer = finish_manual(session)
    model = session.model
    assert model._document.state == "Awaiting confirmation"
    assert len(model._document.records) == 20
    assert not model.status["confirmed"] and not model.status["canTrain"]
    assert not model.training and session.vm.controller.run_record is None
    for cycle, record in enumerate(model._document.records, 1):
        source = cv2.imread(str(writer.folder / record.source_file))
        crop = cv2.imread(str(writer.folder / record.crop_file))
        assert np.all(source == cycle) and crop.shape == (12, 16, 3)
        assert record.cycle == cycle and record.frame_id == cycle + 1
    review_all(session)
    model.setConfirmed(True)
    assert model.status["canTrain"] and not model.training
    assert session.vm.status["verdict"] == "Not inspected"
    assert not list(session.root.glob("**/training-*"))


def test_capture_reserves_source_and_requires_fresh_distinct_frame(session):
    model = session.model
    model.collect()
    writer = model._collection
    assert not session.vm.status["canDisconnect"] and not session.vm.status["canEditSpecimen"]
    session.vm.setSpecimenId("must not replace capture identity")
    session.vm.disconnectCamera()
    session.vm.startInspection()
    assert session.vm.specimenId == "GENERATED-001" and session.camera.alive
    model.captureCycle()
    until(session, lambda: len(writer.document.records) == 1)
    model.captureCycle()
    assert len(writer.document.records) == 1
    session.clock[0] += 2
    session.camera.send(np.ones((24, 32, 3), np.uint8))
    session.vm.poll()
    session.clock[0] += 2
    model.captureCycle()
    assert not model.status["canCapture"]
    session.camera.stop()
    session.vm.poll()
    assert writer.finished.wait(5)
    session.vm.poll()
    assert model._document.state == "Incomplete" and not model.status["canTrain"]


@pytest.mark.parametrize("target", [19, 31, 20.5, float("nan")])
def test_invalid_target_cannot_create_writer(session, target):
    session.model.editField("target", target)
    session.model.collect()
    assert session.model._collection is None and session.model.status["error"]


def test_auto_capture_uses_exact_cycle_callback_and_detaches_after_target(session):
    recipe = ReplayRecipe(SyncConfig(1, 0.2, 4), Region(0, 0, 0.15, 0.15), Region(0.85, 0.85, 0.15, 0.15))
    session.camera.trial = MarkerTrial(recipe)
    record = RunRecord("auto-generated", "GENERATED-001", "counting-v1", "Generated camera", datetime.now(UTC))
    session.vm.controller.start(record, mode=RunMode.DEVELOPMENT, readiness=Readiness())
    session.model.editField("mode", "auto")
    session.camera.send(np.full((24, 32, 3), 255, np.uint8))
    session.vm.poll()
    session.model.collect()
    writer = session.model._collection
    assert writer is not None
    for cycle in range(1, 21):
        session.camera.send(np.zeros((24, 32, 3), np.uint8))
        returned = np.full((24, 32, 3), 40 + cycle, np.uint8)
        returned[:3, :4] = 255
        returned[20:, 27:] = 255
        frame = session.camera.send(returned)
        session.vm.poll()
        until(session, lambda cycle=cycle: len(writer.document.records) == cycle)
        assert writer.document.records[-1].frame_id == frame.frame_id
    assert writer.finished.wait(5)
    until(session, lambda: session.model.status["canReview"])
    assert session.model._trial is None
    assert session.vm.status["observed"] == 20
    assert session.vm.status["verdict"] == "Not inspected"
    assert session.model._document.settings.cycle_mode == "auto"
    assert session.camera.trial._cycle_capture is None


def test_writer_start_failure_leaves_no_capture_reservation(session, monkeypatch):
    class FailingWriter(CandidateCollection):
        def start(self):
            raise RuntimeError("Injected writer startup failure")

    monkeypatch.setattr(collection_ui, "CandidateCollection", FailingWriter)
    session.model.collect()
    assert not session.model.capture_active
    assert "startup failure" in session.model.status["error"]
    assert session.vm.status["canDisconnect"]


def test_progress_poll_does_not_wait_for_metadata_persistence(session):
    session.model.collect()
    writer = session.model._collection
    held, release = Event(), Event()

    def hold_metadata_lock():
        with writer._lock:
            held.set()
            release.wait(2)

    thread = Thread(target=hold_metadata_lock)
    thread.start()
    try:
        assert held.wait(2)
        started = monotonic()
        assert writer.try_document() is None
        session.model.poll()
        assert monotonic() - started < 0.5
        assert session.model.status["saved"] == 0
    finally:
        release.set()
        thread.join(2)
    until(session, lambda: writer.try_document() is not None)


def test_close_during_capture_requests_incomplete_drain(session):
    session.model.collect()
    writer = session.model._collection
    assert not session.vm.requestClose()
    assert session.vm.controller.closing
    assert writer.finished.wait(5)
    session.vm.poll()
    assert writer.document.state == "Incomplete"
    assert not session.model.capture_active
    assert session.vm.requestClose()


def test_review_source_crop_switch_and_reopening_reset_confirmation(session, candidates):
    session.model.loadCollection(str(candidates))
    review_all(session)
    session.model.setConfirmed(True)
    session.model.setCrop(True)
    assert not session.model.status["confirmed"]
    until(session, lambda: session.model.status["canReview"])
    assert session.model.status["region"] == []
    session.model.setConfirmed(True)
    session.model.loadCollection(str(candidates))
    until(session, lambda: session.model.status["canReview"])
    assert session.model.status["reviewed"] == 1 and not session.model.status["confirmed"]


def test_failed_load_requires_a_new_visible_source_draft(session):
    assert session.model.status["canCollect"]
    session.model.loadCollection(str(session.root / "missing-collection.json"))
    assert not session.model.status["canCollect"]
    assert not session.model.status["imageSource"] and session.model._source is None
    session.model.useCameraFrame()
    assert not session.model.status["canCollect"]
    session.model.setRegion(0.25, 0.25, 0.5, 0.5)
    assert session.model.status["canCollect"]


def test_picker_cancellation_preserves_current_draft(session):
    calls = []
    session.model.dialogs = SimpleNamespace(
        choose_collection=lambda initial: calls.append(initial),
        choose_training_python=lambda initial: calls.append(initial),
        choose_collection_folder=lambda initial: calls.append(initial),
    )
    before = session.model.status
    session.model.chooseCollection()
    session.model.choosePython()
    session.model.chooseFolder()
    assert session.model.status == before
    assert calls == [before["values"]["folder"], before["values"]["python"], before["values"]["folder"]]


def test_pending_camera_setup_blocks_collection_commands(session):
    session.vm._setup_open = True
    session.vm._refresh()
    session.model.collect()
    assert not session.model.capture_active
    assert not session.model.status["canUseFrame"]
    session.vm._setup_open = False
    session.vm._refresh()
    session.model.refresh()
    assert session.model.status["canCollect"]


def test_review_hash_failure_and_reload_require_new_confirmation(session, candidates):
    session.model.loadCollection(str(candidates))
    review_all(session)
    session.model.setConfirmed(True)
    assert session.model.status["canTrain"]
    document = load_document(candidates)
    source = candidates.parent / document.records[0].source_file
    source.write_bytes(b"modified after load")
    session.model.selectSample(1)
    until(session, lambda: session.model._review_worker is None)
    assert not session.model.status["canConfirm"] and not session.model.status["canTrain"]
    assert "changed" in session.model.status["error"]
    assert not session.model.status["imageSource"]


def test_incomplete_collection_and_metadata_changes_cannot_train(session, candidates):
    session.model.loadCollection(str(candidates))
    review_all(session)
    prior = candidates.parent / "training-prior" / "model.xml"
    prior.parent.mkdir()
    prior.write_bytes(b"contract-only export")
    prior.with_suffix(".bin").write_bytes(b"contract-only weights")
    session.model.loadExport(str(prior))
    assert session.model.status["canExport"]
    session.model.setConfirmed(True)
    raw = json.loads(candidates.read_text())
    raw["reason"] = "Changed after review"
    candidates.write_text(json.dumps(raw))
    session.model.train()
    assert not session.model.training and "changed" in session.model.status["error"]
    assert not session.model.status["canExport"]
    raw["state"] = "Incomplete"
    candidates.write_text(json.dumps(raw))
    session.model.loadCollection(str(candidates))
    until(session, lambda: session.model._review_worker is None)
    assert not session.model.status["canConfirm"] and not session.model.status["canTrain"]


CONTRACT_TRAINING = """
import json, sys
from pathlib import Path
import numpy as np
import openvino as ov
from rdt_monitor.training import train_collection
args=sys.argv[1:]
manifest=Path(args[args.index('--collection')+1])
digest=args[args.index('--sha256')+1]
result=Path(args[args.index('--result')+1])
def backend(data, output):
    p=ov.opset13.parameter([1,3,-1,-1],np.float32,name='image')
    heat=ov.opset13.reduce_mean(p,ov.opset13.constant([1],np.int64),keep_dims=False)
    score=ov.opset13.reduce_mean(heat,ov.opset13.constant([1,2],np.int64),keep_dims=False)
    heat.output(0).get_tensor().set_names({'anomaly_map'})
    score.output(0).get_tensor().set_names({'pred_score'})
    path=output/'contract-only-not-a-defect-model.xml'
    ov.save_model(ov.Model([score,heat],[p],'contract-only'),path)
    print('Contract backend verified candidate dataset; no model accuracy claim',flush=True)
    return path
export=train_collection(manifest,digest,True,backend=backend)
result.write_text(json.dumps({'model':str(export.relative_to(manifest.parent)),'qualified':False}))
"""


def test_actual_child_process_review_to_contract_export_and_explicit_recipe_binding(session, candidates, monkeypatch):
    pytest.importorskip("openvino")
    model = session.model
    model.loadCollection(str(candidates))
    review_all(session)
    model.editField("familyName", "Generated contract family")
    model.setConfirmed(True)
    commands = []

    def launch(program, arguments):
        commands.append((program, arguments))
        model.process.start(sys.executable, ["-c", CONTRACT_TRAINING, *arguments[2:]])

    monkeypatch.setattr(model, "_launch_training", launch)
    model.train()
    assert commands[0][1][:2] == ["-m", "rdt_monitor.training"]
    assert "--confirm-undamaged" in commands[0][1]
    assert not session.vm.requestClose()  # Preserve the existing explicit training-cancel requirement.
    # Interpreter/OpenVINO import and compilation are not a GUI or inspection deadline.
    until(session, lambda: not model.training, seconds=30)
    assert model.status["canExport"], model.status["error"]
    assert "Contract backend" in model.status["log"]
    assert not model.status["confirmed"]
    assert load_document(candidates).state == "Confirmed normal"
    assert session.vm.controller.loaded_recipe is None and session.vm.controller.worker is None
    model.inspectExport()
    assert session.vm.offline.status["modelPath"] == model.status["export"]
    synchronization = session.root / "sync.json"
    recipe = ReplayRecipe(SyncConfig(1, 0.2, 4), Region(0, 0, 0.1, 0.1), Region(0.8, 0.8, 0.1, 0.1))
    save_recipe(synchronization, StoredRecipe("OTHER-SERIAL", recipe, 32, 24))
    target = session.root / "inspection.json"
    model.saveInspectionRecipe(str(synchronization), str(target))
    saved = load_recipe(target)
    assert saved.detector.region == model._document.settings.region
    assert saved.specimen_id == "GENERATED-REVIEW" and saved.profile.name == "Generated contract family"
    assert saved.detector.review_margin == 0.05
    assert session.vm.controller.loaded_recipe is None  # Saving does not activate a live model.


@pytest.mark.parametrize("scenario", ["start_failure", "cancel", "invalid_result"])
def test_training_failure_cancel_and_result_validation(session, candidates, monkeypatch, scenario):
    model = session.model
    model.loadCollection(str(candidates))
    review_all(session)
    model.setConfirmed(True)
    if scenario == "start_failure":
        model._values["python"] = str(session.root / "missing-python.exe")
    else:
        script = (
            "import time; time.sleep(30)"
            if scenario == "cancel"
            else "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('{\"qualified\": true}')"
        )
        monkeypatch.setattr(
            model,
            "_launch_training",
            lambda program, arguments: model.process.start(sys.executable, ["-c", script, str(model._result_path)]),
        )
    model.train()
    if scenario == "cancel":
        model.cancelTraining()
    until(session, lambda: not model.training)
    assert not model.status["canExport"] and not model.status["confirmed"]
    assert model.status["error"]


def test_export_confinement_and_invalid_band_cannot_save_recipe(session, candidates):
    model = session.model
    model.loadCollection(str(candidates))
    until(session, lambda: model.status["canReview"])
    outside = session.root / "outside.xml"
    outside.write_bytes(b"contract-only")
    outside.with_suffix(".bin").write_bytes(b"weights")
    model.loadExport(str(outside))
    assert not model.status["canExport"] and "inside" in model.status["error"]
    export = candidates.parent / "training-generated" / "model.xml"
    export.parent.mkdir()
    export.write_bytes(b"contract-only")
    export.with_suffix(".bin").write_bytes(b"weights")
    model.loadExport(str(export))
    assert model.status["canExport"]
    model.editField("threshold", 0.99)
    model.editField("margin", 0.1)
    model.editField("familyName", "Invalid band")
    source = session.root / "sync.json"
    recipe = ReplayRecipe(SyncConfig(1, 0.2, 4), Region(0, 0, 0.1, 0.1), Region(0.8, 0.8, 0.1, 0.1))
    save_recipe(source, StoredRecipe("ID", recipe, 32, 24))
    model.saveInspectionRecipe(str(source), str(session.root / "invalid.json"))
    assert not (session.root / "invalid.json").exists() and "not saved" in model.status["error"]


@pytest.mark.parametrize("light", [False, True])
def test_native_three_stage_controls_and_review_are_accessible(session, candidates, light, tmp_path):
    session.vm._light = light
    engine = create_engine(session.vm)
    root = engine.rootObjects()[0]
    popup = root.findChild(QObject, "collectionPopup")
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    root.requestActivate()
    assert QTest.qWaitForWindowExposed(root)
    try:
        popup.open()
        for width, height in ((960, 640), (1440, 900)):
            root.resize(width, height)
            for stage in range(3):
                popup.setProperty("stage", stage)
                QTest.qWait(50)
                canvas = root.findChild(QQuickItem, "trainingCanvas")
                assert canvas.width() >= root.width() - 380 and canvas.height() >= 180
                names = (
                    ("collectCandidates", "loadCandidateCollection")
                    if stage == 0
                    else ("trainPatchCore", "confirmUndamaged")
                )
                if stage == 2:
                    names += ("saveCandidateRecipe",)
                for name in names:
                    control = root.findChild(QQuickItem, name)
                    position = control.mapToItem(None, 0, 0)
                    assert 0 <= position.y() and position.y() + control.height() <= root.height()
                assert root.grabWindow().save(str(tmp_path / f"collection-{light}-{stage}-{width}.png"))
        session.model.loadCollection(str(candidates))
        until(session, lambda: session.model.status["canReview"])
        assert popup.property("stage") == 1
        next_button = root.findChild(QQuickItem, "nextCandidate")
        center = next_button.mapToItem(None, next_button.width() / 2, next_button.height() / 2)
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center.toPoint())
        until(session, lambda: session.model.status["canReview"])
        assert session.model.status["sample"] == 2
        assert not warnings
    finally:
        session.vm.dispose()
        root.close()
        shiboken6.delete(engine)
