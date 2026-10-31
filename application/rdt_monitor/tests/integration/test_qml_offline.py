# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Native offline QML regression checks using contract outputs, never model accuracy."""

from functools import partial
from threading import Event
from time import monotonic
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
import shiboken6
from PySide6.QtCore import QObject, QPoint, QSize, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import rdt_monitor.offline_presentation as offline_module
from rdt_monitor.detection import InspectionWorker
from rdt_monitor.presentation import MonitorViewModel
from rdt_monitor.qml_app import create_engine


@pytest.fixture(scope="module")
def application():
    instance = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Fusion")
    return instance


@pytest.fixture
def offline(application, tmp_path, monkeypatch):
    monitor = MonitorViewModel(start_timer=False)
    model = monitor.offline
    image_path = tmp_path / "specimen.png"
    source = np.full((100, 200, 3), (20, 50, 90), dtype=np.uint8)
    assert cv2.imwrite(str(image_path), source)
    export = tmp_path / "contract.onnx"
    export.write_bytes(b"injected backend contract only, not a trained model")
    captured = []

    class Backend:
        def predict(self, crop):
            captured.append(crop.copy())
            return SimpleNamespace(pred_score=np.array([0.8]), anomaly_map=np.full((2, 3), 0.7))

    monkeypatch.setattr(offline_module, "InspectionWorker", partial(InspectionWorker, loader=lambda path: Backend()))
    yield SimpleNamespace(monitor=monitor, model=model, image=image_path, export=export, source=source, crops=captured)
    monitor.dispose()


def settle(model):
    deadline = monotonic() + 5
    while model.status["busy"] and monotonic() < deadline:
        model.poll()
        QTest.qWait(5)
    assert not model.status["busy"]


def prepare(offline):
    offline.model.loadImage(str(offline.image))
    settle(offline.model)
    offline.model.loadModel(str(offline.export))
    offline.model.setRegion(0.2, 0.2, 0.5, 0.6)
    assert offline.model.status["canInspect"], offline.model.status


def displayed(model):
    identity = model.status["imageSource"].removeprefix("image://offline/")
    return model.provider.requestImage(identity, QSize(), QSize())


def test_exact_crop_heatmap_snapshot_and_live_isolation(offline, tmp_path):
    before = offline.monitor.controller.snapshot()
    prepare(offline)
    offline.model.inspect()
    assert offline.model.status["tone"] == "neutral"
    assert not offline.model.status["canSave"]
    settle(offline.model)
    assert offline.model.status["assessment"] == "Unqualified / anomaly"
    assert offline.model.status["score"] == "0.8000"
    assert offline.crops[0].shape == (60, 100, 3)
    assert np.array_equal(offline.crops[0][0, 0], [90, 50, 20])
    overlay = displayed(offline.model)
    assert overlay.pixelColor(50, 30).red() != 90
    assert overlay.pixelColor(0, 0).getRgb()[:3] == (90, 50, 20)
    offline.model.setHeatmap(False)
    assert displayed(offline.model).pixelColor(50, 30).getRgb()[:3] == (90, 50, 20)
    assert len(offline.crops) == 1
    target = tmp_path / "manual.png"
    offline.model.saveSnapshot(str(target))
    assert np.array_equal(cv2.imread(str(target)), offline.source)
    assert "not qualified" in offline.model.status["error"]
    assert offline.monitor.controller.snapshot() == before
    assert offline.monitor.status["verdict"] == "Not inspected"
    assert offline.monitor.status["defects"] == 0
    assert offline.monitor.events.rowCount() == 0


@pytest.mark.parametrize("mutation", ["roi", "threshold", "model", "image", "clear"])
def test_inputs_invalidate_assessment_and_snapshot(offline, mutation):
    prepare(offline)
    offline.model.inspect()
    settle(offline.model)
    assert offline.model.status["canSave"]
    if mutation == "roi":
        offline.model.setRegion(0.1, 0.1, 0.3, 0.3)
    elif mutation == "threshold":
        offline.model.setThreshold(0.9)
    elif mutation == "model":
        offline.model.loadModel(str(offline.export))
    elif mutation == "image":
        offline.model.loadImage(str(offline.image))
        settle(offline.model)
    else:
        offline.model.clearRegion()
    assert offline.model.status["tone"] == "neutral"
    assert offline.model.status["score"] == "-"
    assert not offline.model.status["canSave"]


@pytest.mark.parametrize("threshold", [0, 1, float("nan"), float("inf")])
def test_invalid_threshold_blocks_inference(offline, threshold):
    prepare(offline)
    offline.model.setThreshold(threshold)
    offline.model.inspect()
    assert not offline.model.status["canInspect"]
    assert not offline.model.status["busy"]
    assert offline.model.status["error"]
    offline.model.setThreshold(0.5)
    assert offline.model.status["canInspect"]


