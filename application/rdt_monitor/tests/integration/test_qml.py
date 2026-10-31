# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Native Qt Quick transport, model, rendering and lifecycle checks without hardware."""

from dataclasses import replace
from datetime import UTC, datetime
from queue import Queue
from threading import Event
from time import monotonic
from types import SimpleNamespace

import numpy as np
import pytest
import shiboken6
from PySide6.QtCore import QObject, QSize, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

import rdt_monitor.presentation as presentation
from rdt_monitor.camera import CameraFrame, CameraStatus
from rdt_monitor.controller import MonitorController
from rdt_monitor.detection import DetectionResult, DetectionSettings
from rdt_monitor.domain import RunRecord, SyncResult, SyncState
from rdt_monitor.lifecycle import InspectionOutcome, Readiness, RunMode
from rdt_monitor.live import CycleDetection
from rdt_monitor.presentation import EventLogModel, FrameProvider, MonitorViewModel
from rdt_monitor.qml_app import create_engine
from rdt_monitor.recipes import RecipeDetector, SpecimenDetails, StoredRecipe
from rdt_monitor.replay import Region, ReplayPipeline, fixture_recipe


@pytest.fixture(scope="module")
def application():
    instance = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Fusion")
    return instance


def request(provider, url, requested=None):
    size = QSize()
    result = provider.requestImage(url.removeprefix("image://frames/"), size, requested or QSize())
    return result, size


def test_image_provider_owns_pixels_and_never_aliases_stale_identity(application):
    provider = FrameProvider()
    pixels = np.full((9, 11, 3), [12, 34, 56], dtype=np.uint8)
    first = provider.publish("preview", pixels)
    roi = provider.publish("roi", pixels)
    pixels[:] = 0
    image, size = request(provider, first)
    assert size == QSize(11, 9)
    assert image.pixelColor(0, 0).getRgb()[:3] == (56, 34, 12)
    latest = provider.publish("preview", pixels)
    assert request(provider, first)[0].isNull()
    assert request(provider, latest)[0].pixelColor(0, 0).red() == 0
    assert request(provider, roi)[0].pixelColor(0, 0).red() == 56
    provider.clear()
    assert request(provider, latest)[0].isNull()
    assert request(provider, roi)[0].isNull()


def test_event_model_retains_bounded_chronological_records(application):
    model = EventLogModel()
    for number in range(510):
        model.append("Cycle", "Counting only", "run", number, number * 10)
    roles = {name.decode(): role for role, name in model.roleNames().items()}
    assert model.rowCount() == 500
    assert model.data(model.index(0, 0), roles["cycle"]) == 10
    assert model.data(model.index(499, 0), roles["frameId"]) == 5090


def test_status_notifications_are_coalesced_and_missing_label_is_explicit(application):
    monitor = MonitorViewModel(start_timer=False)
    changed = QSignalSpy(monitor.statusChanged)
    try:
        monitor.poll()
        monitor.poll()
        assert changed.count() == 0
        monitor.startFixture("")
        assert "Enter a specimen" in monitor.status["fault"]
        assert monitor.controller.pipeline is None
        assert monitor.status["verdict"] == "Not inspected"
    finally:
        monitor.dispose()


def fixture_monitor():
    controller = MonitorController()
    controller.source = "Synthetic fixture / not camera footage"
    controller.synthetic = True
    controller.start_replay(
        "QML-TEST", fixture_recipe(), 1, factory=lambda factory, recipe: ReplayPipeline(factory, recipe, realtime=False)
    )
    controller.pipeline.join(5)
    monitor = MonitorViewModel(controller, start_timer=False)
    monitor.poll()
    return monitor


