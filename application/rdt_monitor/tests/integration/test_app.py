# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from importlib.resources import files
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from time import monotonic

import cv2
import numpy as np
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QImage, QRawFont
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QStyle, QStyleOptionButton

import rdt_monitor.app as desktop
from rdt_monitor.camera import CameraFrame, CameraStatus, MarkerTrial
from rdt_monitor.lifecycle import RunState
from rdt_monitor.recipes import StoredRecipe, load_recipe, save_recipe
from rdt_monitor.replay import ReplayPipeline


@pytest.fixture
def candidate_dialog(window, tmp_path):
    from rdt_monitor.collection import CandidateCollection, CollectionSettings
    from rdt_monitor.domain import SyncResult, SyncState

    settings = CollectionSettings(
        "new-specimen", "run-candidates", "sync-v1", desktop.Region(0.1, 0.1, 0.8, 0.8), 80, 60, 20
    )
    collection = CandidateCollection(tmp_path, settings, capacity=32)
    collection.start()
    for cycle in range(1, 21):
        result = SyncResult(cycle * 10, float(cycle), SyncState.TRACKING, cycle, True, True, "Cycle complete")
        collection.capture(np.full((60, 80, 3), cycle * 5, dtype=np.uint8), result)
    assert collection.finished.wait(5)
    window.open_training_collection()
    dialog = window._training_dialog
    dialog._load_manifest(collection.folder / "collection.json")
    yield dialog
    if dialog._review_worker is not None:
        dialog._review_worker.join(5)
    dialog.close()


def finish_candidate_review(dialog):
    if dialog._review_worker is not None:
        dialog._review_worker.join(5)
        dialog.poll()


def review_all_candidates(dialog):
    finish_candidate_review(dialog)
    for index in range(2, len(dialog._document.records) + 1):
        dialog.sample.setValue(index)
        finish_candidate_review(dialog)


def test_collection_ui_requires_all_images_reviewed_and_explicit_confirmation(candidate_dialog, window, monkeypatch):
    dialog = candidate_dialog
    assert not dialog.train_button.isEnabled()
    assert not dialog.confirm.isEnabled()
    finish_candidate_review(dialog)
    assert dialog._reviewed == {0}
    review_all_candidates(dialog)
    assert len(dialog._reviewed) == 20
    assert dialog.confirm.isEnabled()
    assert not dialog.train_button.isEnabled()
    calls = []
    monkeypatch.setattr(dialog._process, "start", lambda *args: calls.append(args))
    assert calls == []
    dialog.confirm.setChecked(True)
    assert dialog.train_button.isEnabled()
    assert calls == []
    dialog.train_button.click()
    assert len(calls) == 1
    interpreter, arguments = calls[0]
    assert interpreter == dialog.python.text()
    assert "--confirm-undamaged" in arguments
    assert arguments[arguments.index("--collection") + 1] == str(dialog._manifest)
    assert "Defect inspection unavailable" in window.assessment_note.text()
    assert "Unknown" in window.condition.text()


def test_collection_reload_clears_previous_confirmation(candidate_dialog):
    dialog = candidate_dialog
    review_all_candidates(dialog)
    dialog.confirm.setChecked(True)
    path = dialog._manifest
    dialog._load_manifest(path)
    assert not dialog.confirm.isChecked()
    assert not dialog.train_button.isEnabled()
    finish_candidate_review(dialog)
    assert dialog._reviewed == {0}


def test_bad_candidate_prevents_review_confirmation(candidate_dialog):
    dialog = candidate_dialog
    finish_candidate_review(dialog)
    record = dialog._document.records[1]
    (dialog._manifest.parent / record.source_file).write_bytes(b"unreadable")
    dialog.sample.setValue(2)
    finish_candidate_review(dialog)
    assert 1 not in dialog._reviewed
    assert not dialog.confirm.isEnabled()
    assert not dialog.train_button.isEnabled()
    assert dialog.fault.text()


def test_training_dialog_has_safe_no_camera_startup(window):
    window.training_action.trigger()
    dialog = window._training_dialog
    assert not dialog.collect_button.isEnabled()
    assert "Camera unavailable" in dialog.synchronization_status.text()
    assert dialog.saved_count.text() == "Saved candidates: 0 / 30"
    assert not dialog.train_button.isEnabled()
    assert not dialog.confirm.isEnabled()
    assert dialog._process.state() == desktop.QProcess.ProcessState.NotRunning
    assert dialog.target.minimum() == 20 and dialog.target.maximum() == 30
    dialog.close()


def test_collection_exposes_lost_cycle_count_and_explicit_restart(window, monkeypatch):
    from types import SimpleNamespace

    from rdt_monitor.camera import SynchronizationFault

    trial = MarkerTrial(desktop.fixture_recipe())
    image = np.full((60, 80, 3), 255, dtype=np.uint8)
    trial.observe(CameraFrame(0, 10.0, image))
    with pytest.raises(SynchronizationFault):
        trial.observe(CameraFrame(2, 10.1, image))
    window.camera = SimpleNamespace(
        trial=trial, status=CameraStatus("Streaming", "", 0), stop=lambda: None, join=lambda *args: None
    )
    window._last_camera_frame = CameraFrame(2, 10.1, image)
    window.open_training_collection()
    dialog = window._training_dialog
    dialog._region_changed(0, desktop.Region(0.1, 0.1, 0.8, 0.8))
    dialog.poll()
    assert not dialog.collect_button.isEnabled()
    assert "Frame coverage gap" in dialog.synchronization_status.text()
    assert dialog.cycle_count.text() == "Visually observed cycles: 0"
    assert dialog.synchronize_button.isEnabled()
    assert dialog.synchronize_button.text() == "Restart synchronization"
    calls = []
    monkeypatch.setattr(window, "start_marker_trial", lambda: calls.append("restart"))
    dialog.synchronize_button.click()
    assert calls == ["restart"]
    window.camera = None
    dialog.close()


def test_cycle_aligned_collection_reaches_target_without_automatic_training(window, tmp_path, monkeypatch):
    from datetime import UTC, datetime
    from queue import Queue
    from types import SimpleNamespace

    from rdt_monitor.camera import CameraFrame, CameraStatus, MarkerTrial
    from rdt_monitor.collection import CandidateCollection
    from rdt_monitor.domain import RunRecord, SyncConfig
    from rdt_monitor.replay import Region, ReplayRecipe

    recipe = ReplayRecipe(SyncConfig(1, 0.2, 4), Region(0, 0, 0.1, 0.1), Region(0.9, 0.9, 0.1, 0.1))
    trial = MarkerTrial(recipe)
    camera = SimpleNamespace(
        trial=trial,
        alive=True,
        frames=Queue(maxsize=1),
        status=CameraStatus(
            "Streaming", "Injected frame contract", 0, width=80, height=60, last_received_at=monotonic()
        ),
        stop=lambda: None,
        join=lambda *args: None,
    )
    window.camera = camera
    window.run_record = RunRecord("run-capture", "new-specimen", "sync-v1", "Injected camera", datetime.now(UTC))
    baseline = np.full((60, 80, 3), 255, dtype=np.uint8)
    window._last_camera_frame = CameraFrame(0, 10.0, baseline)
    window.open_training_collection()
    dialog = window._training_dialog
    dialog.folder.setText(str(tmp_path))
    dialog.target.setValue(20)
    dialog._region_changed(0, Region(0.1, 0.1, 0.8, 0.8))
    monkeypatch.setattr(
        desktop, "CandidateCollection", lambda parent, settings: CandidateCollection(parent, settings, capacity=32)
    )
    assert dialog.collect_button.isEnabled()
    dialog.collect_button.click()
    assert not dialog.train_button.isEnabled()
    trial.observe(CameraFrame(0, 10.0, baseline))
    first_source = None
    for cycle in range(1, 22):
        absent = np.zeros((60, 80, 3), dtype=np.uint8)
        present = np.full((60, 80, 3), 30 + cycle, dtype=np.uint8)
        present[:6, :8] = 255
        present[54:, 72:] = 255
        trial.observe(CameraFrame(cycle * 2 - 1, 10 + (cycle * 2 - 1) * 0.05, absent))
        trial.observe(CameraFrame(cycle * 2, 10 + cycle * 0.1, present))
        if cycle == 1:
            first_source = present.copy()
    assert dialog._collection.finished.wait(5)
    dialog.poll()
    finish_candidate_review(dialog)
    assert dialog._document.state == "Awaiting confirmation"
    assert len(dialog._document.records) == 20
    first = dialog._document.records[0]
    saved = cv2.imread(str(dialog._manifest.parent / first.source_file))
    assert np.array_equal(saved, first_source)
    assert first.frame_id == 2
    assert dialog.cycle_count.text() == "Visually observed cycles: 21"
    assert dialog.saved_count.text() == "Saved candidates: 20 / 20"
    assert dialog._process.state() == desktop.QProcess.ProcessState.NotRunning
    assert not dialog.confirm.isChecked()
    assert not dialog.train_button.isEnabled()
    assert "Defect inspection unavailable" in window.assessment_note.text()
    window.camera = None
    dialog.close()


@pytest.fixture
def manual_collection_dialog(window, camera_stub, tmp_path):
    window.connect_camera()
    window.poll()
    window.open_training_collection()
    dialog = window._training_dialog
    dialog.cycle_mode.setCurrentIndex(1)
    dialog.folder.setText(str(tmp_path))
    dialog.target.setValue(20)
    dialog._region_changed(0, desktop.Region(0.1, 0.1, 0.8, 0.8))
    yield dialog
    if dialog._collection is not None:
        dialog._stop_collection()
        assert dialog._collection.finished.wait(5)
        dialog.poll()


def test_manual_collection_requires_click_per_cycle_and_keeps_training_explicit(
    manual_collection_dialog, window, monkeypatch
):
    dialog = manual_collection_dialog
    assert window.camera.trial is None
    assert dialog.collect_button.isEnabled()
    assert dialog.cycle_mode.currentData() == "manual"
    dialog.collect_button.click()
    assert dialog._attached_trial is None
    assert not dialog.cycle_mode.isEnabled()
    assert not dialog.train_button.isEnabled()
    clock = [100.0]
    monkeypatch.setattr(desktop, "monotonic", lambda: clock[0])
    writer = dialog._collection
    for cycle in range(1, 21):
        clock[0] += 1
        pixels = np.full((24, 32, 3), cycle, dtype=np.uint8)
        window._last_camera_frame = CameraFrame(cycle, clock[0], pixels)
        dialog.poll()
        assert len(writer.document.records) == cycle - 1
        assert dialog.capture_cycle_button.isEnabled()
        dialog.capture_cycle_button.click()
        assert not dialog.capture_cycle_button.isEnabled()
        for _ in range(500):
            if len(writer.document.records) == cycle:
                break
            writer.finished.wait(0.01)
        assert len(writer.document.records) == cycle
        dialog.poll()
        assert not dialog.capture_cycle_button.isEnabled()
    assert writer.finished.wait(5)
    dialog.poll()
    finish_candidate_review(dialog)
    assert dialog._document.settings.cycle_mode == "manual"
    assert dialog._document.state == "Awaiting confirmation"
    assert dialog.cycle_count.text() == "Operator-declared cycles saved: 20"
    assert not dialog.confirm.isChecked()
    assert not dialog.train_button.isEnabled()
    assert window.run_record is None
    assert window.cycles.text() == "0"
    for cycle, record in enumerate(dialog._document.records, 1):
        assert record.cycle == cycle
        assert np.all(cv2.imread(str(dialog._manifest.parent / record.source_file)) == cycle)
    review_all_candidates(dialog)
    assert dialog.confirm.isEnabled()
    dialog.confirm.setChecked(True)
    assert dialog.train_button.isEnabled()
    assert dialog._process.state() == desktop.QProcess.ProcessState.NotRunning


