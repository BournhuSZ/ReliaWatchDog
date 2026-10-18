from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from rdt_monitor.detection import (
    DetectionFailure,
    DetectionSettings,
    ImageImportWorker,
    InspectionWorker,
    inspect_image,
    load_exported_model,
)
from rdt_monitor.replay import Region


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"test boundary only; not an actual model")
    return DetectionSettings(path, Region(0.25, 0.2, 0.5, 0.6), 0.6)


def test_inspection_uses_rgb_crop_and_aligns_owned_map(settings):
    image = np.full((10, 20, 3), (10, 20, 30), dtype=np.uint8)
    original = image.copy()
    model_map = np.full((1, 1, 3, 5), 0.7, dtype=np.float32)

    class Backend:
        def predict(self, crop):
            assert crop.shape == (6, 10, 3)
            assert np.array_equal(crop[0, 0], [30, 20, 10])
            crop[:] = 0
            return SimpleNamespace(pred_score=np.array([0.8]), anomaly_map=model_map)

    result = inspect_image(image, settings, Backend())
    model_map[:] = 0
    assert np.array_equal(image, original)
    assert result.anomalous
    assert result.score == pytest.approx(0.8)
    assert result.anomaly_map.shape == (6, 10)
    assert np.allclose(result.anomaly_map, 0.7)
    assert not result.anomaly_map.flags.writeable
    assert not result.source_image.flags.writeable
    assert not np.shares_memory(result.source_image, image)


@pytest.mark.parametrize("score", [None, np.nan, np.inf, -0.1, 1.1, [0.1, 0.8]])
def test_invalid_scores_cannot_become_normal(settings, score):
    class Backend:
        def predict(self, image):
            return SimpleNamespace(pred_score=score, anomaly_map=np.zeros((3, 5)))

    with pytest.raises(ValueError):
        inspect_image(np.zeros((10, 20, 3), dtype=np.uint8), settings, Backend())


@pytest.mark.parametrize(
    "heatmap", [None, np.zeros(5), np.zeros((2, 3, 5)), np.full((3, 5), np.nan), np.full((3, 5), 1.1)]
)
def test_invalid_maps_cannot_become_normal(settings, heatmap):
    class Backend:
        def predict(self, image):
            return SimpleNamespace(pred_score=np.array([0.1]), anomaly_map=heatmap)

    with pytest.raises(ValueError):
        inspect_image(np.zeros((10, 20, 3), dtype=np.uint8), settings, Backend())


@pytest.mark.parametrize("threshold", [True, np.nan, np.inf, 0, 1])
def test_invalid_thresholds_rejected(settings, threshold):
    with pytest.raises(ValueError):
        DetectionSettings(settings.model_path, settings.region, threshold)


def test_missing_model_and_unsafe_checkpoint_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        DetectionSettings(tmp_path / "missing.onnx", Region(0, 0, 1, 1), 0.5)
    with pytest.raises(ValueError):
        DetectionSettings(Path("unsafe.ckpt"), Region(0, 0, 1, 1), 0.5)


@pytest.mark.parametrize("shape", [[1, 3, -1, -1], [1, 3, 8, 12]])
def test_real_openvino_runtime_contract_without_trained_defect_claims(tmp_path, shape):
    runtime = pytest.importorskip("openvino")
    operations = runtime.opset13
    parameter = operations.parameter(shape, np.float32, name="image")
    heatmap = operations.reduce_mean(parameter, operations.constant([1], np.int64), keep_dims=False)
    score = operations.reduce_mean(heatmap, operations.constant([1, 2], np.int64), keep_dims=False)
    heatmap.output(0).get_tensor().set_names({"anomaly_map"})
    score.output(0).get_tensor().set_names({"pred_score"})
    model = runtime.Model([score, heatmap], [parameter], "test-runtime-contract-not-a-defect-model")
    path = tmp_path / "contract.xml"
    runtime.save_model(model, path)
    settings = DetectionSettings(path, Region(0, 0, 1, 1), 0.6)
    result = inspect_image(np.full((10, 20, 3), 204, dtype=np.uint8), settings, load_exported_model(path))
    assert result.score == pytest.approx(0.8, abs=0.001)
    assert result.anomaly_map.shape == (10, 20)
    assert result.anomalous