@pytest.mark.parametrize("size", [(960, 640), (1440, 900)])
def test_qml_loads_packaged_components_and_displays_exact_fixture(application, tmp_path, size):
    monitor = fixture_monitor()
    engine = create_engine(monitor)
    root = engine.rootObjects()[0]
    try:
        root.resize(*size)
        QTest.qWait(150)
        image = root.findChild(QQuickItem, "cameraImage")
        assert image is not None
        assert image.width() >= 320 and image.height() >= 180
        preview_bottom = image.mapToItem(None, 0, image.height()).y()
        for name in ("startInspectionButton", "stopMonitoringButton"):
            control = root.findChild(QQuickItem, name)
            # Run controls stay on screen beside the preview, outside result scrolling.
            assert control.mapToItem(None, 0, control.height()).y() <= preview_bottom + 12
        for name in ("offlineButton", "resultsButton", "settingsButton"):
            assert root.findChild(QQuickItem, name) is None
        assert image.property("source").toString() == monitor.previewSource
        assert engine.newQObject(image).property("status").toInt() == 1  # QML Image.Ready
        assert monitor.status["observed"] > 0
        assert monitor.status["inspected"] == 0
        assert "Synthetic" in monitor.status["sourceLabel"]
        assert monitor.status["verdict"] == "Not inspected"
        capture = root.grabWindow()
        assert not capture.isNull()
        assert capture.save(str(tmp_path / f"qml-preview-{size[0]}.png"))
    finally:
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)
        application.processEvents()


def test_preview_freshness_does_not_imply_inspection_and_close_stays_nonblocking(application, monkeypatch):
    controller = MonitorController()
    now = [10.0]
    monkeypatch.setattr(presentation, "monotonic", lambda: now[0])
    controller.mode = "camera"
    controller.camera = SimpleNamespace(
        status=CameraStatus("Streaming", "Preview only", 0, last_received_at=10),
        trial=None,
        alive=True,
        frames=Queue(maxsize=1),
        stop=lambda: None,
    )
    controller.camera.frames.put_nowait(CameraFrame(7, 10, np.zeros((24, 32, 3), dtype=np.uint8)))
    monitor = MonitorViewModel(controller, start_timer=False)
    ready = QSignalSpy(monitor.closeReady)
    try:
        monitor.poll()
        assert monitor.status["live"]
        assert monitor.status["verdict"] == "Not inspected"
        assert monitor.status["inspected"] == 0
        assert monitor.previewSource
        controller.camera.status = replace(controller.camera.status, last_received_at=5)
        monitor.poll()
        assert not monitor.status["live"]
        assert not monitor.requestClose()
        now[0] = 13.1
        monitor.poll()
        monitor.poll()
        assert ready.count() == 1
        assert monitor.requestClose()
    finally:
        controller.camera.alive = False
        monitor.dispose()


@pytest.mark.parametrize("reason", ["", "Evidence incomplete: injected storage failure"])
def test_read_only_roi_uses_inspected_frame_and_preserves_evidence_validity(application, tmp_path, reason):
    model = tmp_path / "model.xml"
    model.write_bytes(b"contract model")
    model.with_suffix(".bin").write_bytes(b"contract weights")
    region = Region(0.25, 0.25, 0.5, 0.5)
    binding = RecipeDetector(f"detector-{'a' * 32}/model.xml", "b" * 64, "c" * 64, region, 0.5)
    worker = SimpleNamespace(
        binding=binding, results=Queue(maxsize=4), failure="", finished=Event(), stop=lambda **kw: None
    )
    worker.finished.set()
    controller = MonitorController()
    controller.start(
        RunRecord("run", "specimen", "recipe", "camera", datetime.now(UTC)),
        mode=RunMode.EXPERIMENTAL_INSPECTION,
        readiness=Readiness(),
        worker=worker,
    )
    observation = SyncResult(10, 1.0, SyncState.TRACKING, 1, True, True, "Cycle complete")
    trial = SimpleNamespace(events=Queue(maxsize=4), result=None, stopped=True, recipe=fixture_recipe())
    trial.events.put_nowait(observation)
    controller.mode = "camera"
    controller.camera = SimpleNamespace(
        status=CameraStatus("Streaming", "Preview", 0, last_received_at=monotonic()),
        trial=trial,
        alive=True,
        frames=Queue(maxsize=1),
        stop=lambda: None,
        stop_trial=lambda: trial,
    )
    pixels = np.full((24, 32, 3), [12, 34, 56], dtype=np.uint8)
    result = DetectionResult(DetectionSettings(model, region, 0.5), 0.9, np.zeros((12, 16), dtype=np.float32), pixels)
    worker.results.put_nowait(CycleDetection(observation, result, tmp_path / "result.json", reason))
    controller.camera.frames.put_nowait(CameraFrame(99, 9, np.zeros_like(pixels)))
    monitor = MonitorViewModel(controller, start_timer=False)
    try:
        monitor.poll()
        roi, size = request(monitor.provider, monitor.roiSource)
        preview, _ = request(monitor.provider, monitor.previewSource)
        assert size == QSize(16, 12)
        assert roi.pixelColor(0, 0).getRgb()[:3] == (56, 34, 12)
        assert preview.pixelColor(0, 0).red() == 0
        assert monitor.status["defects"] == 1
        expected = InspectionOutcome.UNINSPECTED if reason else InspectionOutcome.DEFECT
        assert monitor.status["verdict"] == expected.value
        assert monitor.status["inspected"] == (0 if reason else 1)
    finally:
        monitor.dispose()