def test_manual_capture_rejects_stale_frames_and_stops_on_disconnect(manual_collection_dialog, window, monkeypatch):
    dialog = manual_collection_dialog
    dialog.collect_button.click()
    clock = 100.0
    monkeypatch.setattr(desktop, "monotonic", lambda: clock)
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    window._last_camera_frame = CameraFrame(10, clock - 2, image)
    dialog.poll()
    assert not dialog.capture_cycle_button.isEnabled()
    dialog._capture_manual_cycle()
    assert "fresh camera frame" in dialog.fault.text()
    assert dialog._manual_count == 0
    window.disconnect_camera()
    writer = dialog._collection
    dialog.poll()
    assert writer.finished.wait(5)
    dialog.poll()
    assert dialog._document.state == "Incomplete"
    assert not dialog.train_button.isEnabled()


def test_manual_collection_ignores_marker_fault_but_early_stop_is_incomplete(manual_collection_dialog, window):
    from rdt_monitor.camera import SynchronizationFault

    dialog = manual_collection_dialog
    trial = MarkerTrial(desktop.fixture_recipe())
    image = np.full((24, 32, 3), 255, dtype=np.uint8)
    trial.observe(CameraFrame(0, 10.0, image))
    with pytest.raises(SynchronizationFault):
        trial.observe(CameraFrame(2, 10.1, image))
    window.camera.trial = trial
    dialog._refresh()
    assert dialog.collect_button.isEnabled()
    dialog.collect_button.click()
    writer = dialog._collection
    dialog.poll()
    assert writer.document.state == "Collecting"
    assert "Manual" in dialog.synchronization_status.text()
    dialog.stop_collection.click()
    assert writer.finished.wait(5)
    dialog.poll()
    assert dialog._document.settings.cycle_mode == "manual"
    assert dialog._document.state == "Incomplete"
    assert not dialog.train_button.isEnabled()


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
def test_manual_collection_controls_fit(manual_collection_dialog, application, size):
    dialog = manual_collection_dialog
    dialog.resize(*size)
    dialog.show()
    application.processEvents()
    assert (dialog.width(), dialog.height()) == size
    assert dialog.capture_cycle_button.isVisible()
    assert not dialog.synchronize_button.isVisible()
    for control in (dialog.cycle_mode, dialog.collect_button, dialog.capture_cycle_button, dialog.stop_collection):
        assert control.parentWidget().rect().contains(control.geometry())


def test_training_process_start_failure_is_visible_and_never_marks_model_ready(candidate_dialog, tmp_path):
    dialog = candidate_dialog
    review_all_candidates(dialog)
    dialog.python.setText(str(tmp_path / "missing-python.exe"))
    dialog.confirm.setChecked(True)
    dialog.train_button.click()
    assert not dialog._process.waitForStarted(2000)
    assert dialog._export is None
    assert "unavailable" in dialog.fault.text()
    assert "Unknown" in dialog.state.text()


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
def test_training_collection_native_render(candidate_dialog, application, tmp_path, size):
    dialog = candidate_dialog
    review_all_candidates(dialog)
    dialog.confirm.setChecked(True)
    dialog.resize(*size)
    application.processEvents()
    assert dialog.size().width() == size[0] and dialog.size().height() == size[1]
    assert dialog.rect().contains(dialog.fault.geometry())
    assert dialog.grab().save(str(tmp_path / f"training-collection-{size[0]}x{size[1]}-review.png"))
    dialog.stage_tabs.setCurrentIndex(2)
    application.processEvents()
    assert dialog.train_button.parentWidget().rect().contains(dialog.train_button.geometry())
    assert dialog.editor.height() >= 180
    assert dialog.grab().save(str(tmp_path / f"training-collection-{size[0]}x{size[1]}.png"))


@pytest.fixture
def offline_runtime_model(tmp_path):
    runtime = pytest.importorskip("openvino")
    operations = runtime.opset13
    parameter = operations.parameter([1, 3, -1, -1], np.float32, name="image")
    heatmap = operations.reduce_mean(parameter, operations.constant([1], np.int64), keep_dims=False)
    score = operations.reduce_mean(heatmap, operations.constant([1, 2], np.int64), keep_dims=False)
    heatmap.output(0).get_tensor().set_names({"anomaly_map"})
    score.output(0).get_tensor().set_names({"pred_score"})
    model = runtime.Model([score, heatmap], [parameter], "gui-runtime-contract-not-a-trained-defect-model")
    path = tmp_path / "contract.xml"
    runtime.save_model(model, path)
    return path


@pytest.fixture
def offline_dialog(window, monkeypatch, offline_runtime_model):
    window.open_offline_inspection()
    dialog = window._inspection_dialog
    image = Path(__file__).parents[1] / "fixtures" / "pink-marks-observed.png"
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(image), ""))
    dialog.open_image_button.click()
    assert dialog._busy
    assert not dialog.open_model_button.isEnabled()
    dialog._image_worker.join(10)
    dialog._poll_result()
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(offline_runtime_model), ""))
    dialog.open_model_button.click()
    yield dialog
    if dialog._worker is not None:
        dialog._worker.join(10)
    dialog.close()


def test_offline_inspection_is_independent_and_requires_inputs(window):
    window.offline_inspection_action.trigger()
    dialog = window._inspection_dialog
    assert dialog.isVisible()
    assert not dialog.inspect_button.isEnabled()
    assert not dialog.save_image_button.isEnabled()
    assert dialog.editor.single_region
    assert dialog.editor.region_labels[0] == "INSPECTION ROI"
    assert "Unknown" in window.condition.text()
    assert "Defect inspection unavailable" in window.assessment_note.text()
    assert window.run_record is None
    window.offline_inspection_action.trigger()
    assert window._inspection_dialog is dialog


def test_offline_roi_drawing_stays_single_region(offline_dialog, application):
    dialog = offline_dialog
    dialog.resize(900, 650)
    application.processEvents()
    rectangle = dialog.editor.image_rect()
    origin = QPoint(
        round(rectangle.left() + rectangle.width() * 0.1), round(rectangle.top() + rectangle.height() * 0.1)
    )
    destination = QPoint(
        round(rectangle.left() + rectangle.width() * 0.8), round(rectangle.top() + rectangle.height() * 0.8)
    )
    QTest.mousePress(dialog.editor, Qt.MouseButton.LeftButton, pos=origin)
    QTest.mouseMove(dialog.editor, destination)
    QTest.mouseRelease(dialog.editor, Qt.MouseButton.LeftButton, pos=destination)
    assert dialog._region is not None
    assert dialog.editor.active_role == 0
    assert dialog.editor.regions[1] is None
    assert dialog.inspect_button.isEnabled()
    assert dialog.coordinates[2].value() == pytest.approx(dialog._region.width * 100, abs=0.01)


def test_offline_real_runtime_result_is_snapshot_aligned_and_invalidates(offline_dialog, window):
    dialog = offline_dialog
    dialog._region_changed(0, desktop.Region(0.1, 0.1, 0.8, 0.8))
    original = dialog._source.copy()
    dialog.inspect_button.click()
    assert dialog._busy
    assert not dialog.open_image_button.isEnabled()
    assert not dialog.open_model_button.isEnabled()
    assert not dialog.threshold.isEnabled()
    dialog._worker.join(10)
    dialog._poll_result()
    assert dialog._result is not None
    assert "Unqualified" in dialog.assessment.text()
    assert dialog.save_image_button.isEnabled()
    assert np.array_equal(dialog._result.source_image, original)
    assert np.array_equal(dialog._composite()[0, 0], original[0, 0])
    assert not np.array_equal(dialog._composite(), original)
    dialog.heatmap.setChecked(False)
    assert np.array_equal(dialog._composite(), original)
    assert "Unknown" in window.condition.text()
    assert window.inspections == []
    dialog.threshold.setValue(0.7)
    assert dialog._result is None
    assert "Unknown" in dialog.assessment.text()
    assert not dialog.save_image_button.isEnabled()
    assert np.array_equal(dialog._composite(), original)


def test_offline_failure_removes_previous_result(offline_dialog, monkeypatch, tmp_path):
    dialog = offline_dialog
    dialog.full_image_button.click()
    dialog.inspect_button.click()
    dialog._worker.join(10)
    dialog._poll_result()
    assert dialog._result is not None
    broken = tmp_path / "broken.onnx"
    broken.write_bytes(b"invalid model")
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(broken), ""))
    dialog.open_model_button.click()
    assert dialog._result is None
    dialog.inspect_button.click()
    dialog._worker.join(10)
    dialog._poll_result()
    assert "Unknown" in dialog.assessment.text()
    assert dialog.error.text()
    assert not dialog.save_image_button.isEnabled()


def test_offline_late_result_cannot_override_changed_inputs(offline_dialog):
    dialog = offline_dialog
    dialog.full_image_button.click()
    dialog.inspect_button.click()
    dialog._worker.join(10)
    dialog.threshold.setValue(0.8)
    dialog._poll_result()
    assert dialog._result is None
    assert "inputs changed" in dialog.assessment.text()
    assert not dialog.save_image_button.isEnabled()


def test_offline_snapshot_save_and_bad_image_clear_assessment(offline_dialog, monkeypatch, tmp_path):
    dialog = offline_dialog
    dialog.full_image_button.click()
    dialog.inspect_button.click()
    dialog._worker.join(10)
    dialog._poll_result()
    snapshot = tmp_path / "inspection.png"
    monkeypatch.setattr(desktop.QFileDialog, "getSaveFileName", lambda *args: (str(snapshot), ""))
    expected = dialog._composite()
    dialog.save_image_button.click()
    assert np.array_equal(cv2.imread(str(snapshot)), expected)
    assert "not qualified" in dialog.error.text()
    broken = tmp_path / "bad.png"
    broken.write_bytes(b"not image pixels")
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(broken), ""))
    dialog.open_image_button.click()
    assert dialog._result is None and dialog._source is None
    assert not dialog.open_model_button.isEnabled()
    dialog._image_worker.join(10)
    dialog._poll_result()
    assert dialog._result is None and dialog._source is None
    assert not dialog.inspect_button.isEnabled()
    assert "Unknown" in dialog.assessment.text()


@pytest.mark.parametrize("size", [(900, 650), (1280, 800)])
def test_offline_inspection_native_render(offline_dialog, application, tmp_path, size):
    dialog = offline_dialog
    dialog.resize(*size)
    dialog._region_changed(0, desktop.Region(0.1, 0.1, 0.8, 0.8))
    dialog.inspect_button.click()
    dialog._worker.join(10)
    dialog._poll_result()
    application.processEvents()
    assert dialog.rect().contains(dialog.error.geometry())
    assert dialog.inspect_button.parentWidget().rect().contains(dialog.inspect_button.geometry())
    assert dialog.editor.height() >= 180
    assert dialog.grab().save(str(tmp_path / f"offline-inspection-{size[0]}x{size[1]}.png"))


@pytest.fixture(scope="session")
def application():
    instance = QApplication.instance() or QApplication([])
    assert isinstance(instance, QApplication)
    instance.setStyle("Fusion")
    instance.setStyleSheet(files("rdt_monitor").joinpath("styles.qss").read_text(encoding="utf-8"))
    return instance


@pytest.fixture
def window(application):
    instance = desktop.MainWindow()
    instance.specimen.setText("PCB-TEST-001")
    yield instance
    if instance.pipeline is not None:
        instance.pipeline.stop()
        instance.pipeline.join(10)
    if instance.camera is not None:
        instance.camera.stop()
        instance.camera.join(10)
    instance.close()
    application.processEvents()