def test_failed_import_model_roi_and_save_are_explicit(offline, tmp_path):
    offline.model.loadImage(str(tmp_path / "absent.png"))
    settle(offline.model)
    assert offline.model.status["error"] and not offline.model.status["hasImage"]
    prepare(offline)
    offline.model.setRegion(0.9, 0.2, 0.5, 0.3)
    assert not offline.model.status["canInspect"]
    assert offline.model.status["error"]
    offline.model.setRegion(0, 0, 1, 1)
    offline.model.loadModel(str(tmp_path / "unsafe.ckpt"))
    assert not offline.model.status["canInspect"]
    assert "checkpoint" in offline.model.status["error"]
    offline.model.loadModel(str(offline.export))
    offline.model.inspect()
    settle(offline.model)
    offline.model.saveSnapshot(str(tmp_path / "snapshot.jpg"))
    assert "failed" in offline.model.status["error"]
    assert not (tmp_path / "snapshot.jpg").exists()


@pytest.mark.parametrize("failed", [False, True])
def test_close_discards_late_jobs_and_prevents_overlapping_restart(offline, monkeypatch, failed):
    prepare(offline)
    entered, release = Event(), Event()

    class Backend:
        def predict(self, crop):
            entered.set()
            assert release.wait(5)
            if failed:
                raise ValueError("Contract detector failure")
            return SimpleNamespace(pred_score=np.array([0.1]), anomaly_map=np.zeros((2, 3)))

    monkeypatch.setattr(offline_module, "InspectionWorker", partial(InspectionWorker, loader=lambda path: Backend()))
    offline.model.inspect()
    try:
        assert entered.wait(2)
        generation = offline.model._generation
        offline.model.inspect()
        offline.model.loadImage("must not replace active input")
        offline.model.setRegion(0, 0, 1, 1)
        assert offline.model._generation == generation
        offline.model.reset()
        assert offline.model.status["busy"]
        assert not offline.model.status["hasImage"]
        assert not offline.model.status["imageSource"]
    finally:
        release.set()
        settle(offline.model)
    assert offline.model.status["score"] == "-"
    assert offline.model.status["tone"] == "neutral"
    assert not offline.model.status["error"]
    assert not offline.model.status["canSave"]


def test_background_failure_and_picker_cancellation(offline, monkeypatch):
    prepare(offline)

    class Backend:
        def predict(self, crop):
            raise RuntimeError("Explicit unavailable detector")

    monkeypatch.setattr(offline_module, "InspectionWorker", partial(InspectionWorker, loader=lambda path: Backend()))
    offline.model.inspect()
    settle(offline.model)
    assert offline.model.status["tone"] == "neutral"
    assert "unavailable" in offline.model.status["error"]
    offline.model.dialogs = SimpleNamespace(choose_image=lambda: None, choose_model=lambda: None)
    before = offline.model.status
    offline.model.chooseImage()
    offline.model.chooseModel()
    assert offline.model.status == before


def test_actual_openvino_export_uses_existing_runtime(offline, monkeypatch, tmp_path):
    runtime = pytest.importorskip("openvino")
    operations = runtime.opset13
    image = operations.parameter([1, 3, -1, -1], np.float32, name="image")
    heatmap = operations.reduce_mean(image, operations.constant([1], np.int64), keep_dims=False)
    score = operations.reduce_mean(heatmap, operations.constant([1, 2], np.int64), keep_dims=False)
    heatmap.output(0).get_tensor().set_names({"anomaly_map"})
    score.output(0).get_tensor().set_names({"pred_score"})
    export = tmp_path / "runtime-contract.xml"
    runtime.save_model(runtime.Model([score, heatmap], [image], "contract-only-not-a-defect-model"), export)
    monkeypatch.setattr(offline_module, "InspectionWorker", InspectionWorker)
    prepare(offline)
    offline.model.loadModel(str(export))
    offline.model.inspect()
    settle(offline.model)
    assert offline.model.status["assessment"] == "Unqualified / normal"
    assert float(offline.model.status["score"]) == pytest.approx((20 + 50 + 90) / (3 * 255), abs=0.001)
    assert offline.model._result.anomaly_map.shape == (60, 100)
    assert offline.model._result.settings.model_path == export
    assert offline.monitor.status["verdict"] == "Not inspected"


def test_worker_publication_during_liveness_check_is_not_lost(offline):
    from queue import Queue

    class CompletingImport:
        results = Queue(maxsize=1)

        @property
        def alive(self):
            self.results.put_nowait(offline.source.copy())
            return False

    offline.model._worker = CompletingImport()
    offline.model._importing = True
    offline.model.poll()
    assert offline.model.status["hasImage"]
    assert not offline.model.status["error"]