def test_camera_button_switches_command_and_retains_state_while_temporarily_disabled(application, monkeypatch):
    monitor = MonitorViewModel(start_timer=False)
    calls = []
    camera = SimpleNamespace(
        alive=True,
        status=CameraStatus("Streaming", "Injected preview", 0, last_received_at=monotonic()),
        trial=None,
        frames=Queue(maxsize=1),
        stop=lambda: None,
    )

    def connect(index):
        calls.append(("connect", index))
        monitor.controller.camera = camera
        monitor.controller.mode = "camera"
        return True

    def disconnect(now):
        calls.append(("disconnect",))
        monitor.controller.camera = None

    monkeypatch.setattr(monitor.controller, "connect_camera", connect)
    monkeypatch.setattr(monitor.controller, "disconnect_camera", disconnect)
    engine = create_engine(monitor)
    root = engine.rootObjects()[0]
    button = root.findChild(QQuickItem, "cameraConnectionButton")
    try:
        root.requestActivate()
        assert QTest.qWaitForWindowExposed(root)
        assert button.property("text") == "Connect"

        def click():
            center = button.mapToItem(None, button.width() / 2, button.height() / 2).toPoint()
            QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, center)
            application.processEvents()

        click()
        assert calls == [("connect", 0)]
        assert button.property("text") == "Disconnect"
        monitor._setup_open = True
        monitor.poll()
        assert not button.isEnabled() and button.property("text") == "Disconnect"
        monitor._setup_open = False
        monitor.poll()
        click()
        assert calls == [("connect", 0), ("disconnect",)]
        assert button.property("text") == "Connect"
    finally:
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)


@pytest.mark.parametrize("light", [False, True])
def test_gui_lock_blocks_commands_shortcuts_and_close_but_keeps_preview_updates(application, light):
    monitor = MonitorViewModel(start_timer=False)
    monitor._light = light
    engine = create_engine(monitor)
    root = engine.rootObjects()[0]
    lock = root.findChild(QQuickItem, "guiLockSwitch")
    try:
        root.resize(960, 640)
        root.requestActivate()
        assert QTest.qWaitForWindowExposed(root)
        monitor._status.update(canStart=True, canStop=True)
        monitor.statusChanged.emit()
        center = lock.mapToItem(None, lock.width() / 2, lock.height() / 2).toPoint()
        QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, center)
        assert root.property("operationsLocked") and lock.isEnabled()
        assert lock.property("checked")
        for name in (
            "cameraConnectionButton",
            "cameraIndex",
            "specimenField",
            "loadRecipeButton",
            "startInspectionButton",
            "stopMonitoringButton",
            "applicationMenuBar",
        ):
            assert not root.findChild(QQuickItem, name).isEnabled(), name
        for name in (
            "loadRecipeAction",
            "cameraAction",
            "offlineAction",
            "resultsAction",
            "collectionAction",
            "diagnosticsAction",
            "detailsAction",
            "lightAction",
            "settingsAction",
            "fixedSizeAction",
        ):
            assert not root.findChild(QObject, name).property("enabled"), name
        QTest.keyClick(root, Qt.Key_O, Qt.ControlModifier)
        QTest.keyClick(root, Qt.Key_Q, Qt.ControlModifier)
        assert not monitor.controller.closing
        assert not root.findChild(QObject, "cameraSetupPopup").property("visible")
        assert not root.close() and root.isVisible()
        old_source = monitor.previewSource
        monitor._preview = monitor.provider.publish("preview", np.zeros((24, 32, 3), dtype=np.uint8))
        monitor.imageChanged.emit()
        application.processEvents()
        assert monitor.previewSource != old_source
        image = root.findChild(QQuickItem, "cameraImage")
        assert image.property("source").toString() == monitor.previewSource
        monitor.events.append("While locked", "Background logging continues")
        assert monitor.eventModel.rowCount() > 0
        QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, center)
        assert not root.property("operationsLocked")
        assert not lock.property("checked")
        assert root.findChild(QQuickItem, "specimenField").isEnabled()
        assert root.findChild(QQuickItem, "startInspectionButton").isEnabled()
        assert root.findChild(QQuickItem, "stopMonitoringButton").isEnabled()
    finally:
        root.setProperty("operationsLocked", False)
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)