def run_fixture(window, monkeypatch):
    monkeypatch.setattr(
        desktop,
        "ReplayPipeline",
        lambda factory, recipe: ReplayPipeline(factory, recipe, realtime=False),
    )
    window.fixture_button.click()
    window.start_replay()
    window.pipeline.join(10)
    assert not window.pipeline.alive
    window.poll()


def test_startup_is_unqualified_and_inspection_is_unavailable(window):
    assert not window.start_button.isEnabled()
    assert "Unknown" in window.condition.text()
    assert "Not recorded" in window.evidence.text()
    assert "Defect inspection unavailable" in window.assessment_note.text()
    assert window.fault.isHidden()
    assert window.windowTitle() == "RDT Reliability Monitor"
    assert window.fixture_button.isHidden()
    assert window.open_button.isHidden()
    assert window.start_button.isHidden()
    assert window.diagnostics_menu.title() == "Diagnostics"
    assert window.run_record is None


def test_application_font_has_latin_glyphs(application):
    raw_font = QRawFont.fromFont(application.font())
    assert raw_font.isValid(), "Qt requires an installed font for readable native render checks"
    assert raw_font.supportsCharacter(ord("R")), "Application font must render Latin labels"


def test_replay_settings_lock_during_run(window):
    window.fixture_button.click()
    window.start_replay()
    assert not window.settings_button.isEnabled()
    assert not window.specimen.isEnabled()
    assert not window.open_button.isEnabled()
    assert not window.start_button.isEnabled()
    assert window.stop_button.isEnabled()
    assert "locked" in window.run_label.text()
    window.stop_replay()
    window.pipeline.join(10)
    window.poll()
    assert window.settings_button.isEnabled()
    assert window.start_button.isEnabled()


def test_fixture_inspections_remain_unknown_and_not_saved(window, monkeypatch):
    run_fixture(window, monkeypatch)
    assert window.cycles.text() == "4"
    assert len(window.inspections) == 4
    assert all("Unknown" in record.reason for record in window.inspections)
    assert window.health.text() == "Ended"
    assert "Stopped" in window.freshness.text()
    assert window.evidence.text() == "Not recorded"
    assert window.history.rowCount() == 10


def test_new_run_requires_operator_command_and_gets_fresh_identity(window, monkeypatch):
    run_fixture(window, monkeypatch)
    run_id = window.run_record.run_id
    window.poll()
    assert window.run_record.run_id == run_id
    assert not window.pipeline.alive
    window.start_button.click()
    window.pipeline.join(10)
    window.poll()
    assert window.run_record.run_id != run_id
    assert len(window.inspections) == 4


def test_missing_video_is_a_visible_fault_not_a_normal_result(window):
    window._source = "does-not-exist.avi"
    window.start_replay()
    window.pipeline.join(10)
    window.poll()
    assert window.health.text() == "Fault"
    assert "Unknown" in window.condition.text()
    assert "existing local recording" in window.fault.text()


