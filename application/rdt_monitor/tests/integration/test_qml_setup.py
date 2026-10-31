# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Frozen QML setup contracts and native interactions without physical hardware."""

import json
from dataclasses import replace
from queue import Queue
from time import monotonic
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import shiboken6
from PySide6.QtCore import QObject, QSize, Qt, QTimer
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import rdt_monitor.controller as coordination
import rdt_monitor.presentation as presentation
from rdt_monitor.camera import CameraFrame, CameraStatus, MarkerTrial
from rdt_monitor.dialogs import QmlMonitorDialogs
from rdt_monitor.domain import SyncConfig
from rdt_monitor.presentation import MonitorViewModel
from rdt_monitor.qml_app import create_engine
from rdt_monitor.recipes import StoredRecipe, load_recipe, save_inspection_recipe, save_recipe
from rdt_monitor.replay import MarkerAppearance, Region, ReplayRecipe


@pytest.fixture(scope="module")
def application():
    instance = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Fusion")
    return instance


@pytest.fixture
def session(application, tmp_path):
    pixels = np.full((240, 320, 3), 25, dtype=np.uint8)
    cv2.line(pixels, (35, 47), (92, 47), (240, 240, 240), 6)
    cv2.line(pixels, (63, 27), (63, 68), (240, 240, 240), 6)
    cv2.circle(pixels, (255, 47), 18, (240, 240, 240), 5)
    frame = CameraFrame(7, monotonic(), pixels)
    recipe = ReplayRecipe(SyncConfig(2, 0.2, 4), Region(0.1, 0.1, 0.2, 0.2), Region(0.7, 0.1, 0.2, 0.2))
    path = tmp_path / "original.json"
    save_recipe(path, StoredRecipe("TRAINING-001", recipe, 320, 240))
    monitor = MonitorViewModel(start_timer=False, output_root=tmp_path / "results")
    value = SimpleNamespace(
        monitor=monitor, model=monitor.setup, recipe=recipe, frame=frame, pixels=pixels, path=path, root=tmp_path
    )
    yield value
    monitor.dispose()


def begin(session, loaded=True):
    selection = (load_recipe(session.path), session.path) if loaded else None
    session.model.begin(session.recipe, selection, "ACTUAL-002", session.frame)
    return selection


def test_owned_frozen_snapshot_actual_serial_and_cancel(session):
    before = session.monitor.controller.snapshot()
    begin(session)
    assert session.model.status["values"]["specimenId"] == "ACTUAL-002"
    url = session.model.status["imageSource"]
    image = session.model.provider.requestImage(url.removeprefix("image://setup/"), QSize(), QSize())
    original = image.pixelColor(63, 47)
    session.pixels[:] = 0
    assert image.pixelColor(63, 47) == original
    assert session.model._source[47, 63, 0] == 240
    session.model.cancel()
    assert not session.model.status["open"] and session.model._choice is None
    assert session.monitor.controller.snapshot() == before


def test_new_setup_requires_both_distinct_source_pixel_regions_and_confirmation(session):
    begin(session, loaded=False)
    assert not session.model.status["valid"]
    session.model.setRegion(0.1, 0.1, 0.2, 0.2)
    session.model.selectRole(1)
    session.model.setRegion(0.1, 0.1, 0.2, 0.2)
    assert not session.model.status["valid"]
    session.model.setRegion(0.7, 0.1, 0.2, 0.2)
    assert session.model.status["canConfirm"]
    session.model.accept()
    assert session.model.status["open"] and not session.model.status["canUse"]
    session.model.setConfirmed(True)
    session.model.accept()
    assert not session.model.status["open"]
    choice = session.model._choice
    assert choice.specimen_id == "ACTUAL-002"
    assert choice.recipe.marker_appearance is not None and choice.recipe.position_appearance is not None