def test_station_header_clock_user_settings_and_lock(application, monkeypatch):
    monkeypatch.setattr(presentation, "getuser", lambda: "LAB\\operator")
    monitor = MonitorViewModel(start_timer=False)
    original_light = monitor.lightMode
    original_overlay = monitor.overlayEnabled
    engine = create_engine(monitor)
    root = engine.rootObjects()[0]

    def click(name):
        item = root.findChild(QQuickItem, name)
        center = item.mapToItem(None, item.width() / 2, item.height() / 2).toPoint()
        QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, center)
        QTest.qWait(50)

    try:
        root.resize(960, 640)
        root.requestActivate()
        assert QTest.qWaitForWindowExposed(root)
        assert root.findChild(QQuickItem, "headerUser").property("text") == "Windows user · LAB\\operator"
        assert root.findChild(QQuickItem, "headerRunStatus").property("text") == "Monitoring · Stopped"
        root.findChild(QObject, "viewMenu").open()
        QTest.qWait(50)
        click("settingsMenuItem")
        popup = root.findChild(QObject, "settingsPopup")
        assert popup.property("visible")
        click("settingsLightSwitch")
        assert monitor.lightMode != original_light
        click("settingsMapSwitch")
        assert monitor.overlayEnabled != original_overlay
        click("settingsLockSwitch")
        QTest.qWait(200)
        assert root.property("operationsLocked") and not popup.property("visible")
        timestamp = root.findChild(QQuickItem, "headerTimestamp")
        before = timestamp.property("text")
        deadline = monotonic() + 3
        while timestamp.property("text") == before and monotonic() < deadline:
            QTest.qWait(50)
        assert timestamp.property("text") != before
        assert not root.findChild(QObject, "settingsAction").property("enabled")
        assert root.findChild(QQuickItem, "guiLockSwitch").property("checked")
        click("guiLockSwitch")
        assert not root.property("operationsLocked")
    finally:
        root.setProperty("operationsLocked", False)
        monitor.setLightMode(original_light)
        monitor.setOverlayEnabled(original_overlay)
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)


def test_os_user_lookup_failure_is_explicit(application, monkeypatch):
    def unavailable():
        raise OSError("No OS user available")

    monkeypatch.setattr(presentation, "getuser", unavailable)
    monitor = MonitorViewModel(start_timer=False)
    try:
        assert monitor.sessionUser == "Unavailable"
    finally:
        monitor.dispose()


def test_view_menu_locks_current_dimensions_and_restores_resizing(application):
    monitor = MonitorViewModel(start_timer=False)
    engine = create_engine(monitor)
    root = engine.rootObjects()[0]
    try:
        root.resize(1100, 760)
        root.requestActivate()
        assert QTest.qWaitForWindowExposed(root)
        QTest.qWait(100)
        previous = (root.minimumWidth(), root.minimumHeight(), root.maximumWidth(), root.maximumHeight())
        action = root.findChild(QObject, "fixedSizeAction")

        def toggle_from_menu():
            root.findChild(QObject, "viewMenu").open()
            QTest.qWait(50)
            item = root.findChild(QQuickItem, "fixedSizeMenuItem")
            center = item.mapToItem(None, item.width() / 2, item.height() / 2).toPoint()
            QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, center)
            QTest.qWait(100)

        toggle_from_menu()
        assert action.property("checked")
        assert root.minimumWidth() == root.maximumWidth() == 1100
        assert root.minimumHeight() == root.maximumHeight() == 760
        root.setProperty("operationsLocked", True)
        assert not action.property("enabled") and action.property("checked")
        root.setProperty("operationsLocked", False)
        toggle_from_menu()
        assert not action.property("checked")
        assert (root.minimumWidth(), root.minimumHeight(), root.maximumWidth(), root.maximumHeight()) == previous
        root.resize(1200, 800)
        QTest.qWait(100)
        assert root.width() == 1200 and root.height() == 800
    finally:
        root.setProperty("operationsLocked", False)
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)