def test_invalid_settings_stay_open_without_modifying_recipe(window):
    dialog = desktop.ReplaySettings(window.recipe, window)
    dialog._regions[0][2].setValue(0)
    original = dialog.recipe
    dialog._save()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.recipe == original
    assert "positive size" in dialog._error.text()
    dialog.close()


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
def test_native_render_is_nonblank_and_controls_fit(window, application, monkeypatch, tmp_path, size):
    run_fixture(window, monkeypatch)
    window.resize(*size)
    window.show()
    application.processEvents()
    assert (window.width(), window.height()) == size
    assert window.centralWidget().rect().contains(window.fault.geometry())
    assert window.start_button.isHidden()
    assert window.live_view.width() >= 320 and window.live_view.height() >= 180
    image = window.grab().toImage()
    assert not image.isNull()
    preview = window.live_view.grab().toImage()
    assert preview.pixelColor(preview.width() // 2, preview.height() // 2) != QColor("#08090C")
    assert image.save(str(tmp_path / f"replay-{size[0]}x{size[1]}.png"))


def test_session_history_is_bounded(window):
    for cycle in range(510):
        window._log("Cycle start", "Unknown", "0 s", cycle, "0")
    assert window.history.rowCount() == 500
    assert window.history.item(0, 2).text() == "10"
    assert window.history.editTriggers() == window.history.EditTrigger.NoEditTriggers
    assert window.source_label.textFormat() == Qt.TextFormat.PlainText


@pytest.fixture
def camera_stub(monkeypatch):
    instances = []

    class PreviewStub:
        def __init__(self, index):
            self.alive = False
            self.trial = None
            self.frames = Queue(maxsize=1)
            self.status = CameraStatus("Stopped", "Disconnected", index)
            instances.append(self)

        def start(self):
            self.alive = True
            self.status = CameraStatus("Streaming", "Preview only", self.status.index, 700, 32, 24, 0, monotonic())
            pixels = np.full((24, 32, 3), 100, dtype=np.uint8)
            pixels.flags.writeable = False
            self.frames.put_nowait(CameraFrame(0, self.status.last_received_at, pixels))

        def stop(self):
            self.alive = False
            self.status = CameraStatus("Stopped", "Disconnected; last frame is not live", self.status.index)

        def start_trial(self, recipe, capture=None):
            self.trial = MarkerTrial(recipe)
            self.trial.set_cycle_capture(capture)

        def stop_trial(self):
            trial = self.trial
            self.trial = None
            if trial is not None:
                trial.request_stop()
            return trial

        def join(self, timeout):
            assert timeout >= 0

    monkeypatch.setattr(desktop, "CameraPreview", PreviewStub)
    return instances


def test_camera_connect_previews_without_inspecting(window, camera_stub):
    window.camera_index.setValue(2)
    window.connect_button.click()
    window.poll()
    assert camera_stub[0].status.index == 2
    assert window.health.text() == "Streaming"
    assert "Live" in window.freshness.text()
    assert "unavailable" in window.camera_details.text()
    assert window.live_view._recipe is None
    assert "Unknown" in window.condition.text()
    assert "preview only" in window.sync.text()
    assert window.run_record is None and window.inspections == []
    assert window.cycles.text() == "0"
    assert not window.fixture_button.isEnabled()
    assert not window.open_button.isEnabled()
    assert not window.camera_index.isEnabled()
    assert not window.start_button.isEnabled()
    assert not window.settings_button.isEnabled()


def test_production_commands_cannot_start_development_work(window, camera_stub):
    window.connect_camera()
    window.poll()
    assert window.trial_button.isEnabled()
    assert window.load_recipe_button.isEnabled()
    window.start_work()
    assert "Live defect detector" in window.fault.text()
    assert "Approved recipe" in window.fault.text()
    assert window.run_record is None
    assert window.lifecycle.snapshot.state == RunState.STOPPED
    assert window.camera.trial is None


def test_run_controls_are_on_main_screen_not_in_diagnostics(window, application, camera_stub):
    window.connect_camera()
    window.poll()
    window.show()
    application.processEvents()
    window._update_diagnostics()
    actions = {action.text(): action for action in window.diagnostics_menu.actions()}
    assert window.diagnostics_menu.isEnabled()
    for control in (window.load_recipe_button, window.trial_button, window.stop_work_button):
        assert control.isVisible()
    assert window.load_recipe_button.isEnabled()
    assert window.trial_button.isEnabled()
    assert not window.stop_work_button.isEnabled()
    assert window.trial_button.text() == "Start"
    assert window.stop_work_button.text() == "Stop"
    assert "Start development trial" not in actions
    assert "Load development recipe" not in actions
    assert "Load recipe" not in actions
    assert not actions["Start replay"].isEnabled()
    assert not actions["Open recording"].isEnabled()


def test_stop_drains_cycles_not_yet_polled_and_retains_run_identity(window, camera_stub, monkeypatch):
    window.connect_camera()
    window.poll()
    approve_trial(monkeypatch)
    window.trial_button.click()
    record = window.run_record
    trial = window.camera.trial
    for index in range(3):
        pixels = np.full((24, 32, 3), 0 if index == 1 else 255, dtype=np.uint8)
        trial.observe(CameraFrame(index, 10 + index / 30, pixels))
    assert window.cycles.text() == "0"
    window.stop_work_button.click()
    assert trial.stopped
    assert window.cycles.text() == "1"
    assert window.inspected_cycles.text() == "Inspected 0 | Uninspected 1"
    assert window.lifecycle.snapshot.state == RunState.STOPPED
    assert window.lifecycle.snapshot.record == record
    assert len(window.inspections) == 1
    assert window.camera.alive
    window.poll()
    window.poll()
    assert window.lifecycle.snapshot.observed == 1
    assert len(window.inspections) == 1


def test_disconnect_finalizes_development_run_before_camera_release(window, camera_stub, monkeypatch):
    window.connect_camera()
    window.poll()
    approve_trial(monkeypatch)
    window.trial_button.click()
    record = window.run_record
    window.disconnect_camera()
    assert window.lifecycle.snapshot.state == RunState.STOPPED
    assert window.lifecycle.snapshot.record == record
    assert not window.camera.alive
    assert window.camera.trial is None


def test_disconnect_waits_asynchronously_for_accepted_capture(window, camera_stub, monkeypatch):
    window.connect_camera()
    window.poll()
    approve_trial(monkeypatch)
    window.trial_button.click()
    trial = window.camera.trial
    entered, release = Event(), Event()

    def capture(image, result):
        entered.set()
        assert release.wait(5)

    trial.set_cycle_capture(capture)
    present = np.full((24, 32, 3), 255, dtype=np.uint8)
    trial.observe(CameraFrame(0, 10, present))
    trial.observe(CameraFrame(1, 10.05, np.zeros_like(present)))
    worker = Thread(target=trial.observe, args=(CameraFrame(2, 10.1, present),))
    worker.start()
    try:
        assert entered.wait(5)
        window.disconnect_camera()
        assert window.camera.alive
        assert window.lifecycle.snapshot.state == RunState.STOPPING
        assert window.lifecycle.snapshot.observed == 1
        assert not window.trial_button.isEnabled()
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    window.poll()
    assert not window.camera.alive
    assert window.lifecycle.snapshot.state == RunState.STOPPED
    assert window.lifecycle.snapshot.uninspected == 1


def test_camera_and_replay_cannot_run_together(window, camera_stub):
    window.fixture_button.click()
    window.connect_camera()
    window.start_replay()
    assert window.pipeline is None
    assert len(camera_stub) == 1
    window.connect_camera()
    assert len(camera_stub) == 1


def test_camera_disconnect_marks_stale_and_requires_explicit_reconnect(window, camera_stub):
    window.connect_button.click()
    window.poll()
    window.disconnect_button.click()
    window.poll()
    assert "Not live" in window.freshness.text()
    assert window.connect_button.isEnabled()
    assert window.camera_index.isEnabled()
    assert not window.start_button.isEnabled()
    assert len(camera_stub) == 1
    window.poll()
    assert len(camera_stub) == 1
    window.connect_button.click()
    assert len(camera_stub) == 2


def test_camera_fault_is_visible_without_clearing_unknown_assessment(window, camera_stub):
    window.connect_camera()
    worker = camera_stub[0]
    worker.status = CameraStatus("Fault", "USB disconnected", 0)
    worker.alive = False
    window.poll()
    assert window.health.text() == "Camera fault"
    assert "USB disconnected" in window.fault.text()
    assert not window.fault.isHidden()
    assert "Not live" in window.freshness.text()
    assert "Unknown" in window.condition.text()
    assert window.connect_button.isEnabled()


def test_replay_disables_camera_connection(window):
    window.fixture_button.click()
    window.start_replay()
    window.connect_camera()
    assert window.camera is None
    assert not window.connect_button.isEnabled()
    assert not window.camera_index.isEnabled()


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
def test_camera_render_controls_fit(window, application, camera_stub, tmp_path, size):
    window.connect_camera()
    window.poll()
    window.resize(*size)
    window.show()
    application.processEvents()
    assert (window.width(), window.height()) == size
    assert window.centralWidget().rect().contains(window.fault.geometry())
    assert not window.camera_details.isVisible()
    assert window.live_view.width() >= 320 and window.live_view.height() >= 180
    for control in (
        window.connect_button,
        window.disconnect_button,
        window.load_recipe_button,
        window.trial_button,
        window.stop_work_button,
    ):
        assert control.isVisible()
        assert control.parentWidget().rect().contains(control.geometry())
    assert window.live_view._recipe is None
    image = window.grab().toImage()
    assert not image.isNull()
    assert image.save(str(tmp_path / f"camera-{size[0]}x{size[1]}.png"))


def test_inspection_menu_exposes_collection_without_starting_work(window, application):
    window.show()
    application.processEvents()
    assert window.training_action.text() == "Collect and train"
    window.training_action.trigger()
    dialog = window._training_dialog
    assert dialog is not None and dialog.isVisible()
    assert dialog._collection is None
    assert not dialog._training()
    assert window.run_record is None
    dialog.close()


def test_results_button_opens_central_folder_without_changing_run(window, monkeypatch, tmp_path):
    root = tmp_path / "inspection-results"
    monkeypatch.setattr(desktop, "INSPECTION_DIRECTORY", root)
    opened = []

    def open_url(url):
        opened.append(url.toLocalFile())
        return True

    monkeypatch.setattr(desktop.QDesktopServices, "openUrl", open_url)
    window.results_button.click()
    assert root.is_dir()
    assert [Path(path) for path in opened] == [root]
    assert window.run_record is None
    assert window.condition.property("verdict_color") == "grey"


@pytest.mark.parametrize("failure", ["desktop", "storage"])
def test_results_button_failure_is_visible(window, monkeypatch, tmp_path, failure):
    root = tmp_path / "inspection-results"
    if failure == "storage":
        root.write_text("Not a directory")
    monkeypatch.setattr(desktop, "INSPECTION_DIRECTORY", root)
    monkeypatch.setattr(desktop.QDesktopServices, "openUrl", lambda url: False)
    window.results_button.click()
    assert "Inspection results not opened:" in window.fault.text()
    assert str(root) in window.fault.text()


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
@pytest.mark.parametrize("details", [False, True])
@pytest.mark.parametrize(
    ("outcome", "color", "text"),
    [
        (desktop.InspectionOutcome.NO_DEFECT, "green", "Cycle 12345 - No defect detected"),
        (desktop.InspectionOutcome.NEEDS_REVIEW, "yellow", "Cycle 12345 - Needs review"),
        (desktop.InspectionOutcome.DEFECT, "red", "Cycle 12345 - Defect detected"),
        (desktop.InspectionOutcome.UNINSPECTED, "grey", "Cycle 12345 - Uninspected / finalization timed out"),
    ],
)
def test_verdict_palette_and_actions_fit_native_window(window, application, size, details, outcome, color, text):
    window.resize(*size)
    window._show_verdict(outcome, text)
    window._live_defects = 12345
    window._update_defect_alarm()
    window.assessment_note.setText("UNQUALIFIED | Score 0.550 | Threshold 0.500 | Review margin 0.050")
    window.evidence.setText("Failure record saved")
    window._set_notice("Inspection results unavailable. Review the output folder permissions before restarting.")
    window.details_button.setChecked(details)
    window.show()
    application.processEvents()
    assert (window.width(), window.height()) == size
    assert window.condition.property("verdict_color") == color
    assert window.condition.text() == text
    rendered = window.condition.grab().toImage()
    ring_color = rendered.pixelColor(rendered.width() // 2, round(5 * rendered.devicePixelRatio())).name()
    assert ring_color == {"grey": "#a7b4c4", "green": "#4ade80", "yellow": "#facc15", "red": "#fb7185"}[color]
    assert color in window.condition.accessibleName()
    for control in (window.condition, window.defect_alarm, window.results_button, window.operator_status):
        assert control.isVisible()
        assert control.parentWidget().rect().contains(control.geometry())
    assert window.live_view.width() >= 320 and window.live_view.height() >= 180
    assert window.condition.height() >= window.condition.heightForWidth(window.condition.width())
    assert window.trial_button.objectName() == "primary"
    assert window.defect_alarm.property("latched") is True
    assert window.condition.parentWidget().geometry().bottom() < window.trial_button.parentWidget().geometry().top()
    assert window.condition.geometry().bottom() < window.defect_alarm.geometry().top()
    assert not window.condition.styleSheet()  # Semantic colors belong to the shared theme.


def test_keyboard_order_reaches_camera_and_recipe_actions(window, application):
    window.show()
    application.processEvents()
    window.specimen.setFocus()
    QTest.keyClick(window.specimen, Qt.Key.Key_Tab)
    assert window.connect_button.hasFocus()
    QTest.keyClick(window.connect_button, Qt.Key.Key_Tab)
    assert window.load_recipe_button.hasFocus()  # Disconnected state skips disabled Disconnect.


def test_operator_view_hides_diagnostics_but_details_remains_accessible(window, application):
    window.show()
    application.processEvents()
    technical = (
        window.camera_index,
        window.camera_details,
        window.frame_label,
        window.freshness,
        window.source_label,
        window.run_label,
        window.health,
        window.sync,
        window.inspected_cycles,
        window.evidence,
        window.assessment_note,
    )
    assert not window.details_panel.isVisible()
    assert window.history.isVisible()
    assert all(not control.isVisible() for control in technical)
    for control in (
        window.recipe_summary,
        window.condition,
        window.cycles,
        window.defect_alarm,
        window.operator_status,
        window.load_recipe_button,
        window.trial_button,
        window.stop_work_button,
    ):
        assert control.isVisible()
    window._log("Cycle inspection", "Defect detected", "1 s", 1, "2")
    assert window.history.rowCount() == 1
    window._set_notice("Evidence not saved: disk full")
    assert window.fault.isVisible()
    window.details_button.click()
    application.processEvents()
    assert all(control.isVisible() for control in technical)
    assert window.history.rowCount() == 1
    window.details_button.click()
    assert window.history.isVisible()
    assert window.fault.isVisible()


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
def test_expanded_details_fit_without_hiding_operator_controls(window, application, size):
    window.resize(*size)
    window.show()
    window.details_button.click()
    application.processEvents()
    assert (window.width(), window.height()) == size
    assert window.history.isVisible()
    assert window.camera_index.isVisible()
    assert window.event_log_panel.rect().contains(window.history.geometry())
    assert window.live_view.width() >= 320 and window.live_view.height() >= 180
    for button in (window.trial_button, window.stop_work_button, window.load_recipe_button):
        assert button.isVisible()
    assert window.details_button.text() == "Hide details"
    assert window.condition.parentWidget().rect().contains(window.condition.geometry())
    assert window.defect_alarm.parentWidget().rect().contains(window.defect_alarm.geometry())
    assert window.condition.geometry().bottom() < window.defect_alarm.geometry().top()
    verdict_panel = window.condition.parentWidget()
    controls = window.trial_button.parentWidget()
    assert verdict_panel.geometry().bottom() < controls.geometry().top()
    assert verdict_panel.parentWidget().rect().contains(verdict_panel.geometry())
    assert controls.parentWidget().rect().contains(controls.geometry())
    window.details_scroll.verticalScrollBar().setValue(window.details_scroll.verticalScrollBar().maximum())
    application.processEvents()
    assert window.history.visibleRegion().isEmpty() is False


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
@pytest.mark.parametrize("details", [False, True])
def test_event_log_is_visible_below_monitor_with_details_open_or_closed(window, application, size, details):
    window.resize(*size)
    window.details_button.setChecked(details)
    for cycle in range(1, 8):
        window._log("Cycle inspection", "No defect detected", f"{cycle} s", cycle, str(cycle))
    window.show()
    application.processEvents()
    assert (window.width(), window.height()) == size
    assert window.history.isVisible()
    assert window.history.rowCount() == 7
    assert window.roi_view.isVisible()
    assert window.roi_view.parentWidget().rect().contains(window.roi_view.geometry())
    assert window.history.viewport().height() >= window.history.rowHeight(0)
    log_top = window.history.mapTo(window.centralWidget(), QPoint(0, 0)).y()
    for control in (window.live_view, window.trial_button, window.stop_work_button, window.results_button):
        bottom = control.mapTo(window.centralWidget(), control.rect().bottomRight()).y()
        assert bottom < log_top
    assert window.live_view.width() >= 320 and window.live_view.height() >= 180


def test_inspection_heatmap_marks_only_roi_and_preserves_source(tmp_path):
    model = tmp_path / "model.xml"
    model.write_bytes(b"model")
    model.with_suffix(".bin").write_bytes(b"weights")
    source = np.full((20, 30, 3), 100, dtype=np.uint8)
    settings = desktop.DetectionSettings(model, desktop.Region(0.2, 0.25, 0.4, 0.5), 0.5)
    left, top, right, bottom = settings.region.pixels(source)
    heatmap = np.zeros((bottom - top, right - left), dtype=np.float32)
    heatmap[2:4, 3:6] = 1
    result = desktop.DetectionResult(settings, 0.9, heatmap, source)
    overlay = desktop._inspection_overlay(result)
    expected = source.copy()
    expected[top + 2 : top + 4, left + 3 : left + 6] = (39, 39, 193)
    np.testing.assert_array_equal(overlay, expected)
    np.testing.assert_array_equal(source, np.full_like(source, 100))


def test_reference_layout_keeps_controls_below_verdict_not_on_camera(window, application):
    window.show()
    application.processEvents()
    assert window.condition.parentWidget().objectName() == "verdictPanel"
    for control in (window.trial_button, window.stop_work_button, window.load_recipe_button, window.results_button):
        assert control.parentWidget().objectName() == "controlPanel"
        assert control.isVisible()
    assert window.trial_button.parentWidget().y() > window.condition.parentWidget().y()
    assert window.live_view.parentWidget().objectName() == "cameraPanel"
    assert window.camera_connection.text() == "Disconnected"
    assert window.camera_connection.property("connected") is False
    assert not window.details_panel.isVisible()


def test_camera_connection_badge_does_not_confuse_preview_with_inspection(window, camera_stub, monkeypatch):
    now = monotonic()
    monkeypatch.setattr(desktop, "monotonic", lambda: now)
    window.connect_camera()
    window.camera.status = desktop.replace(window.camera.status, last_received_at=now)
    window.poll()
    assert window.camera_connection.text() == "Live"
    assert window.camera_connection.property("connected") is True
    assert window.condition.property("verdict_color") == "grey"
    assert window.run_record is None
    window.camera.status = desktop.replace(window.camera.status, last_received_at=now - 2)
    window.poll()
    assert window.camera_connection.text() == "Preview stale"
    assert window.camera_connection.property("connected") is False
    window.disconnect_camera()
    window.poll()
    assert window.camera_connection.text() == "Disconnected"
    assert window.condition.property("verdict_color") == "grey"


def test_review_band_preview_tracks_settings_and_invalid_bands(candidate_dialog):
    dialog = candidate_dialog
    assert "0.450 - 0.550 (inclusive)" in dialog.review_band_note.text()
    dialog.live_review_margin.setValue(0.1)
    assert "0.400 - 0.600 (inclusive)" in dialog.review_band_note.text()
    dialog.live_threshold.setValue(0.02)
    assert "must stay within 0 - 1" in dialog.review_band_note.text()


def test_compact_training_panel_scrolls_without_enlarging_window(candidate_dialog, application):
    dialog = candidate_dialog
    dialog.resize(880, 720)
    scroll = dialog.findChild(desktop.QScrollArea)
    assert scroll is not None
    for tab, last_control in (
        (0, dialog.load_button),
        (1, dialog.confirm),
        (2, dialog.save_inspection_recipe_button),
    ):
        dialog.stage_tabs.setCurrentIndex(tab)
        dialog.show()
        application.processEvents()
        scroll.ensureWidgetVisible(last_control)
        application.processEvents()
        assert (dialog.width(), dialog.height()) == (880, 720)
        assert scroll.horizontalScrollBar().maximum() == 0
        position = last_control.mapTo(scroll.viewport(), QPoint(0, 0))
        assert scroll.viewport().rect().contains(position)
        assert dialog.editor.height() >= 180


def approve_trial(monkeypatch):
    def accept(dialog):
        dialog.recognition_mode.setCurrentIndex(1)
        for inputs, values in zip(dialog._regions, ((0, 0, 50, 100), (50, 0, 50, 100)), strict=True):
            for spin, value in zip(inputs, values, strict=True):
                spin.setValue(value)
        dialog._stable.setValue(1)
        dialog._confirmed.setChecked(True)
        dialog._save()
        assert dialog.result() == QDialog.DialogCode.Accepted
        return dialog.result()

    monkeypatch.setattr(desktop.ReplaySettings, "exec", accept)


def lose_synchronization(window, monkeypatch):
    approve_trial(monkeypatch)
    window.connect_camera()
    window.poll()
    window.trial_button.click()
    session = window.camera.trial
    pixels = np.zeros((24, 32, 3), dtype=np.uint8)
    present = np.full_like(pixels, 255)
    for index in range(42):
        session.observe(CameraFrame(index, 100 + index / 10, pixels if index == 1 else present))
    with pytest.raises(RuntimeError, match="overdue"):
        session.observe(CameraFrame(42, 104.25, present))
    window.camera.frames.put_nowait(CameraFrame(200, monotonic(), pixels))
    window.poll()
    return session


def test_synchronization_requires_specimen_id_and_locks_run_identity(window, camera_stub, monkeypatch):
    window.specimen.clear()
    window.connect_camera()
    window.poll()
    approve_trial(monkeypatch)
    window.trial_button.click()
    assert window.camera.trial is None
    assert "specimen ID" in window.fault.text()
    assert window.specimen.isEnabled()
    window.specimen.setText("  PCB-2026-001  ")
    window.trial_button.click()
    assert window.run_record.specimen_id == "PCB-2026-001"
    assert not window.specimen.isEnabled()
    assert "PCB-2026-001" in window.run_label.text()


def test_synchronization_loss_keeps_preview_live_and_requires_explicit_restart(window, camera_stub, monkeypatch):
    session = lose_synchronization(window, monkeypatch)
    previous_run = window.run_record
    assert window.camera.alive
    assert window.health.text() == "Degraded"
    assert "Live" in window.freshness.text()
    assert "overdue" in window.sync.text()
    assert "Synchronization lost" in window.fault.text()
    assert window.trial_button.text() == "Restart"
    assert window.trial_button.isEnabled()
    assert window.specimen.isEnabled()
    assert "Unknown" in window.condition.text()
    assert "Not recorded" in window.evidence.text()
    rows = window.history.rowCount()
    window.poll()
    window.poll()
    assert window.history.rowCount() == rows
    assert window.camera.trial is session
    assert window.run_record == previous_run
    events = [window.history.item(row, 4).text() for row in range(rows)]
    assert events.count("Synchronization fault") == 1
    assert "Camera Fault" not in events
    window.specimen.setText("PCB-RESTARTED")
    window.trial_button.click()
    window.poll()
    assert window.camera.trial is not session
    assert window.camera.trial.result is None
    assert window.run_record.run_id != previous_run.run_id
    assert window.run_record.specimen_id == "PCB-RESTARTED"
    assert window.cycles.text() == "0"
    assert not window.trial_button.isEnabled()
    assert "overdue" not in window.fault.text()


def test_cancelling_recovery_preserves_latched_synchronization_failure(window, camera_stub, monkeypatch):
    session = lose_synchronization(window, monkeypatch)
    run = window.run_record
    monkeypatch.setattr(desktop.ReplaySettings, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    window.trial_button.click()
    window.poll()
    assert window.camera.trial is session
    assert window.run_record == run
    assert "overdue" in window.sync.text()
    assert "Live" in window.freshness.text()


def test_stop_is_available_for_a_degraded_development_run(window, camera_stub, monkeypatch):
    lose_synchronization(window, monkeypatch)
    assert window.lifecycle.snapshot.state == RunState.DEGRADED
    assert window.stop_work_button.isEnabled()
    record = window.run_record
    window.stop_work_button.click()
    window.poll()
    assert window.camera.alive
    assert window.camera.trial is None
    assert window.lifecycle.snapshot.state == RunState.STOPPED
    assert window.lifecycle.snapshot.record == record
    assert window.lifecycle.snapshot.gaps == 1


def test_diagnostics_menu_drives_replay_without_foreground_demo_controls(window, monkeypatch):
    monkeypatch.setattr(
        desktop, "ReplayPipeline", lambda factory, recipe: ReplayPipeline(factory, recipe, realtime=False)
    )
    window._update_diagnostics()
    fixture_action = next(
        action for action in window.diagnostics_menu.actions() if action.text() == "Synthetic fixture"
    )
    start_action = next(action for action in window.diagnostics_menu.actions() if action.text() == "Start replay")
    assert not start_action.isEnabled()
    fixture_action.trigger()
    window._update_diagnostics()
    assert start_action.isEnabled()
    start_action.trigger()
    window.pipeline.join(10)
    window.poll()
    assert window.cycles.text() == "4"
    assert window.fixture_button.isHidden()
    assert window.start_button.isHidden()


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
def test_operator_recovery_native_render(window, application, camera_stub, monkeypatch, tmp_path, size):
    lose_synchronization(window, monkeypatch)
    window.resize(*size)
    window.show()
    application.processEvents()
    assert (window.width(), window.height()) == size
    assert window.trial_button.isVisible()
    assert window.load_recipe_button.isVisible()
    assert window.stop_work_button.isVisible()
    assert window.trial_button.isEnabled()
    assert window.centralWidget().rect().contains(window.fault.geometry())
    assert window.live_view.height() >= 180
    assert window.grab().save(str(tmp_path / f"operator-recovery-{size[0]}x{size[1]}.png"))


@pytest.fixture
def region_dialog(window, application):
    image = QImage(640, 480, QImage.Format.Format_RGB888)
    image.fill(QColor("#505861"))
    dialog = desktop.ReplaySettings(window.recipe, window, camera_image=image, specimen_id="PCB-SAVED-001")
    dialog.recognition_mode.setCurrentIndex(1)
    dialog.show()
    application.processEvents()
    yield dialog
    dialog.close()
    application.processEvents()


def canvas_point(editor, horizontal, vertical):
    target = editor.image_rect()
    return QPoint(round(target.left() + horizontal * target.width()), round(target.top() + vertical * target.height()))


def drag_region(editor, start, end):
    QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=canvas_point(editor, *start))
    QTest.mouseMove(editor, canvas_point(editor, *end))
    QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=canvas_point(editor, *end))


def draw_region_pair(dialog):
    drag_region(dialog.preview, (0.1, 0.1), (0.4, 0.4))
    drag_region(dialog.preview, (0.6, 0.6), (0.9, 0.9))


@pytest.mark.parametrize("size", [(984, 680), (1000, 680)])
def test_actual_mark_setup_captures_and_restores_references(window, application, monkeypatch, tmp_path, size):
    source = cv2.imread(str(Path(__file__).parents[1] / "fixtures" / "pink-marks-reference.png"))
    image = QImage(source.data, 200, 185, source.strides[0], QImage.Format.Format_BGR888).copy()
    dialog = desktop.ReplaySettings(window.recipe, window, camera_image=image, specimen_id="coil-01")
    assert dialog.recognition_mode.currentIndex() == 0
    for inputs, values in zip(
        dialog._regions,
        ((4.5, 6 / 185 * 100, 23, 49 / 185 * 100), (76, 129 / 185 * 100, 22.5, 47 / 185 * 100)),
        strict=True,
    ):
        for spin, value in zip(inputs, values, strict=True):
            spin.setValue(value)
    candidate = dialog._candidate_recipe(require_confirmation=False)
    assert candidate.marker_appearance is not None and candidate.position_appearance is not None
    assert candidate.marker_appearance.score(source, candidate.marker) > 0.99
    assert candidate.position_appearance.score(source, candidate.position) > 0.99
    path = tmp_path / "coil-01.json"
    monkeypatch.setattr(desktop.QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    dialog.store_recipe_button.click()
    assert load_recipe(path).recipe == candidate
    absent = QImage(200, 185, QImage.Format.Format_RGB888)
    absent.fill(QColor("#cccccc"))
    restored = desktop.ReplaySettings(window.recipe, window, camera_image=absent, specimen_id="other")
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    restored.load_recipe_button.click()
    assert restored.specimen_id == "coil-01"
    assert restored.recognition_mode.currentIndex() == 0
    assert restored._candidate_recipe(require_confirmation=False) == candidate
    assert not restored._confirmed.isChecked()
    assert not restored.save_button.isEnabled()
    dialog._confirmed.setChecked(True)
    dialog.resize(*size)
    dialog.show()
    application.processEvents()
    assert dialog.store_recipe_button.isVisible() and dialog.save_button.isEnabled()
    assert dialog.rect().contains(dialog._error.geometry())
    assert dialog.grab().save(str(tmp_path / "actual-mark-setup.png"))
    restored.close()
    dialog.close()


def test_recipe_save_and_load_in_fresh_setup(region_dialog, window, monkeypatch, tmp_path):
    dialog = region_dialog
    assert not dialog.store_recipe_button.isEnabled()
    draw_region_pair(dialog)
    dialog._brightness.setValue(195)
    dialog._stable.setValue(3)
    dialog._timeout.setValue(8)
    expected = dialog._candidate_recipe(require_confirmation=False)
    path = tmp_path / "PCB-recipe.json"
    monkeypatch.setattr(desktop.QFileDialog, "getSaveFileName", lambda *args: (str(path), ""))
    dialog.store_recipe_button.click()
    assert load_recipe(path) == StoredRecipe("PCB-SAVED-001", expected, 640, 480)
    assert "Recipe saved:" in dialog.recipe_file.text()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert window.camera is None and window.run_record is None
    fresh = desktop.ReplaySettings(window.recipe, window, camera_image=dialog.preview._image, specimen_id="OTHER")
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    fresh.load_recipe_button.click()
    assert fresh.specimen_id == "PCB-SAVED-001"
    assert "PCB-SAVED-001" in fresh.recipe_identity.text()
    assert fresh._candidate_recipe(require_confirmation=False) == expected
    assert fresh.preview.regions == (expected.marker, expected.position)
    assert not fresh._confirmed.isChecked()
    assert not fresh.save_button.isEnabled()
    assert "Recipe loaded:" in fresh.recipe_file.text()
    fresh._confirmed.setChecked(True)
    fresh.save_button.click()
    assert fresh.result() == QDialog.DialogCode.Accepted
    assert fresh.recipe == expected
    assert window.run_record is None
    fresh.close()


def test_recipe_picker_cancellation_preserves_settings(region_dialog, monkeypatch):
    dialog = region_dialog
    draw_region_pair(dialog)
    dialog._confirmed.setChecked(True)
    expected = dialog._candidate_recipe(require_confirmation=True)
    monkeypatch.setattr(desktop.QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    dialog.store_recipe_button.click()
    dialog.load_recipe_button.click()
    assert dialog._candidate_recipe(require_confirmation=True) == expected
    assert dialog._recipe_path is None


@pytest.mark.parametrize("failure", ["malformed", "dimensions", "limits", "missing"])
def test_failed_recipe_load_preserves_current_settings(region_dialog, monkeypatch, tmp_path, failure):
    dialog = region_dialog
    draw_region_pair(dialog)
    dialog._confirmed.setChecked(True)
    original = dialog._candidate_recipe(require_confirmation=True)
    path = tmp_path / "recipe.json"
    if failure == "malformed":
        path.write_text("{not-json}")
    elif failure != "missing":
        recipe = original
        if failure == "limits":
            recipe = desktop.replace(recipe, synchronization=desktop.SyncConfig(121, 0.2, 4))
        save_recipe(path, StoredRecipe("OTHER", recipe, 320 if failure == "dimensions" else 640, 480))
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    dialog.load_recipe_button.click()
    assert "Recipe not loaded:" in dialog._error.text()
    assert dialog._candidate_recipe(require_confirmation=True) == original
    assert dialog.specimen_id == "PCB-SAVED-001"
    assert dialog._recipe_path is None


def test_recipe_save_error_is_visible_and_does_not_close_setup(region_dialog, monkeypatch, tmp_path):
    dialog = region_dialog
    draw_region_pair(dialog)
    monkeypatch.setattr(desktop.QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "recipe.json"), ""))

    def fail(*args):
        raise OSError("Disk unavailable")

    monkeypatch.setattr(desktop, "save_recipe", fail)
    dialog.store_recipe_button.click()
    assert "Recipe not saved: Disk unavailable" in dialog._error.text()
    assert dialog._recipe_path is None
    assert dialog.result() != QDialog.DialogCode.Accepted


@pytest.mark.parametrize("accepted", [False, True])
def test_loaded_recipe_starts_only_after_start_and_confirmation(window, camera_stub, monkeypatch, tmp_path, accepted):
    path = tmp_path / "recipe.json"
    recipe = desktop.fixture_recipe()
    save_recipe(path, StoredRecipe("PCB-LOADED-005", recipe, 32, 24))
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))

    def execute(dialog):
        assert "Recipe loaded:" in dialog.recipe_file.text()
        assert not dialog._confirmed.isChecked()
        assert not dialog.save_button.isEnabled()
        assert window.camera.trial is None
        if not accepted:
            return QDialog.DialogCode.Rejected
        dialog._confirmed.setChecked(True)
        dialog._save()
        return dialog.result()

    monkeypatch.setattr(desktop.ReplaySettings, "exec", execute)
    window.connect_camera()
    window.poll()
    window.load_recipe_button.click()
    assert window.camera.trial is None
    assert window.run_record is None
    assert window.specimen.text() == "PCB-LOADED-005"
    window.trial_button.click()
    if accepted:
        assert window.camera.trial.recipe == recipe
        assert window.run_record.specimen_id == "PCB-LOADED-005"
        assert window.specimen.text() == "PCB-LOADED-005"
        assert not window.specimen.isEnabled()
    else:
        assert window.camera.trial is None
        assert window.run_record is None
        assert window.specimen.text() == "PCB-LOADED-005"