@pytest.mark.parametrize(
    "field,value",
    [
        ("specimenId", "OTHER"),
        ("name", "Display name"),
        ("batch", "B-2"),
        ("stable", 3),
        ("gap", 0.3),
        ("timeout", 5),
        ("brightness", 190),
        ("recognition", "appearance"),
        ("recipeName", "New name"),
        ("notes", "New purpose"),
    ],
)
def test_every_setup_edit_invalidates_confirmation(session, field, value):
    begin(session)
    session.model.setConfirmed(True)
    assert session.model.status["canUse"]
    session.model.editField(field, value)
    assert not session.model.status["confirmed"] and not session.model.status["canUse"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("stable", 0),
        ("stable", 121),
        ("gap", float("nan")),
        ("gap", 61),
        ("timeout", -1),
        ("brightness", 256),
        ("specimenId", ""),
        ("recognition", "unsupported"),
    ],
)
def test_invalid_setup_fields_block_confirmation(session, field, value):
    begin(session)
    session.model.editField(field, value)
    session.model.setConfirmed(True)
    assert not session.model.status["valid"]
    assert not session.model.status["canUse"]
    assert session.model.status["error"]


def test_unchanged_fields_role_selection_and_numeric_precision_keep_confirmation(session):
    precise = replace(session.recipe, marker=Region(0.100000123, 0.1, 0.2, 0.2))
    save_recipe(session.path, StoredRecipe("TRAINING", precise, 320, 240))
    begin(session)
    session.model.setConfirmed(True)
    session.model.editField("name", "")
    session.model.selectRole(1)
    session.model.selectRole(0)
    session.model.setRegion(*session.model.status["region"])
    assert session.model.status["confirmed"]
    assert session.model.status["region"][0] == 0.100000123


def test_numeric_invalid_region_retains_draft_and_blocks_use(session):
    begin(session)
    session.model.setRegion(0.2, 0.2, 0, 0)
    assert session.model.status["region"] == []
    assert session.model.status["coordinates"] == [0.2, 0.2, 0, 0]
    session.model.setRegion(0.2, 0.2, 0.2, 0.2)
    assert session.model.status["valid"]
    session.model.setRegion(0.1, 0.1, 0.0000001, 0.0000001)
    assert not session.model.status["valid"]
    session.model.clearRegion()
    assert session.model.status["regions"][1] == [0.7, 0.1, 0.2, 0.2]


def test_nested_setup_cannot_replace_an_open_draft(session):
    begin(session)
    before = session.model.status
    with pytest.raises(RuntimeError, match="already open"):
        session.model.confirm(session.recipe, None, "OTHER", session.frame)
    assert session.model.status == before


def test_loaded_references_reused_until_explicit_refresh(session):
    recipe = replace(
        session.recipe,
        marker_appearance=MarkerAppearance.capture(session.pixels, session.recipe.marker),
        position_appearance=MarkerAppearance.capture(session.pixels, session.recipe.position),
    )
    save_recipe(session.path, StoredRecipe("TRAINING", recipe, 320, 240))
    session.pixels[:] = 0
    begin(session)
    assert session.model.status["canConfirm"]
    session.model.setConfirmed(True)
    assert session.model._candidate().recipe.marker_appearance == recipe.marker_appearance
    session.model.refreshReferences()
    assert not session.model.status["valid"] and not session.model.status["confirmed"]
    assert "contrast" in session.model.status["error"]


def test_load_dimensions_failure_and_picker_cancellation_preserve_draft(session):
    begin(session)
    session.model.setConfirmed(True)
    values = session.model.status["values"]
    session.model.dialogs = SimpleNamespace(choose_recipe=lambda: None, choose_recipe_revision=lambda: None)
    session.model.chooseRecipe()
    assert session.model.status["confirmed"]
    bad = session.root / "other-size.json"
    save_recipe(bad, StoredRecipe("OTHER", session.recipe, 640, 480))
    session.model.loadRecipe(str(bad))
    assert "dimensions" in session.model.status["error"]
    assert session.model.status["values"] == values
    assert session.model._selection[1] == session.path


def bind_model(session):
    export = session.root / "model.xml"
    export.write_bytes(b"contract model only")
    export.with_suffix(".bin").write_bytes(b"contract weights only")
    path = session.root / "inspection.json"
    save_inspection_recipe(path, load_recipe(session.path), export, Region(0.2, 0.5, 0.6, 0.3), 0.5)
    session.path = path
    return load_recipe(path)