@pytest.mark.parametrize("light", [False, True])
def test_review_commands_are_reachable_from_menus(application, monkeypatch, tmp_path, light):
    monitor = MonitorViewModel(start_timer=False, output_root=tmp_path)
    monitor._light = light
    opened = []
    monkeypatch.setattr(presentation.QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    engine = create_engine(monitor)
    root = engine.rootObjects()[0]

    def choose(menu, item):
        root.findChild(QObject, menu).open()
        QTest.qWait(60)
        control = root.findChild(QQuickItem, item)
        center = control.mapToItem(None, control.width() / 2, control.height() / 2).toPoint()
        QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, center)
        QTest.qWait(100)

    try:
        root.requestActivate()
        assert QTest.qWaitForWindowExposed(root)
        choose("toolsMenu", "offlineMenuItem")
        popup = root.findChild(QObject, "offlinePopup")
        assert popup.property("visible")
        popup.close()
        QTest.qWait(200)
        choose("fileMenu", "resultsMenuItem")
        assert len(opened) == 1 and opened[0].toLocalFile() == tmp_path.as_posix()
    finally:
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)


def test_summary_distinguishes_valid_failures_from_alarm_and_excludes_incomplete_cycles(application):
    controller = MonitorController()
    record = RunRecord("summary-run", "specimen", "recipe", "injected", datetime.now(UTC))
    controller.start(record, mode=RunMode.EXPERIMENTAL_INSPECTION, readiness=Readiness())
    tickets = [controller.lifecycle.observe_cycle(i, i, float(i)) for i in range(1, 6)]
    controller.lifecycle.settle(tickets[1], InspectionOutcome.NEEDS_REVIEW, evidence_saved=True)
    controller.lifecycle.settle(tickets[0], InspectionOutcome.NO_DEFECT, evidence_saved=True)
    controller.lifecycle.settle(tickets[2], InspectionOutcome.DEFECT, evidence_saved=True)
    controller.lifecycle.settle(tickets[3], InspectionOutcome.DEFECT, evidence_saved=False)
    monitor = MonitorViewModel(controller, start_timer=False)
    try:
        status = monitor.status
        assert (status["passed"], status["failed"], status["reviewed"], status["inspected"]) == (1, 1, 1, 3)
        assert status["yieldRate"] == pytest.approx(100 / 3)
        assert (status["observed"], status["pending"], status["uninspected"], status["defects"]) == (5, 1, 1, 2)
        controller.lifecycle.request_stop(10)
        assert controller.lifecycle.finish_stop(14, source_quiescent=True)
        monitor._refresh()
        assert monitor.status["yieldRate"] == pytest.approx(100 / 3)
        assert monitor.status["uninspected"] == 2
        controller.start(
            RunRecord("summary-next", "next", "recipe", "injected", datetime.now(UTC)),
            mode=RunMode.EXPERIMENTAL_INSPECTION,
            readiness=Readiness(),
        )
        monitor._refresh()
        assert monitor.status["yieldRate"] is None
        assert (monitor.status["passed"], monitor.status["failed"], monitor.status["reviewed"]) == (0, 0, 0)
    finally:
        monitor.dispose()