@pytest.mark.parametrize("connected", [False, True])
def test_load_recipe_opens_without_specimen_and_does_not_start(window, camera_stub, monkeypatch, tmp_path, connected):
    path = tmp_path / "recipe.json"
    recipe = desktop.fixture_recipe()
    save_recipe(path, StoredRecipe("PCB-LOADED-005", recipe, 32, 24))
    opened = []

    def choose(*args):
        opened.append(True)
        return str(path), ""

    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", choose)
    if connected:
        window.connect_camera()
        window.poll()
    window.specimen.clear()
    window.load_recipe_button.click()
    assert opened == [True]
    assert "Counting only - no detector" in window.run_label.text()
    assert "Start marker:" in window.run_label.toolTip()
    assert "Position marker:" in window.run_label.toolTip()
    assert window._camera_recipe == recipe
    assert window.specimen.text() == "PCB-LOADED-005"
    assert window.camera.trial is None if connected else window.camera is None
    assert window.run_record is None
    assert window.lifecycle.snapshot.state == RunState.STOPPED


def test_cancelling_recipe_picker_preserves_selection(window, monkeypatch, tmp_path):
    path = tmp_path / "recipe.json"
    recipe = desktop.fixture_recipe()
    save_recipe(path, StoredRecipe("PCB-LOADED-005", recipe, 32, 24))
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    window.load_recipe_button.click()
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    window.load_recipe_button.click()
    assert window._camera_recipe == recipe
    assert window.specimen.text() == "PCB-LOADED-005"
    assert window.run_record is None


