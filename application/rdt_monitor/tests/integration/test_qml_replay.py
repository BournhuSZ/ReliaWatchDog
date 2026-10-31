# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Recording replay parity, configuration isolation and native QML diagnostics."""

from dataclasses import replace
from types import SimpleNamespace

import cv2
import pytest
import shiboken6
from PySide6.QtCore import QObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rdt_monitor.camera import CameraStatus
from rdt_monitor.lifecycle import RunState
from rdt_monitor.presentation import MonitorViewModel
from rdt_monitor.qml_app import create_engine
from rdt_monitor.replay import ReplayPipeline, SyntheticSource, fixture_recipe
from rdt_monitor.replay_settings import settings_values


@pytest.fixture(scope="module")
def application():
    instance = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Fusion")
    return instance


@pytest.fixture
def monitor(application):
    value = MonitorViewModel(start_timer=False)
    yield value
    value.dispose()
    if value.controller.pipeline is not None:
        value.controller.pipeline.join(3)
        value.poll()


@pytest.fixture
def recording(tmp_path):
    path = tmp_path / "synthetic-recording-not-camera.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 30, (960, 540))
    assert writer.isOpened(), "Native OpenCV MJPG encoder must be available for replay parity checks"
    source = SyntheticSource(frame_count=80)
    try:
        while (frame := source.read()) is not None:
            writer.write(frame.image)
    finally:
        writer.release()
    return path


def fast_replay(monitor, monkeypatch):
    start = monitor.controller.start_replay

    def launch(specimen, recipe, revision):
        return start(
            specimen, recipe, revision, factory=lambda factory, recipe: ReplayPipeline(factory, recipe, realtime=False)
        )

    monkeypatch.setattr(monitor.controller, "start_replay", launch)


def test_selection_and_cancellation_do_not_open_or_start(monitor, tmp_path):
    path = tmp_path / "chosen.avi"
    path.write_bytes(b"Selected only; decoder must not open this yet")
    source = monitor.controller.source
    monitor.selectRecording(str(path))
    assert monitor.status["recordingPath"] == str(path.resolve())
    assert monitor.controller.source == source
    assert monitor.controller.pipeline is None and monitor.controller.run_record is None

    def choose():
        assert not monitor.status["canReplay"]
        assert not monitor.requestClose()
        monitor.startRecording("must not start in picker")
        return None

    monitor._dialogs = SimpleNamespace(choose_recording=choose)
    monitor.chooseRecording()
    assert monitor.status["recordingPath"] == str(path.resolve())
    assert monitor.controller.pipeline is None
    monitor.selectRecording(str(tmp_path / "absent.avi"))
    assert monitor.status["recordingPath"] == str(path.resolve())
    assert "not selected" in monitor.status["replayError"]


@pytest.mark.parametrize("label", ["", "   "])
def test_start_requires_explicit_file_and_label(monitor, tmp_path, label):
    monitor.startRecording("label only")
    assert monitor.controller.pipeline is None
    path = tmp_path / "file.avi"
    path.write_bytes(b"not decoded")
    monitor.selectRecording(str(path))
    monitor.startRecording(label)
    assert monitor.controller.pipeline is None
    assert monitor.status["replayError"]