def test_model_binding_fixed_and_decisions_require_new_revision_without_overwrite(session):
    original = bind_model(session)
    original_bytes = session.path.read_bytes()
    begin(session)
    assert session.model.status["bound"]
    session.model.editField("inspectionRoi", [0, 0, 1, 1])
    session.model.editField("threshold", 0.6)
    assert session.model.status["valid"] and not session.model.status["canConfirm"]
    session.model.setConfirmed(True)
    session.model.accept()
    assert session.model.status["open"]
    revision = session.root / "revision.json"
    session.model.saveRevision(str(revision))
    saved = load_recipe(revision)
    assert saved.detector.region == original.detector.region
    assert saved.detector.model_sha256 == original.detector.model_sha256
    assert saved.detector.threshold == 0.6 and saved.detector.review_margin == original.detector.review_margin
    assert session.path.read_bytes() == original_bytes
    assert not session.model.status["confirmed"]
    session.model.setConfirmed(True)
    assert session.model.status["canUse"]
    session.model.saveRevision(str(revision))
    assert "preserve" in session.model.status["error"]
    assert load_recipe(revision) == saved


def test_timing_and_metadata_changes_require_saved_revision_but_marker_move_does_not(session):
    begin(session)
    session.model.setRegion(0.12, 0.1, 0.2, 0.2)
    assert session.model.status["canConfirm"]
    session.model.editField("stable", 3)
    assert not session.model.status["canConfirm"]
    session.model.editField("recipeName", "Named purpose")
    revision = session.root / "new.json"
    session.model.saveRevision(str(revision))
    family = load_recipe(revision).profile.family_id
    assert session.model.status["canConfirm"]
    session.model.editField("notes", "Another purpose")
    next_revision = session.root / "next.json"
    session.model.saveRevision(str(next_revision))
    assert load_recipe(next_revision).profile.family_id == family
    assert load_recipe(next_revision).profile.revision != load_recipe(revision).profile.revision


class Camera:
    def __init__(self, frame):
        self.alive = True
        self.trial = None
        self.frames = Queue(maxsize=1)
        self.frames.put_nowait(frame)
        self.status = CameraStatus(
            "Streaming", "Injected camera", 0, width=320, height=240, last_received_at=frame.received_at
        )

    def start_trial(self, recipe, capture=None):
        self.trial = MarkerTrial(recipe)
        self.trial.set_cycle_capture(capture)

    def stop_trial(self):
        trial, self.trial = self.trial, None
        if trial:
            trial.request_stop()
        return trial

    def stop(self):
        self.alive = False
        self.status = replace(self.status, state="Stopped")


@pytest.mark.parametrize("action", ["accept", "cancel", "changed_source", "storage_failure"])
def test_real_qml_modal_confirmation_commits_only_after_explicit_valid_choice(session, monkeypatch, action):
    vm = session.monitor
    # Static acquisition advances only when the injected camera provides another frame.
    monkeypatch.setattr(presentation, "monotonic", lambda: session.frame.received_at)
    vm.controller.mode = "camera"
    vm.controller.camera = camera = Camera(session.frame)
    vm.setSpecimenId("ACTUAL-002")
    vm.loadRecipe(str(session.path))
    vm.poll()
    engine = create_engine(vm)
    root = engine.rootObjects()[0]
    assert isinstance(vm._dialogs, QmlMonitorDialogs)
    if action == "storage_failure":
        monkeypatch.setattr(
            coordination, "save_run_setup", lambda *args: (_ for _ in ()).throw(OSError("Storage unavailable"))
        )
    failures = []
    responses = []

    def respond():
        responses.append(action)
        try:
            assert vm.setup.status["open"] and not vm.status["canReplay"]
            assert camera.trial is None and vm.controller.run_record is None
            assert not vm.requestClose()
            vm.startInspection()  # Repeated Start cannot open a second setup session.
            if action == "cancel":
                vm.setup.cancel()
            else:
                if action == "changed_source":
                    data = json.loads(session.path.read_text())
                    data["specimen_id"] = "Changed during setup"
                    session.path.write_text(json.dumps(data))
                vm.setup.editField("specimenId", "ACTUAL-003")
                vm.setup.setConfirmed(True)
                vm.setup.accept()
        except AssertionError as error:
            failures.append(str(error))
            vm.setup.cancel()

    QTimer.singleShot(30, respond)
    try:
        vm.startInspection()
        assert not failures
        assert responses == [action], vm.status["fault"]
        assert not vm.setup.status["open"] and not vm.setup.status["imageSource"]
        if action == "accept":
            assert vm.controller.run_record.specimen_id == "ACTUAL-003"
            assert vm.specimenId == "ACTUAL-003" and camera.trial is not None
            assert list((session.root / "results").glob("run-*-setup.json"))
        else:
            assert vm.controller.run_record is None and camera.trial is None
            if action != "cancel":
                assert vm.status["fault"]
    finally:
        vm.stopMonitoring()
        vm.poll()
        camera.stop()
        vm.dispose()
        root.close()
        shiboken6.delete(engine)