@pytest.mark.parametrize("light", [False, True])
@pytest.mark.parametrize("size", [(960, 640), (1280, 800), (1440, 900)])
def test_setup_card_alignment_and_summary_geometry(application, light, size):
    monitor = MonitorViewModel(start_timer=False)
    monitor._light = light
    engine = create_engine(monitor)
    root = engine.rootObjects()[0]
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    try:
        root.resize(*size)
        root.requestActivate()
        assert QTest.qWaitForWindowExposed(root)
        QTest.qWait(80)
        specimen = root.findChild(QQuickItem, "specimenToolbox")
        assert specimen.height() == 104
        camera = root.findChild(QQuickItem, "cameraPanel")
        header = root.findChild(QQuickItem, "cameraHeader")
        connect = root.findChild(QQuickItem, "cameraConnectionButton")
        index = root.findChild(QQuickItem, "cameraIndex")
        assert header.height() == connect.height() == index.height() == 28
        assert connect.mapToItem(header, 0, 0).x() > index.mapToItem(header, index.width(), 0).x()
        assert (
            header.mapToItem(camera, 0, header.height()).y()
            <= root.findChild(QQuickItem, "cameraImage").mapToItem(camera, 0, 0).y()
        )
        load = root.findChild(QQuickItem, "loadRecipeButton")
        assert load.mapToItem(specimen, 0, 0).y() == 8
        entry = root.findChild(QQuickItem, "specimenField")
        recipe = root.findChild(QQuickItem, "recipeNameLabel")
        assert entry.mapToItem(specimen, 0, 0).x() > recipe.mapToItem(specimen, 0, 0).x()
        assert entry.width() >= 140
        assert root.findChild(QQuickItem, "monitoringIndicator").width() == 18
        session = root.findChild(QQuickItem, "sessionPanel")
        branding = root.findChild(QQuickItem, "brandingArea")
        assert specimen.mapToItem(None, specimen.width(), 0).x() <= session.mapToItem(None, 0, 0).x()
        if size[0] >= 1280:
            assert branding.mapToItem(None, 0, 0).y() == specimen.mapToItem(None, 0, 0).y()
        else:
            assert branding.mapToItem(None, 0, branding.height()).y() < specimen.mapToItem(None, 0, 0).y()
        lock = root.findChild(QQuickItem, "guiLockSwitch")
        lock_position = lock.mapToItem(session, 0, 0)
        assert lock_position.x() >= 0 and lock_position.x() + lock.width() <= session.width()
        assert lock_position.y() >= 0 and lock_position.y() + lock.height() <= 40
        event = root.findChild(QQuickItem, "eventPanel")
        summary = root.findChild(QQuickItem, "runSummary")
        roi = root.findChild(QQuickItem, "roiPanel")
        assert event.mapToItem(None, event.width(), 0).x() <= summary.mapToItem(None, 0, 0).x()
        assert summary.mapToItem(None, summary.width(), 0).x() <= roi.mapToItem(None, 0, 0).x()
        for name in ("cameraConnectionButton", "loadRecipeButton", "runSummary", "yieldChart", "yieldRate"):
            item = root.findChild(QQuickItem, name)
            point = item.mapToItem(None, 0, 0)
            assert 0 <= point.x() and point.x() + item.width() <= root.width()
            assert 0 <= point.y() and point.y() + item.height() <= root.height()
            if name in ("yieldChart", "yieldRate"):
                summary = root.findChild(QQuickItem, "runSummary")
                relative = item.mapToItem(summary, 0, 0)
                assert relative.y() + item.height() <= summary.height() - 8
        assert root.findChild(QQuickItem, "yieldRate").property("text") == "—"
        monitor.setSpecimenId("LONG-SPECIMEN-" + "X" * 100)
        monitor._status.update(passed=7, failed=2, reviewed=1, inspected=10, yieldRate=70.0, defects=2)
        monitor.statusChanged.emit()
        QTest.qWait(50)
        assert root.findChild(QQuickItem, "yieldRate").property("text") == "70.0%"
        summary = root.findChild(QQuickItem, "runSummary")
        chart = root.findChild(QQuickItem, "yieldChart")
        assert chart.mapToItem(summary, 0, chart.height()).y() <= summary.height() - 8
        alert = root.findChild(QQuickItem, "inspectionAlert")
        assert alert.isVisible()
        assert (
            alert.mapToItem(None, 0, 0).x()
            >= root.findChild(QQuickItem, "cameraImage")
            .mapToItem(None, root.findChild(QQuickItem, "cameraImage").width(), 0)
            .x()
        )
        assert not warnings
    finally:
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)


@pytest.mark.parametrize("bound_model", [False, True])
def test_recipe_header_uses_saved_metadata_without_replacing_actual_serial(application, tmp_path, bound_model):
    detector = RecipeDetector(f"detector-{'a' * 32}/model.xml", "b" * 64, "c" * 64, Region(0.1, 0.1, 0.5, 0.5), 0.5)
    stored = StoredRecipe(
        "TRAINING-SERIAL",
        fixture_recipe(),
        960,
        540,
        detector=detector if bound_model else None,
        specimen=SpecimenDetails("Reliability part", "Lot 7") if bound_model else SpecimenDetails(),
    )
    monitor = MonitorViewModel(start_timer=False)
    try:
        monitor.setSpecimenId("ACTUAL-SERIAL")
        monitor.controller.loaded_recipe = (stored, tmp_path / "recipe.json")
        monitor._refresh()
        assert monitor.specimenId == "ACTUAL-SERIAL"
        if bound_model:
            assert monitor.status["recipePart"] == "Part: Reliability part · Batch: Lot 7"
            assert "OpenVINO IR" in monitor.status["recipeModel"] and "bbbbbbbbbbbb" in monitor.status["recipeModel"]
            assert "Threshold 0.500 ± 0.050" in monitor.status["recipeModel"]
            assert detector.model_sha256 in monitor.status["recipeMetadata"]
        else:
            assert monitor.status["recipePart"] == "Part: Not recorded · Batch: Not recorded"
            assert monitor.status["recipeModel"] == "Model: None · counting only"
        assert "TRAINING-SERIAL" not in monitor.status["recipePart"]
    finally:
        monitor.dispose()