def test_actual_recording_decodes_counts_and_keeps_neutral_verdict(monitor, recording, monkeypatch):
    fast_replay(monitor, monkeypatch)
    monitor.setSpecimenId("LIVE-SERIAL")
    monitor.controller.camera_recipe = replace(fixture_recipe(), brightness=210)
    camera_recipe = monitor.controller.camera_recipe
    monitor.selectRecording(str(recording))
    monitor.startRecording("RECORDING-001")
    pipeline = monitor.controller.pipeline
    assert pipeline is not None
    pipeline.join(5)
    assert not pipeline.alive
    monitor.poll()
    assert monitor.status["observed"] == 1
    assert monitor.status["inspected"] == 0
    assert monitor.status["uninspected"] == 1
    assert monitor.status["verdict"] == "Not inspected"
    assert monitor.status["defects"] == 0 and not monitor.status["coverageWarning"]
    assert monitor.status["evidence"] == "Not recorded"
    assert monitor.status["isReplay"] and monitor.status["replayState"] == "Ended"
    assert "Recording replay" in monitor.status["sourceLabel"]
    assert "Synthetic fixture" not in monitor.status["sourceLabel"]
    assert not monitor.status["live"] and monitor.status["canReplay"]
    assert monitor.status["cameraLabel"] == "Replay · Ended"
    assert monitor.controller.run_record.specimen_id == "RECORDING-001"
    assert monitor.specimenId == "LIVE-SERIAL"
    assert monitor.controller.camera_recipe == camera_recipe
    assert {region["label"] for region in monitor.status["regions"]} == {"Start", "Position"}
    assert monitor.previewSource and not monitor.roiSource
    assert monitor.controller.lifecycle.snapshot.state == RunState.STOPPED


def test_replay_edits_are_atomic_and_do_not_change_live_recipe(monitor):
    original = fixture_recipe()
    monitor.controller.camera_recipe = original
    values = settings_values(original)
    values.update(stable=3, gap=0.3, timeout=5, brightness=200)
    assert monitor.configureReplay(values)
    assert monitor.status["replayRevision"] == 2
    assert monitor.controller.camera_recipe is original
    assert monitor.controller.pipeline is None
    assert monitor.configureReplay(values)  # An unchanged Apply does not create a new revision.
    assert monitor.status["replayRevision"] == 2
    values["start"][0] = 0.99
    assert not monitor.configureReplay(values)
    assert monitor.status["replaySettings"]["start"] == settings_values(original)["start"]
    assert monitor.status["replaySettings"]["stable"] == 3


@pytest.mark.parametrize(
    "key,value",
    [
        ("stable", 1.5),
        ("stable", True),
        ("stable", 0),
        ("brightness", 256),
        ("gap", float("nan")),
        ("timeout", -1),
        ("position", [0, 0, 0, 0]),
        ("start", [0, 0, 1]),
    ],
)
def test_invalid_replay_edits_cannot_replace_committed_settings(monitor, key, value):
    before = monitor.status["replaySettings"]
    values = dict(before)
    values[key] = value
    assert not monitor.configureReplay(values)
    assert monitor.status["replaySettings"] == before
    assert monitor.status["replayRevision"] == 1
    assert monitor.status["replayError"]


def test_source_ownership_blocks_replay_edits_and_start(monitor, tmp_path):
    monitor.controller.camera = SimpleNamespace(
        alive=True, trial=None, status=CameraStatus("Streaming", "preview only", 0)
    )
    path = tmp_path / "new.avi"
    path.write_bytes(b"not opened")
    before = monitor.status["replaySettings"]
    monitor.selectRecording(str(path))
    monitor.startRecording("REPLAY")
    assert not monitor.configureReplay({**before, "brightness": 100})
    assert monitor.status["replaySettings"] == before
    assert not monitor.status["recordingPath"]
    assert monitor.controller.pipeline is None
    monitor.controller.camera = None


def test_bad_decode_and_deleted_source_surface_faults_without_normal(monitor, tmp_path, monkeypatch):
    fast_replay(monitor, monkeypatch)
    path = tmp_path / "broken.avi"
    path.write_bytes(b"invalid recording bytes")
    monitor.selectRecording(str(path))
    for remove in (False, True):
        if remove:
            path.unlink()
        monitor.startRecording("FAULT-TEST")
        monitor.controller.pipeline.join(3)
        monitor.poll()
        assert monitor.status["replayState"] == "Fault"
        assert "Replay unavailable" in monitor.status["fault"]
        assert monitor.status["verdict"] == "Not inspected"
        assert monitor.status["observed"] == monitor.status["defects"] == 0
        assert monitor.status["canReplay"]