@pytest.mark.parametrize("shape", [[1, 1, 8, 12], [2, 3, 8, 12], [1, 8, 12, 3], [1, 8, 12]])
def test_incompatible_export_image_inputs_rejected(tmp_path, shape):
    runtime = pytest.importorskip("openvino")
    operations = runtime.opset13
    parameter = operations.parameter(shape, np.float32, name="image")
    heatmap = operations.reduce_mean(parameter, operations.constant([0], np.int64), keep_dims=False)
    score = operations.reduce_mean(parameter, operations.constant(list(range(len(shape))), np.int64), keep_dims=False)
    heatmap.output(0).get_tensor().set_names({"anomaly_map"})
    score.output(0).get_tensor().set_names({"pred_score"})
    model = runtime.Model([score, heatmap], [parameter], "incompatible-input-contract")
    path = tmp_path / "invalid.xml"
    runtime.save_model(model, path)
    with pytest.raises(ValueError, match="NCHW|three-channel"):
        load_exported_model(path)


def test_worker_owns_source_and_loads_model_off_caller_thread(settings):
    from threading import current_thread

    caller = current_thread()
    image = np.full((10, 20, 3), 100, dtype=np.uint8)

    class Backend:
        def predict(self, crop):
            assert current_thread() is not caller
            assert np.all(crop == 100)
            return SimpleNamespace(pred_score=np.array([0.2]), anomaly_map=np.zeros((3, 5)))

    def loader(path):
        assert current_thread() is not caller
        assert path == settings.model_path
        return Backend()

    worker = InspectionWorker(image, settings, loader)
    image[:] = 0
    worker.start()
    worker.join(5)
    assert not worker.alive
    result = worker.results.get_nowait()
    assert not result.anomalous
    assert np.all(result.source_image == 100)
    with pytest.raises(RuntimeError):
        worker.start()


def test_model_loading_failure_is_explicit_unavailable(settings):
    def unavailable(path):
        raise ImportError("OpenVINO unavailable")

    worker = InspectionWorker(np.zeros((10, 20, 3), dtype=np.uint8), settings, unavailable)
    worker.start()
    worker.join(5)
    result = worker.results.get_nowait()
    assert isinstance(result, DetectionFailure)
    assert "unavailable" in result.reason


def test_image_import_decodes_off_caller_thread_and_owns_pixels(tmp_path, monkeypatch):
    from threading import current_thread

    import cv2

    caller = current_thread()
    path = tmp_path / "specimen.png"
    assert cv2.imwrite(str(path), np.full((10, 20, 3), 120, dtype=np.uint8))
    decode = cv2.imdecode

    def decode_on_worker(encoded, flags):
        assert current_thread() is not caller
        return decode(encoded, flags)

    monkeypatch.setattr(cv2, "imdecode", decode_on_worker)
    worker = ImageImportWorker(path)
    worker.start()
    worker.join(5)
    result = worker.results.get_nowait()
    assert not worker.alive
    assert np.all(result == 120)
    assert not result.flags.writeable


@pytest.mark.parametrize("kind", ["missing", "unreadable", "oversized"])
def test_image_import_failures_remain_unavailable(tmp_path, monkeypatch, kind):
    path = tmp_path / "source.png"
    if kind != "missing":
        path.write_bytes(b"not a readable image")
    if kind == "oversized":
        monkeypatch.setattr(ImageImportWorker, "max_bytes", 8)
    worker = ImageImportWorker(path)
    worker.start()
    worker.join(5)
    assert isinstance(worker.results.get_nowait(), DetectionFailure)