def test_recipe_picker_defaults_to_central_recipe_folder(window, monkeypatch):
    opened = []

    def choose(parent, title, directory, filters):
        opened.append(directory)
        return "", ""

    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", choose)
    window.load_recipe_button.click()
    assert opened == [str(desktop.RECIPE_DIRECTORY)]
    assert desktop.RECIPE_DIRECTORY.is_dir()


def test_main_recipe_load_error_is_visible_without_camera(window, monkeypatch, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    window.load_recipe_button.click()
    assert "Recipe not loaded:" in window.fault.text()
    assert window._camera_recipe is None
    assert window.run_record is None


def test_loaded_recipe_dimensions_checked_when_camera_starts(window, camera_stub, monkeypatch, tmp_path):
    path = tmp_path / "recipe.json"
    save_recipe(path, StoredRecipe("PCB-LOADED-005", desktop.fixture_recipe(), 640, 480))
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    window.load_recipe_button.click()
    window.connect_camera()
    window.poll()
    window.trial_button.click()
    assert "Recipe image dimensions differ" in window.fault.text()
    assert window.camera.trial is None
    assert window.run_record is None


def test_bound_recipe_preserves_precise_regions_on_confirmation(region_dialog, tmp_path):
    from rdt_monitor.recipes import load_recipe, save_inspection_recipe

    dialog = region_dialog
    recipe = desktop.replace(
        desktop.fixture_recipe(),
        marker=desktop.Region(0.363924, 0.312237, 0.06170899999999999, 0.092827),
        position=desktop.Region(0.5379740000000001, 0.523206, 0.06171, 0.07595),
    )
    model = tmp_path / "model.xml"
    model.write_bytes(b"test")
    model.with_suffix(".bin").write_bytes(b"weights")
    path = tmp_path / "inspection.json"
    save_inspection_recipe(path, StoredRecipe("coil", recipe, 640, 480), model, desktop.Region(0.4, 0.6, 0.1, 0.1), 0.5)
    stored = load_recipe(path)
    assert dialog._apply_stored_recipe(stored, path)
    dialog._confirmed.setChecked(True)
    assert dialog._candidate_recipe(require_confirmation=True) == stored.recipe
    assert "Threshold 0.500" in dialog.recipe_file.toolTip()
    assert not dialog.store_recipe_button.isEnabled()


def test_missing_bundled_model_rejects_recipe_without_replacing_selection(window, monkeypatch, tmp_path):
    from rdt_monitor.recipes import load_recipe, save_inspection_recipe

    model = tmp_path / "model.xml"
    model.write_bytes(b"test")
    model.with_suffix(".bin").write_bytes(b"weights")
    path = tmp_path / "inspection.json"
    save_inspection_recipe(
        path,
        StoredRecipe("coil", desktop.fixture_recipe(), 32, 24),
        model,
        desktop.Region(0.1, 0.1, 0.5, 0.5),
        0.5,
    )
    stored = load_recipe(path)
    stored.detector.settings(path).model_path.with_suffix(".bin").unlink()
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    window.load_recipe_button.click()
    assert "Recipe not loaded:" in window.fault.text()
    assert window._loaded_camera_recipe is None
    assert window.run_record is None


def test_live_cycle_recipe_counts_inspections_and_preserves_defects(window, camera_stub, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from rdt_monitor.live import LiveInspection
    from rdt_monitor.recipes import save_inspection_recipe

    model = tmp_path / "trained.xml"
    model.write_bytes(b"test model")
    model.with_suffix(".bin").write_bytes(b"test weights")
    recipe = desktop.replace(
        desktop.fixture_recipe(),
        synchronization=desktop.SyncConfig(1, 0.2, 4),
        marker=desktop.Region(0, 0, 0.5, 1),
        position=desktop.Region(0.5, 0, 0.5, 1),
    )
    path = tmp_path / "inspection.json"
    save_inspection_recipe(
        path, StoredRecipe("PCB-LIVE", recipe, 32, 24), model, desktop.Region(0.1, 0.1, 0.8, 0.8), 0.5
    )
    scores = iter([0.9, 0.1, 0.51, 0.5, None, 0.2])

    def predict(image):
        score = next(scores)
        if score is None:
            raise RuntimeError("Injected cycle inference failure")
        return SimpleNamespace(
            pred_score=np.array([score], dtype=np.float32),
            anomaly_map=np.full((1, 1, 8, 8), score, dtype=np.float32),
        )

    monkeypatch.setattr(
        desktop,
        "LiveInspection",
        lambda binding, recipe_path, folder, **kwargs: LiveInspection(
            binding, recipe_path, folder, loader=lambda _: SimpleNamespace(predict=predict), **kwargs
        ),
    )
    monkeypatch.setattr(desktop, "INSPECTION_DIRECTORY", tmp_path / "inspection-results")
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    window.load_recipe_button.click()
    assert window.run_record is None
    assert "Unqualified inspection" in window.run_label.text()
    assert window.live_view.inspection_region == load_recipe(path).detector.region
    assert window.roi_view._image.isNull()
    assert "Threshold 0.500" in window.run_label.toolTip()
    window.connect_camera()
    window.poll()
    assert "Unqualified inspection" in window.run_label.text()

    def confirm(dialog):
        dialog._confirmed.setChecked(True)
        dialog._save()
        return dialog.result()

    monkeypatch.setattr(desktop.ReplaySettings, "exec", confirm)
    window.trial_button.click()
    worker = window._live_inspection
    assert worker is not None
    assert window.condition.property("verdict_color") == "grey"
    assert "No inspection yet" in window.condition.text()
    assert window.lifecycle.snapshot.mode == desktop.RunMode.EXPERIMENTAL_INSPECTION
    trial = window.camera.trial
    try:
        expected_colors = iter(["red", "green", "yellow", "yellow", "grey", "green"])
        for frame in range(13):
            pixels = np.full((24, 32, 3), 0 if frame % 2 else 255, dtype=np.uint8)
            trial.observe(CameraFrame(frame, 100 + frame * 0.05, pixels))
            if frame > 0 and frame % 2 == 0:
                for _ in range(500):
                    if not worker.results.empty():
                        break
                    worker.finished.wait(0.01)
                assert not worker.results.empty()
                window.poll()
                assert window.condition.property("verdict_color") == next(expected_colors)
                assert f"Cycle {frame // 2}" in window.condition.text()
                assert f"Cycle {frame // 2} ROI" in window.roi_caption.text()
                if frame == 10:
                    assert window.roi_view._image.isNull()
                    assert "unavailable" in window.roi_caption.text()
                else:
                    assert not window.roi_view._image.isNull()
                    left, top, right, bottom = worker.binding.region.pixels(pixels)
                    assert window.roi_view._image.width() == right - left
                    assert window.roi_view._image.height() == bottom - top
                    pixel = window.roi_view._image.pixelColor(0, 0)
                    assert pixel.red() > pixel.blue()  # Heatmap marks exact cycle pixels, not the live preview.
                assert window._live_defects == 1
                assert "latched" in window.defect_alarm.text()
        window.poll()
        window.stop_work_button.click()
        assert worker.finished.wait(5)
        window.poll()
        snapshot = window.lifecycle.snapshot
        assert snapshot.state == RunState.STOPPED
        assert snapshot.observed == 6 and snapshot.inspected == 5
        assert snapshot.defects == 1 and snapshot.uninspected == 1
        assert snapshot.gaps == 1
        assert "No defect detected" in window.condition.text()
        assert window.condition.property("verdict_color") == "green"
        assert "DEFECT ALARM" in window.defect_alarm.text()
        assert window.assessment_note.text().startswith("Stopped")
        assert window.evidence.text() == "Saved"
        assert window.camera.alive
        assert window.camera.trial is None
        assert not window.roi_view._image.isNull()  # Stop retains the last inspection.
        assert len(list(worker.folder.rglob("result.json"))) == 6
        assert worker.folder.parent == desktop.INSPECTION_DIRECTORY
        assert len(list((worker.folder / "yellow-review").glob("cycle-*"))) == 2
        assert len(list((worker.folder / "grey-uninspected").glob("cycle-*"))) == 1
        assert window._loaded_camera_recipe[0].detector is not None
        position_rows = [
            window.history.item(row, 5).text()
            for row in range(window.history.rowCount())
            if window.history.item(row, 4).text() == "Inspection position"
        ]
        assert position_rows
        assert all("Awaiting cycle inference" in state for state in position_rows)
        assert not any("detector unavailable" in state for state in position_rows)
        window.trial_button.click()
        restarted = window._live_inspection
        assert restarted is not worker
        assert window.condition.property("verdict_color") == "grey"
        assert window._live_defects == 0
        assert window.roi_view._image.isNull()
        assert "No latched defect" in window.defect_alarm.text()
        window.stop_work_button.click()
        assert restarted.finished.wait(5)
        window.poll()
    finally:
        worker.stop(cancel=True)
        assert worker.finished.wait(5)


def test_training_export_can_be_saved_as_a_model_bound_recipe(candidate_dialog, monkeypatch, tmp_path):
    dialog = candidate_dialog
    from rdt_monitor.recipes import load_recipe

    finish_candidate_review(dialog)
    model = tmp_path / "trained.xml"
    model.write_bytes(b"model")
    model.with_suffix(".bin").write_bytes(b"weights")
    dialog._export = model
    dialog.live_review_margin.setValue(0.12)
    settings = dialog._document.settings
    source = tmp_path / "sync.json"
    save_recipe(source, StoredRecipe("specimen", desktop.fixture_recipe(), settings.width, settings.height))
    target = tmp_path / "inspection.json"
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    monkeypatch.setattr(desktop.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    dialog._refresh()
    assert dialog.save_inspection_recipe_button.isEnabled()
    dialog.save_inspection_recipe_button.click()
    stored = load_recipe(target)
    assert stored.detector.region == settings.region
    assert stored.detector.threshold == 0.5
    assert stored.detector.review_margin == 0.12
    assert "Inspection recipe saved" in dialog.fault.text()


def test_invalid_review_band_is_reported_without_saving(candidate_dialog, monkeypatch, tmp_path):
    dialog = candidate_dialog
    finish_candidate_review(dialog)
    model = tmp_path / "model.xml"
    model.write_bytes(b"model")
    model.with_suffix(".bin").write_bytes(b"weights")
    dialog._export = model
    dialog.live_threshold.setValue(0.02)
    dialog.live_review_margin.setValue(0.05)
    settings = dialog._document.settings
    source, target = tmp_path / "sync.json", tmp_path / "invalid.json"
    save_recipe(source, StoredRecipe("specimen", desktop.fixture_recipe(), settings.width, settings.height))
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(source), ""))
    monkeypatch.setattr(desktop.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    dialog._refresh()
    dialog.save_inspection_recipe_button.click()
    assert "Inspection recipe not saved: Review margin" in dialog.fault.text()
    assert not target.exists()


def test_latest_verdict_does_not_flicker_or_regress_and_alarm_survives_evidence_failure(window, tmp_path):
    from types import SimpleNamespace

    from rdt_monitor.live import CycleDetection
    from rdt_monitor.recipes import save_inspection_recipe

    model = tmp_path / "model.xml"
    model.write_bytes(b"model")
    model.with_suffix(".bin").write_bytes(b"weights")
    path = tmp_path / "recipe.json"
    save_inspection_recipe(
        path, StoredRecipe("coil", window.recipe, 32, 24), model, desktop.Region(0.1, 0.1, 0.5, 0.5), 0.5
    )
    binding = load_recipe(path).detector
    record = desktop.RunRecord("test-run", "coil", "test-recipe", "camera", desktop.datetime.now(desktop.UTC))
    window.run_record = record
    window.lifecycle.start(record, mode=desktop.RunMode.EXPERIMENTAL_INSPECTION, readiness=desktop.Readiness())
    worker = SimpleNamespace(binding=binding, results=Queue(maxsize=10), failure="")
    window._live_inspection = worker
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    evidence = tmp_path / "result.json"
    evidence.write_text("{}")
    events = [
        desktop.SyncResult(number, float(number), desktop.SyncState.TRACKING, number, True, True, "Cycle complete")
        for number in range(1, 5)
    ]

    def result(event, score, reason=""):
        detection = desktop.DetectionResult(binding.settings(path), score, np.zeros((12, 16), dtype=np.float32), image)
        return CycleDetection(event, detection, evidence, reason)

    try:
        for event in events[:3]:
            window._event(event)
        # A queue-overload failure can overtake an older in-flight model result.
        worker.results.put_nowait(result(events[2], 0.1))
        worker.results.put_nowait(result(events[0], 0.9))
        worker.results.put_nowait(result(events[1], 0.51))
        window._poll_live_inspection()
        assert window.condition.property("verdict_color") == "green"
        assert "Cycle 3" in window.condition.text()
        assert "Cycle 3 ROI" in window.roi_caption.text()
        assert window._live_defects == 1  # Yellow above the central threshold does not latch.
        before = window.condition.text()
        window._event(events[3])
        window._poll_live_inspection()
        assert window.condition.text() == before
        assert "Cycle 3 ROI" in window.roi_caption.text()
        assert window.condition.property("verdict_color") == "green"
        worker.results.put_nowait(result(events[3], 0.9, "Evidence incomplete: injected disk failure"))
        window._poll_live_inspection()
        assert window.condition.property("verdict_color") == "grey"
        assert "Cycle 4" in window.condition.text()
        assert window._live_defects == 2
        assert window.lifecycle.snapshot.defects == 2
        assert window.coverage_warning.text() == "1 cycle(s) not inspected"
        assert not window.coverage_warning.isHidden()
        assert window.operator_status.text() == "Needs attention"
        assert not window.details_panel.isVisible()
        assert window.evidence.text() == "Failure record saved"
        assert "disk failure" in window.fault.text()
    finally:
        window._live_inspection = None


def test_existing_trained_model_can_be_selected_after_loading_collection(candidate_dialog, monkeypatch):
    dialog = candidate_dialog
    finish_candidate_review(dialog)
    model = dialog._manifest.parent / "training-contract" / "model.xml"
    model.parent.mkdir()
    model.write_bytes(b"model")
    model.with_suffix(".bin").write_bytes(b"weights")
    monkeypatch.setattr(desktop.QFileDialog, "getOpenFileName", lambda *args: (str(model), ""))
    dialog.load_export_button.click()
    assert dialog._export == model.resolve()
    assert dialog.save_inspection_recipe_button.isEnabled()
    assert dialog._process.state() == desktop.QProcess.ProcessState.NotRunning


def test_stop_work_keeps_camera_preview_connected(window, camera_stub, monkeypatch):
    window.connect_camera()
    window.poll()
    approve_trial(monkeypatch)
    window.trial_button.click()
    recipe = window.camera.trial.recipe
    assert window.stop_work_button.isEnabled()

    window.stop_work_button.click()
    assert window.camera.trial is None
    assert window.camera.alive
    assert window.camera.status.state == "Streaming"
    assert window.stop_work_button.isEnabled() is False
    assert "Stopped" in window.sync.text()
    assert "Stopped" in window.run_label.text()

    window.poll()
    assert window.trial_button.isEnabled()
    assert window.load_recipe_button.isEnabled()
    assert "Live" in window.freshness.text()

    reopened_recipes = []
    monkeypatch.setattr(
        desktop.ReplaySettings,
        "exec",
        lambda dialog: reopened_recipes.append(dialog.recipe) or QDialog.DialogCode.Rejected,
    )
    window.trial_button.click()
    assert reopened_recipes == [recipe]
    assert window.camera.trial is None


def test_draw_regions_auto_advance_and_save(region_dialog):
    dialog = region_dialog
    editor = dialog.preview
    frozen_image = editor._image.cacheKey()
    assert editor.active_role == 0
    assert not dialog._coordinate_panel.isVisible()
    assert not dialog.save_button.isEnabled()
    drag_region(editor, (0.1, 0.2), (0.4, 0.5))
    assert editor.active_role == 1
    assert editor.regions[0] is not None and editor.regions[1] is None
    assert dialog._regions[0][0].value() == pytest.approx(10, abs=0.3)
    assert dialog._regions[0][2].value() == pytest.approx(30, abs=0.3)
    assert not dialog.save_button.isEnabled()
    drag_region(editor, (0.9, 0.9), (0.6, 0.6))
    assert editor.regions[1].width == pytest.approx(0.3, abs=0.004)
    assert editor._image.cacheKey() == frozen_image
    assert dialog._error.text() == ""
    QTest.mouseClick(dialog._confirmed, Qt.MouseButton.LeftButton, pos=QPoint(8, dialog._confirmed.height() // 2))
    assert dialog.save_button.isEnabled()
    QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.recipe.marker == editor.regions[0]
    assert dialog.recipe.position == editor.regions[1]


def test_drag_body_clamps_without_changing_size(region_dialog):
    dialog = region_dialog
    draw_region_pair(dialog)
    dialog.role_buttons[0].click()
    original = dialog.preview.regions[0]
    position = dialog.preview.regions[1]
    drag_region(dialog.preview, (0.25, 0.25), (-0.5, 1.5))
    moved = dialog.preview.regions[0]
    assert moved.left == 0
    assert moved.top == pytest.approx(1 - original.height, abs=0.000002)
    assert moved.width == original.width and moved.height == original.height
    assert dialog.preview.regions[1] == position


@pytest.mark.parametrize("handle", ["nw", "n", "ne", "e", "se", "s", "sw", "w"])
def test_all_resize_handles_preserve_opposite_edges(region_dialog, handle):
    dialog = region_dialog
    drag_region(dialog.preview, (0.2, 0.2), (0.7, 0.7))
    dialog.role_buttons[0].click()
    original = dialog.preview.regions[0]
    start = (
        0.2 if "w" in handle else 0.7 if "e" in handle else 0.45,
        0.2 if "n" in handle else 0.7 if "s" in handle else 0.45,
    )
    end = (
        start[0] + (-0.08 if "w" in handle else 0.08 if "e" in handle else 0),
        start[1] + (-0.06 if "n" in handle else 0.06 if "s" in handle else 0),
    )
    drag_region(dialog.preview, start, end)
    resized = dialog.preview.regions[0]
    if "w" in handle:
        assert resized.left == pytest.approx(0.12, abs=0.004)
        assert resized.left + resized.width == pytest.approx(original.left + original.width, abs=0.000002)
    elif "e" in handle:
        assert resized.left == original.left
        assert resized.left + resized.width == pytest.approx(0.78, abs=0.004)
    else:
        assert resized.left == original.left and resized.width == original.width
    if "n" in handle:
        assert resized.top == pytest.approx(0.14, abs=0.004)
        assert resized.top + resized.height == pytest.approx(original.top + original.height, abs=0.000002)
    elif "s" in handle:
        assert resized.top == original.top
        assert resized.top + resized.height == pytest.approx(0.76, abs=0.004)
    else:
        assert resized.top == original.top and resized.height == original.height


def test_letterbox_clicks_do_not_create_regions(region_dialog, application):
    editor = region_dialog.preview
    editor.setFixedSize(600, 300)
    application.processEvents()
    assert editor.image_rect().left() == 100
    QTest.mousePress(editor, Qt.MouseButton.LeftButton, pos=QPoint(10, 50))
    QTest.mouseMove(editor, QPoint(80, 120))
    QTest.mouseRelease(editor, Qt.MouseButton.LeftButton, pos=QPoint(80, 120))
    assert editor.regions == (None, None)
    drag_region(editor, (0.8, 0.8), (1.5, 1.5))
    region = editor.regions[0]
    assert region.left + region.width == pytest.approx(1)
    assert region.top + region.height == pytest.approx(1)


def test_overlap_priority_and_clear_selected_only(region_dialog):
    dialog = region_dialog
    drag_region(dialog.preview, (0.1, 0.1), (0.6, 0.6))
    drag_region(dialog.preview, (0.3, 0.3), (0.8, 0.8))
    assert dialog._error.text() == ""
    position = dialog.preview.regions[1]
    dialog.role_buttons[0].click()
    drag_region(dialog.preview, (0.45, 0.45), (0.5, 0.45))
    assert dialog.preview.regions[0].left == pytest.approx(0.15, abs=0.004)
    assert dialog.preview.regions[1] == position
    start = dialog.preview.regions[0]
    dialog.role_buttons[1].click()
    drag_region(dialog.preview, (0.45, 0.45), (0.45, 0.5))
    assert dialog.preview.regions[0] == start
    assert dialog.preview.regions[1].top == pytest.approx(0.35, abs=0.004)
    dialog.clear_region_button.click()
    assert dialog.preview.regions == (start, None)
    assert all(spin.value() == 0 for spin in dialog._regions[1])
    assert not dialog.save_button.isEnabled()
    drag_region(dialog.preview, (0.7, 0.6), (0.9, 0.9))
    assert dialog.preview.regions[0] == start
    assert dialog.preview.regions[1] is not None


def test_coordinate_sync_and_edits_require_fresh_confirmation(region_dialog):
    dialog = region_dialog
    draw_region_pair(dialog)
    dialog.advanced_button.click()
    assert dialog._coordinate_panel.isVisible()
    dialog._confirmed.setChecked(True)
    assert dialog.save_button.isEnabled()
    dialog._regions[0][0].setValue(15)
    assert dialog.preview.regions[0].left == 0.15
    assert not dialog._confirmed.isChecked() and not dialog.save_button.isEnabled()
    dialog._confirmed.setChecked(True)
    dialog._brightness.setValue(dialog._brightness.value() + 1)
    assert not dialog._confirmed.isChecked()
    dialog._regions[0][2].setValue(0)
    assert "Start region is not set" in dialog._error.text()
    assert dialog.preview.regions[1] is not None
    dialog._regions[0][2].setValue(30)
    assert dialog._error.text() == ""
    dialog._confirmed.setChecked(True)
    dialog.preview.select_region(0)
    drag_region(dialog.preview, (0.25, 0.25), (0.3, 0.25))
    assert not dialog._confirmed.isChecked()
    assert dialog._regions[0][0].value() == pytest.approx(20, abs=0.3)


def test_graphical_regions_survive_dialog_resize(region_dialog, application):
    dialog = region_dialog
    draw_region_pair(dialog)
    regions = dialog.preview.regions
    image_rect = dialog.preview.image_rect()
    dialog.resize(900, 620)
    application.processEvents()
    assert dialog.preview.regions == regions
    assert dialog.preview.image_rect() != image_rect


def test_click_existing_region_selects_it_without_drawing(region_dialog):
    dialog = region_dialog
    drag_region(dialog.preview, (0.1, 0.1), (0.4, 0.4))
    assert dialog.preview.active_role == 1
    QTest.mouseClick(dialog.preview, Qt.MouseButton.LeftButton, pos=canvas_point(dialog.preview, 0.25, 0.25))
    assert dialog.preview.active_role == 0
    assert dialog.preview.regions[1] is None


def test_selection_without_geometry_changes_preserves_confirmation(region_dialog):
    dialog = region_dialog
    draw_region_pair(dialog)
    dialog._confirmed.setChecked(True)
    dialog.role_buttons[0].click()
    QTest.mouseClick(dialog.preview, Qt.MouseButton.LeftButton, pos=canvas_point(dialog.preview, 0.25, 0.25))
    assert dialog._confirmed.isChecked()
    assert dialog.save_button.isEnabled()


def test_tiny_accidental_drag_preserves_regions_and_selection(region_dialog):
    dialog = region_dialog
    draw_region_pair(dialog)
    dialog.role_buttons[0].click()
    regions = dialog.preview.regions
    dialog._confirmed.setChecked(True)
    drag_region(dialog.preview, (0.45, 0.45), (0.451, 0.451))
    assert dialog.preview.regions == regions
    assert dialog.preview.active_role == 0
    assert dialog._confirmed.isChecked()


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
def test_graphical_editor_native_render(region_dialog, application, tmp_path, size):
    dialog = region_dialog
    dialog.preview._display_image(desktop.SyntheticSource().read().image)
    dialog.resize(min(1000, size[0] - 40), min(680, size[1] - 60))
    application.processEvents()
    drag_region(dialog.preview, (0.05, 0.1), (0.15, 0.2))
    drag_region(dialog.preview, (0.8, 0.1), (0.9, 0.2))
    assert dialog.preview.active_role == 1
    assert dialog.rect().contains(dialog.preview.geometry())
    assert dialog.preview.width() >= 400
    assert dialog.preview.height() >= 260
    assert dialog.grab().save(str(tmp_path / f"region-editor-{size[0]}x{size[1]}.png"))
    dialog.advanced_button.click()
    dialog._confirmed.setChecked(True)
    application.processEvents()
    assert dialog.height() < size[1] - 30
    assert dialog._coordinate_panel.parentWidget().rect().contains(dialog._coordinate_panel.geometry())
    titles = [
        label
        for label in dialog._coordinate_panel.findChildren(QLabel)
        if label.text() in {"Start marker", "Position marker"}
    ]
    assert len(titles) == 2
    for title in titles:
        assert title.isVisible()
        assert title.width() >= title.fontMetrics().horizontalAdvance(title.text())
    assert dialog.rect().contains(dialog._confirmed.geometry())
    assert dialog.save_button.isEnabled()
    assert dialog.grab().save(str(tmp_path / f"region-editor-advanced-{size[0]}x{size[1]}.png"))


def test_live_settings_require_regions_and_confirmation(window, camera_stub):
    window.connect_camera()
    window.poll()
    dialog = desktop.ReplaySettings(window.recipe, window, camera_image=window.live_view._image)
    dialog.recognition_mode.setCurrentIndex(1)
    assert all(spin.value() == 0 for inputs in dialog._regions for spin in inputs)
    original = dialog.recipe
    dialog._save()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.recipe == original
    for inputs, values in zip(dialog._regions, ((0, 0, 50, 100), (50, 0, 50, 100)), strict=True):
        for spin, value in zip(inputs, values, strict=True):
            spin.setValue(value)
    dialog._save()
    assert "Confirm regions and timing" in dialog._error.text()
    assert dialog.preview._recipe is not None
    dialog._confirmed.setChecked(True)
    dialog._save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.recipe.marker != dialog.recipe.position
    dialog.close()


def test_trial_settings_reject_shared_unqualified_region(window, camera_stub):
    window.connect_camera()
    window.poll()
    dialog = desktop.ReplaySettings(window.recipe, window, camera_image=window.live_view._image)
    for inputs in dialog._regions:
        inputs[2].setValue(50)
        inputs[3].setValue(100)
    dialog._confirmed.setChecked(True)
    dialog._save()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "distinct" in dialog._error.text()
    dialog.close()


def test_trial_cancel_leaves_plain_preview(window, camera_stub, monkeypatch):
    window.connect_camera()
    window.poll()
    monkeypatch.setattr(desktop.ReplaySettings, "exec", lambda dialog: QDialog.DialogCode.Rejected)
    window.trial_button.click()
    window.poll()
    assert window.camera.trial is None
    assert window.run_record is None
    assert window.live_view._recipe is None
    assert window.trial_button.isEnabled()


def test_trial_counts_observations_without_claiming_defect_inspection(window, camera_stub, monkeypatch):
    window.connect_camera()
    window.poll()
    assert window.trial_button.isEnabled()
    approve_trial(monkeypatch)
    window.trial_button.click()
    assert "locked" in window.run_label.text()
    trial = window.camera.trial
    for index in range(3):
        pixels = np.full((24, 32, 3), 0 if index == 1 else 255, dtype=np.uint8)
        frame = CameraFrame(index + 1, 10 + index / 30, pixels)
        trial.observe(frame)
    window.camera.frames.put_nowait(frame)
    window.poll()
    assert window.cycles.text() == "1"
    assert any(window.history.item(row, 4).text() == "Cycle complete" for row in range(window.history.rowCount()))
    assert len(window.inspections) == 1
    assert "Unknown" in window.inspections[0].reason
    assert "Unknown" in window.condition.text()
    assert "Not recorded" in window.evidence.text()
    assert window.live_view._recipe == trial.recipe
    assert not window.trial_button.isEnabled()
    assert "Counting only - not inspected" in window.assessment_note.text()
    assert window.condition.property("verdict_color") == "grey"
    window.disconnect_camera()
    window.poll()
    assert "coverage ended" in window.sync.text()
    assert not window.trial_button.isEnabled()
    window.connect_camera()
    window.poll()
    assert window.camera.trial is None
    assert window.run_record is None
    assert window.cycles.text() == "0"


@pytest.mark.parametrize("size", [(1024, 768), (1440, 900)])
def test_marker_trial_native_render(window, application, camera_stub, monkeypatch, tmp_path, size):
    window.connect_camera()
    window.poll()
    approve_trial(monkeypatch)
    window.trial_button.click()
    pixels = np.zeros((24, 32, 3), dtype=np.uint8)
    window.camera.frames.put_nowait(CameraFrame(1, monotonic(), pixels))
    window.poll()
    window.resize(*size)
    window.show()
    application.processEvents()
    assert (window.width(), window.height()) == size
    assert window.trial_button.parentWidget().rect().contains(window.trial_button.geometry())
    assert not window.camera_details.isVisible()
    assert window.grab().save(str(tmp_path / f"marker-trial-{size[0]}x{size[1]}.png"))
    dialog = desktop.ReplaySettings(window.recipe, window, camera_image=window.live_view._image)
    dialog.resize(min(900, size[0] - 40), min(690, size[1] - 60))
    dialog.show()
    application.processEvents()
    assert dialog.height() < size[1]
    option = QStyleOptionButton()
    dialog._confirmed.initStyleOption(option)
    indicator = dialog._confirmed.style().subElementRect(
        QStyle.SubElement.SE_CheckBoxIndicator, option, dialog._confirmed
    )
    checkbox_image = dialog._confirmed.grab().toImage()
    ratio = checkbox_image.devicePixelRatio()
    assert checkbox_image.pixelColor(round(indicator.left() * ratio), round(indicator.center().y() * ratio)) == QColor(
        "#9AA0A6"
    )
    assert dialog.grab().save(str(tmp_path / f"marker-setup-{size[0]}x{size[1]}.png"))
    dialog._confirmed.setChecked(True)
    application.processEvents()
    assert dialog.grab().save(str(tmp_path / f"marker-setup-checked-{size[0]}x{size[1]}.png"))
    dialog.close()
