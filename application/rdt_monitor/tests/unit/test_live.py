# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Cycle-bound inference, model binding and local evidence contracts."""

import json
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from rdt_monitor.domain import SyncConfig, SyncResult, SyncState
from rdt_monitor.lifecycle import InspectionOutcome
from rdt_monitor.live import LiveInspection
from rdt_monitor.recipes import StoredRecipe, load_recipe, save_inspection_recipe
from rdt_monitor.replay import Region, ReplayRecipe


@pytest.fixture
def bound_recipe(tmp_path):
    model = tmp_path / "trained.xml"
    model.write_bytes(b"injected export contract")
    model.with_suffix(".bin").write_bytes(b"injected weights")
    path = tmp_path / "inspection.json"
    sync = ReplayRecipe(SyncConfig(1, 1, 10), Region(0, 0, 0.1, 0.1), Region(0.9, 0.9, 0.1, 0.1))
    stored = StoredRecipe("specimen", sync, 32, 24)
    save_inspection_recipe(path, stored, model, Region(0.2, 0.2, 0.5, 0.5), 0.5)
    return path, load_recipe(path)


def cycle(number):
    return SyncResult(number * 2, float(number), SyncState.TRACKING, number, True, True, "Cycle complete")


def predictor(score):
    return SimpleNamespace(
        predict=lambda image: SimpleNamespace(
            pred_score=np.array([score], dtype=np.float32),
            anomaly_map=np.full((1, 1, 8, 8), score, dtype=np.float32),
        )
    )


def test_recipe_bundles_model_and_preserves_exact_roi_and_threshold(bound_recipe):
    path, stored = bound_recipe
    binding = stored.detector
    settings = binding.settings(path)
    assert settings.model_path.parent != path.parent
    assert settings.model_path.read_bytes() == b"injected export contract"
    assert settings.region == Region(0.2, 0.2, 0.5, 0.5)
    assert settings.threshold == 0.5
    assert json.loads(path.read_text())["schema_version"] == 4
    assert binding.review_margin == 0.05


@pytest.mark.parametrize("escape", ["../other.xml", "C:/other.xml", "\\\\host\\share\\other.xml", "/other.xml"])
def test_recipe_rejects_unconfined_model_references(bound_recipe, escape):
    path, _ = bound_recipe
    raw = json.loads(path.read_text())
    raw["detector"]["model_file"] = escape
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="bundled relative"):
        load_recipe(path)


@pytest.mark.parametrize(
    ("score", "group", "outcome"),
    [
        (0.1, "green-normal", InspectionOutcome.NO_DEFECT),
        (0.45, "yellow-review", InspectionOutcome.NEEDS_REVIEW),
        (0.5, "yellow-review", InspectionOutcome.NEEDS_REVIEW),
        (0.55, "yellow-review", InspectionOutcome.NEEDS_REVIEW),
        (0.9, "red-defect", InspectionOutcome.DEFECT),
    ],
)
def test_live_worker_uses_exact_pixels_and_commits_evidence(bound_recipe, score, group, outcome):
    path, stored = bound_recipe
    images = []

    def predict(image):
        images.append(image.copy())
        return predictor(score).predict(image)

    worker = LiveInspection(
        stored.detector, path, path.parent / "run-test", loader=lambda _: SimpleNamespace(predict=predict)
    )
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    image[:, :, 0] = 7
    image[:, :, 2] = 23
    worker.start()
    worker.capture(image, cycle(1))
    image[:] = 255
    worker.stop()
    assert worker.finished.wait(5)
    detection = worker.results.get_nowait()
    assert detection.event.frame_id == 2
    assert detection.result.anomalous == (score >= 0.5)
    assert not detection.reason
    assert detection.evidence.is_file()
    assert detection.evidence == worker.folder / group / "cycle-00000001" / "result.json"
    record = json.loads(detection.evidence.read_text())
    assert record["frame_id"] == 2
    assert record["model_sha256"] == stored.detector.model_sha256
    assert record["qualified"] is False
    assert record["outcome"] == outcome
    assert record["model_verdict"] == outcome
    assert record["review_margin"] == 0.05
    assert record["error"] == ""
    assert record["missing_images"] == []
    assert len(record["images"]) == 3
    original = cv2.imread(str(detection.evidence.parent / record["images"][0]))
    assert np.all(original[:, :, 0] == 7)
    assert np.all(images[0][:, :, 0] == 23)  # Predictor receives RGB ROI, not BGR or a later preview.