def test_replay_header_does_not_present_retained_live_detector_as_active(application, tmp_path):
    monitor = fixture_monitor()
    detector = RecipeDetector(f"detector-{'a' * 32}/model.xml", "b" * 64, "c" * 64, Region(0.1, 0.1, 0.5, 0.5), 0.5)
    try:
        monitor.controller.loaded_recipe = (
            StoredRecipe("TRAINING", fixture_recipe(), 960, 540, detector),
            tmp_path / "old.json",
        )
        monitor._refresh()
        assert monitor.status["recipeModel"] == "Model: None · counting-only replay"
        assert "live recipe inactive" in monitor.status["recipePart"]
    finally:
        monitor.dispose()


def test_log_tabs_separate_event_and_inspection_results(application):
    monitor = MonitorViewModel(start_timer=False)
    engine = create_engine(monitor)
    root = engine.rootObjects()[0]
    try:
        monitor.events.append("Camera connect", "Preview only")
        monitor.results.append("Defect detected", "Score 0.800 · Evidence saved", "run", 3, 42)
        root.requestActivate()
        assert QTest.qWaitForWindowExposed(root)
        view = root.findChild(QQuickItem, "eventLog")
        tabs = root.findChild(QQuickItem, "logTabs")
        assert view.property("model") == monitor.eventModel
        button = root.findChild(QQuickItem, "resultLogTab")
        QTest.mouseClick(
            root,
            Qt.LeftButton,
            Qt.NoModifier,
            button.mapToItem(None, button.width() / 2, button.height() / 2).toPoint(),
        )
        QTest.qWait(50)
        assert tabs.property("currentIndex") == 1
        assert view.property("model") == monitor.resultModel
        assert monitor.resultModel.rowCount() == 1
        assert monitor.results._rows[0]["cycle"] == 3
        assert monitor.results._rows[0]["frameId"] == 42
        assert all(row["message"] != "Defect detected" for row in monitor.events._rows)
    finally:
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)


def test_recipe_manager_loads_selected_file_and_preserves_serial(application, tmp_path):
    from rdt_monitor.recipes import RecipeProfile, SpecimenDetails, save_recipe

    recipe = StoredRecipe(
        "TRAINING",
        fixture_recipe(),
        640,
        480,
        profile=RecipeProfile("a" * 32, "b" * 32, "Recipe A"),
        specimen=SpecimenDetails("Part A", "Lot 1", "Family A", "001234567890"),
    )
    path = tmp_path / "recipe.json"
    save_recipe(path, recipe)
    monitor = MonitorViewModel(start_timer=False)
    monitor.setSpecimenId("ACTUAL")
    engine = create_engine(monitor)
    root = engine.rootObjects()[0]
    try:
        root.requestActivate()
        assert QTest.qWaitForWindowExposed(root)
        root.findChild(QObject, "loadRecipeAction").trigger(None)
        QTest.qWait(50)
        popup = root.findChild(QObject, "recipeManager")
        assert popup.property("visible")
        library = monitor.recipeLibrary
        library.setDirectory(str(tmp_path))
        library.selectFamily("Family A")
        library.selectUut("Part A · 001234567890")
        library.selectRecipe(str(path.resolve()))
        QTest.qWait(50)
        button = root.findChild(QQuickItem, "loadSelectedRecipe")
        assert button.isEnabled()
        QTest.mouseClick(
            root,
            Qt.LeftButton,
            Qt.NoModifier,
            button.mapToItem(None, button.width() / 2, button.height() / 2).toPoint(),
        )
        QTest.qWait(50)
        assert monitor.controller.loaded_recipe[0] == recipe
        assert monitor.specimenId == "ACTUAL"
        assert not popup.property("visible")
    finally:
        monitor.dispose()
        root.close()
        shiboken6.delete(engine)