def test_native_threshold_keyboard_edit_and_inspect_action(offline):
    prepare(offline)
    engine = create_engine(offline.monitor)
    root = engine.rootObjects()[0]
    popup = root.findChild(QObject, "offlinePopup")
    try:
        popup.open()
        root.requestActivate()
        QTest.qWait(80)
        field = root.findChild(QQuickItem, "offlineThreshold")
        field.forceActiveFocus()
        QTest.keyClick(root, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        for key in (Qt.Key.Key_0, Qt.Key.Key_Period, Qt.Key.Key_9):
            QTest.keyClick(root, key)
        QTest.keyClick(root, Qt.Key.Key_Tab)
        assert offline.model.status["threshold"] == pytest.approx(0.9)
        button = root.findChild(QQuickItem, "offlineInspect")
        center = button.mapToItem(None, button.width() / 2, button.height() / 2)
        QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center.toPoint())
        settle(offline.model)
        assert offline.model.status["assessment"] == "Unqualified / normal"
        assert offline.model._result.settings.threshold == pytest.approx(0.9)
    finally:
        popup.close()
        offline.monitor.dispose()
        root.close()
        shiboken6.delete(engine)


@pytest.mark.parametrize("light", [False, True])
def test_native_editor_draw_move_resize_letterboxing_and_both_sizes(offline, light, tmp_path):
    prepare(offline)
    offline.monitor._light = light
    engine = create_engine(offline.monitor)
    warnings = []
    engine.warnings.connect(lambda rows: warnings.extend(row.toString() for row in rows))
    root = engine.rootObjects()[0]
    popup = root.findChild(QObject, "offlinePopup")
    try:
        popup.open()
        for width, height in ((960, 640), (1440, 900)):
            root.resize(width, height)
            QTest.qWait(100)
            area = root.findChild(QQuickItem, "offlineImageArea")
            canvas = root.findChild(QQuickItem, "offlineCanvas")
            assert area.width() > 280 and area.height() > 100
            assert area.width() / area.height() == pytest.approx(2, abs=0.02)
            offline.model.clearRegion()

            def point(x, y, area=area):
                position = area.mapToItem(None, x * area.width(), y * area.height())
                return QPoint(round(position.x()), round(position.y()))

            def drag(start, end):
                QTest.mousePress(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(*start))
                QTest.mouseMove(root, point(*end), 20)
                QTest.mouseRelease(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(*end))
                QTest.qWait(10)

            drag((0.2, 0.2), (0.7, 0.8))
            assert offline.model.status["region"] == pytest.approx([0.2, 0.2, 0.5, 0.6], abs=0.015)
            drag((0.4, 0.5), (0.5, 0.6))
            assert offline.model.status["region"] == pytest.approx([0.3, 0.3, 0.5, 0.6], abs=0.02)
            for x, y in ((0, 0), (0.5, 0), (1, 0), (0, 0.5), (1, 0.5), (0, 1), (0.5, 1), (1, 1)):
                offline.model.setRegion(0.2, 0.2, 0.5, 0.6)
                drag((0.2 + x * 0.5, 0.2 + y * 0.6), (0.2 + x * 0.5 + 0.03, 0.2 + y * 0.6 + 0.03))
                region = offline.model.status["region"]
                assert region != pytest.approx([0.2, 0.2, 0.5, 0.6], abs=0.01)
                assert all(0 <= value <= 1 for value in region)
                assert region[0] + region[2] <= 1 and region[1] + region[3] <= 1
            for name in ("offlineInspect", "offlineSave"):
                button = root.findChild(QQuickItem, name)
                position = button.mapToItem(None, 0, 0)
                assert 0 <= position.y() < root.height() - button.height()
            assert canvas.height() > 180
            assert canvas.width() >= root.width() - 350
            offline.model.setRegion(0.2, 0.2, 0.5, 0.6)
            before = offline.model.status["region"]
            if area.height() + 10 < canvas.height():
                margin = canvas.mapToItem(None, canvas.width() / 2, 2)
                QTest.mouseClick(root, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, margin.toPoint())
                assert offline.model.status["region"] == before
            drag((0.4, 0.5), (1.2, 1.2))
            region = offline.model.status["region"]
            assert region[0] + region[2] <= 1 and region[1] + region[3] <= 1
            assert root.grabWindow().save(str(tmp_path / f"offline-{light}-{width}.png"))
        assert not warnings
        popup.close()
        QTest.qWait(30)
        assert not offline.model.status["hasImage"]
    finally:
        offline.monitor.dispose()
        root.close()
        shiboken6.delete(engine)