def test_inference_overload_is_explicit_and_never_blocks_capture(bound_recipe):
    path, stored = bound_recipe
    entered, release = Event(), Event()

    def predict(image):
        entered.set()
        assert release.wait(5)
        return predictor(0.1).predict(image)

    worker = LiveInspection(
        stored.detector,
        path,
        path.parent / "run-test",
        capacity=1,
        loader=lambda _: SimpleNamespace(predict=predict),
    )
    worker.start()
    image = np.zeros((24, 32, 3), dtype=np.uint8)
    try:
        worker.capture(image, cycle(1))
        assert entered.wait(5)
        worker.capture(image, cycle(2))
        worker.capture(image, cycle(3))
    finally:
        release.set()
        worker.stop()
        assert worker.finished.wait(5)
    assert worker.results.qsize() == 3
    results = [worker.results.get_nowait() for _ in range(3)]
    rejected = next(result for result in results if result.event == cycle(3))
    assert rejected.result is None
    assert "queue full" in rejected.reason
    assert rejected.evidence == worker.folder / "grey-uninspected" / "cycle-00000003" / "result.json"
    record = json.loads(rejected.evidence.read_text())
    assert record["outcome"] == "Uninspected"
    assert record["missing_images"] == ["map.png", "roi.png", "source.png"]
    assert record["score"] is None


@pytest.mark.parametrize("failure", ["hash", "inference", "budget", "invalid-output"])
def test_failures_are_uninspected_not_success_shaped(bound_recipe, failure):
    path, stored = bound_recipe
    binding = stored.detector
    if failure == "hash":
        binding = replace(binding, weights_sha256="0" * 64)

    def predict(image):
        if failure == "inference":
            raise RuntimeError("Injected inference failure")
        return predictor(float("nan") if failure == "invalid-output" else 0.9).predict(image)

    worker = LiveInspection(
        binding,
        path,
        path.parent / "run-test",
        loader=lambda _: SimpleNamespace(predict=predict),
        budget_bytes=1 if failure == "budget" else 1024 * 1024,
    )
    worker.start()
    worker.capture(np.zeros((24, 32, 3), dtype=np.uint8), cycle(1))
    worker.stop()
    assert worker.finished.wait(5)
    detection = worker.results.get_nowait()
    assert detection.reason
    if failure == "budget":
        assert detection.evidence is None
        assert detection.result.anomalous
        assert "budget exhausted" in detection.reason
        assert "Failure record not saved" in detection.reason
    else:
        assert detection.evidence.parent.parent.name == "grey-uninspected"
        record = json.loads(detection.evidence.read_text())
        assert record["outcome"] == "Uninspected"
        assert record["error"]
        assert "map.png" in record["missing_images"]


def test_live_output_cannot_escape_recipe_root(bound_recipe):
    path, stored = bound_recipe
    worker = LiveInspection(stored.detector, path, path.parent.parent / "escape-run", loader=lambda _: predictor(0.1))
    worker.start()
    worker.capture(np.zeros((24, 32, 3), dtype=np.uint8), cycle(1))
    worker.stop()
    assert worker.finished.wait(5)
    assert "escapes" in worker.results.get_nowait().reason
    assert not (path.parent.parent / "escape-run").exists()


def test_cancelled_inflight_inference_never_publishes_a_late_success(bound_recipe):
    path, stored = bound_recipe
    entered, release = Event(), Event()

    def predict(image):
        entered.set()
        assert release.wait(5)
        return predictor(0.9).predict(image)

    worker = LiveInspection(
        stored.detector,
        path,
        path.parent / "run-cancel",
        loader=lambda _: SimpleNamespace(predict=predict),
    )
    worker.start()
    try:
        worker.capture(np.zeros((24, 32, 3), dtype=np.uint8), cycle(1))
        assert entered.wait(5)
        worker.capture(np.zeros((24, 32, 3), dtype=np.uint8), cycle(2))
        worker.stop(cancel=True)
        assert not worker.finished.is_set()
    finally:
        release.set()
        assert worker.finished.wait(5)
    assert worker.results.empty()
    records = list(worker.folder.rglob("result.json"))
    assert len(records) == 2
    for path in records:
        record = json.loads(path.read_text())
        assert record["outcome"] == "Uninspected"
        assert "deadline exceeded" in record["error"]
    assert not (worker.folder / "red-defect").exists()