def visual(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        result = visual(child, name)
        if result is not None:
            return result
    return None


@pytest.mark.parametrize("light", [False, True])
def test_native_setup_geometry_controls_and_read_only_inspection_roi(session, light, tmp_path):
    bind_model(session)
    vm = session.monitor
    vm._light = light
    engine = create_engine(vm)
    root = engine.rootObjects()[0]
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    begin(session)
    popup = root.findChild(QObject, "cameraSetupPopup")
    try:
        for width, height in ((960, 640), (1440, 900)):
            root.resize(width, height)
            QTest.qWait(80)
            canvas = root.findChild(QQuickItem, "setupCanvas")
            area = visual(canvas, "offlineImageArea")
            assert area.width() / area.height() == pytest.approx(4 / 3, abs=0.01)
            assert canvas.width() >= root.width() - 380

            def point(x, y, area=area):
                return area.mapToItem(None, x * area.width(), y * area.height()).toPoint()

            def drag(start, end, point=point):
                QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(*start))
                QTest.mouseMove(root, point(*end), 20)
                QTest.mouseRelease(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(*end))
                QTest.qWait(10)

            vm.setup.selectRole(1)
            vm.setup.setRegion(0.7, 0.1, 0.2, 0.2)
            vm.setup.selectRole(0)
            drag((0.8, 0.2), (0.82, 0.22))  # Hit the inactive Position mark to select and move it.
            assert vm.setup.status["role"] == 1
            assert vm.setup.status["regions"][1][0] == pytest.approx(0.72, abs=0.02)
            trained = [region for region in vm.setup.status["backgroundRegions"] if region["role"] == -1][0]
            assert not trained["selectable"] and trained["left"] == 0.2
            vm.setup.clearRegion()
            drag((0.68, 0.12), (0.88, 0.32))
            assert len(vm.setup.status["region"]) == 4
            assert root.grabWindow().save(str(tmp_path / f"setup-{light}-{width}.png"))
            popup.setProperty("advanced", True)
            QTest.qWait(30)
            assert canvas.height() >= 180 and area.height() >= 100
            coordinate = visual(popup.property("contentItem"), "setupCoordinate0")
            before = vm.setup.status["region"]
            coordinate.forceActiveFocus()
            QTest.keyClick(root, Qt.Key.Key_Tab)
            assert vm.setup.status["region"] == before  # Merely focusing a rounded display cannot move a mark.
            sample = area.mapToItem(None, area.width() * 0.5, area.height() * 0.45)
            assert root.grabWindow().pixelColor(round(sample.x()), round(sample.y())).red() == 25
            for name in ("useCameraSetup", "confirmCameraSetup", "setupSaveRevision"):
                control = root.findChild(QQuickItem, name)
                position = control.mapToItem(None, 0, 0)
                assert 0 <= position.y() and position.y() + control.height() <= root.height()
            assert root.grabWindow().save(str(tmp_path / f"setup-advanced-{light}-{width}.png"))
            popup.setProperty("advanced", False)
        assert not warnings
    finally:
        vm.dispose()
        root.close()
        shiboken6.delete(engine)