def test_stop_and_repeated_start_preserve_one_run(monitor, recording):
    monitor.selectRecording(str(recording))
    monitor.startRecording("STOP-TEST")
    record = monitor.controller.run_record
    pipeline = monitor.controller.pipeline
    monitor.startRecording("must not replace active run")
    monitor.startFixture("must not replace active run")
    assert monitor.controller.run_record is record and monitor.controller.pipeline is pipeline
    monitor.stopReplay()
    assert not monitor.status["canStopReplay"]
    pipeline.join(3)
    monitor.poll()
    assert monitor.status["replayState"] == "Stopped"
    assert monitor.status["canReplay"]


def test_synthetic_fixture_never_uses_recording_settings(monitor, monkeypatch):
    fast_replay(monitor, monkeypatch)
    monitor.configureReplay({**monitor.status["replaySettings"], "brightness": 240, "stable": 5})
    monitor.startFixture("SYNTHETIC")
    assert monitor.controller.pipeline.recipe == fixture_recipe()
    monitor.controller.pipeline.join(5)
    monitor.poll()
    assert "Synthetic" in monitor.status["sourceLabel"]


def test_thread_start_failure_does_not_leave_controller_busy(monitor, tmp_path, monkeypatch):
    path = tmp_path / "selected.avi"
    path.write_bytes(b"not decoded")
    monitor.selectRecording(str(path))

    class FailingPipeline(ReplayPipeline):
        def start(self):
            raise RuntimeError("Injected thread startup failure")

    start = monitor.controller.start_replay
    monkeypatch.setattr(
        monitor.controller,
        "start_replay",
        lambda specimen, recipe, revision: start(specimen, recipe, revision, factory=FailingPipeline),
    )
    monitor.startRecording("FAIL-START")
    assert monitor.status["canReplay"]
    assert "startup failure" in monitor.status["replayError"]
    assert not monitor.controller.busy
    monitor.controller.pipeline = None  # There is no started native thread to join in teardown.


@pytest.mark.parametrize("light", [False, True])
def test_native_settings_cancel_apply_and_actions_fit(monitor, recording, light, tmp_path):
    monitor._light = light
    monitor.selectRecording(str(recording))
    engine = create_engine(monitor)
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    root = engine.rootObjects()[0]
    popup = root.findChild(QObject, "replayPopup")

    def find_visual(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            result = find_visual(child, name)
            if result is not None:
                return result
        return None

    def click(name):
        item = root.findChild(QQuickItem, name)
        center = item.mapToItem(None, item.width() / 2, item.height() / 2)
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center.toPoint())
        QTest.qWait(20)

    def edit(name, keys):
        field = find_visual(popup.property("contentItem"), name)
        field.forceActiveFocus()
        QTest.keyClick(root, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        for key in keys:
            QTest.keyClick(root, key)
        QTest.keyClick(root, Qt.Key.Key_Tab)

    try:
        root.requestActivate()
        for width, height in ((960, 640), (1440, 900)):
            root.resize(width, height)
            popup.open()
            QTest.qWait(80)
            click("editReplaySettings")
            edit("stableReplaySetting", [Qt.Key.Key_3])
            click("cancelReplaySettings")
            assert monitor.status["replaySettings"]["stable"] == 2
            click("editReplaySettings")
            edit("stableReplaySetting", [Qt.Key.Key_3])
            edit("startReplayCoordinate0", [Qt.Key.Key_6])
            click("applyReplaySettings")
            assert monitor.status["replaySettings"]["stable"] == 3
            assert monitor.status["replaySettings"]["start"][0] == pytest.approx(0.06)
            assert not popup.property("editing")
            for name in ("startRecording", "startSynthetic", "stopReplay"):
                item = root.findChild(QQuickItem, name)
                position = item.mapToItem(None, 0, 0)
                assert 0 <= position.x() and position.x() + item.width() <= root.width()
                assert 0 <= position.y() and position.y() + item.height() <= root.height()
            assert root.grabWindow().save(str(tmp_path / f"replay-{light}-{width}.png"))
            popup.close()
            assert monitor.configureReplay(settings_values(fixture_recipe()))
        assert not warnings
    finally:
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)