def test_results_root_can_be_separate_from_recipes(bound_recipe, tmp_path):
    path, stored = bound_recipe
    root = tmp_path / "inspection-results"
    worker = LiveInspection(stored.detector, path, root / "run-test", output_root=root, loader=lambda _: predictor(0.5))
    worker.start()
    worker.capture(np.zeros((24, 32, 3), dtype=np.uint8), cycle(1))
    worker.stop()
    assert worker.finished.wait(5)
    detection = worker.results.get_nowait()
    assert not detection.reason
    assert detection.evidence == root / "run-test" / "yellow-review" / "cycle-00000001" / "result.json"


def test_storage_budget_preserves_grey_record_when_metadata_still_fits(bound_recipe):
    path, stored = bound_recipe
    worker = LiveInspection(
        stored.detector, path, path.parent / "run-budget", loader=lambda _: predictor(0.9), budget_bytes=8192
    )
    worker.start()
    worker.capture(np.random.default_rng(0).integers(0, 256, (256, 256, 3), dtype=np.uint8), cycle(1))
    worker.stop()
    assert worker.finished.wait(5)
    detection = worker.results.get_nowait()
    assert "budget exhausted" in detection.reason
    record = json.loads(detection.evidence.read_text())
    assert detection.evidence.parent.parent.name == "grey-uninspected"
    assert record["outcome"] == "Uninspected"
    assert record["model_verdict"] == "Defect detected"
    assert record["images"] == []
    assert worker._written <= 8192


def test_partial_image_write_failure_is_never_committed_as_red(bound_recipe, monkeypatch):
    import rdt_monitor.live as live

    path, stored = bound_recipe
    write = live.atomic_bytes

    def fail_roi(destination, content):
        if destination.name == "roi.png":
            raise OSError("Injected image write failure")
        write(destination, content)

    monkeypatch.setattr(live, "atomic_bytes", fail_roi)
    worker = LiveInspection(stored.detector, path, path.parent / "run-disk", loader=lambda _: predictor(0.9))
    worker.start()
    worker.capture(np.zeros((24, 32, 3), dtype=np.uint8), cycle(1))
    worker.stop()
    assert worker.finished.wait(5)
    detection = worker.results.get_nowait()
    assert "Injected image write failure" in detection.reason
    record = json.loads(detection.evidence.read_text())
    assert detection.evidence.parent.parent.name == "grey-uninspected"
    assert record["images"] == ["source.png"]
    assert record["missing_images"] == ["map.png", "roi.png"]
    assert not (worker.folder / "red-defect").exists()
    assert not list(worker.folder.glob("pending-*"))


def test_failure_to_write_even_the_result_record_is_explicit(bound_recipe, monkeypatch):
    import rdt_monitor.live as live

    path, stored = bound_recipe
    write = live.atomic_bytes

    def fail_record(destination, content):
        if destination.name == "result.json":
            raise OSError("Injected record write failure")
        write(destination, content)

    monkeypatch.setattr(live, "atomic_bytes", fail_record)
    worker = LiveInspection(stored.detector, path, path.parent / "run-no-record", loader=lambda _: predictor(0.9))
    worker.start()
    worker.capture(np.zeros((24, 32, 3), dtype=np.uint8), cycle(1))
    worker.stop()
    assert worker.finished.wait(5)
    detection = worker.results.get_nowait()
    assert detection.evidence is None
    assert detection.result.score > 0.55
    assert "Failure record not saved" in detection.reason
    assert not list(worker.folder.rglob("result.json"))
    assert not (worker.folder / "red-defect").exists()


def test_result_group_symlink_cannot_write_outside_run(bound_recipe, tmp_path):
    path, stored = bound_recipe
    outside = tmp_path / "outside"
    outside.mkdir()
    run = tmp_path / "run-symlink"

    def load(_):
        try:
            (run / "green-normal").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            # Report lack of symlink privileges on the test thread, not inside the worker.
            unavailable.append(error)
        return predictor(0.1)

    unavailable = []
    worker = LiveInspection(stored.detector, path, run, loader=load)
    worker.start()
    worker.capture(np.zeros((24, 32, 3), dtype=np.uint8), cycle(1))
    worker.stop()
    assert worker.finished.wait(5)
    if unavailable:
        pytest.skip(f"Requires directory symlink privileges: {unavailable[0]}")
    detection = worker.results.get_nowait()
    assert detection.reason
    assert detection.evidence.parent.parent.name == "grey-uninspected"
    assert not list(outside.iterdir())
