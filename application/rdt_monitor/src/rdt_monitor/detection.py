# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Offline exported-model inspection; not qualified monitoring or evidence."""

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from math import isfinite
from pathlib import Path
from queue import Queue
from threading import Thread
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from rdt_monitor.replay import Image, Region

FloatMap = NDArray[np.float32]


class ModelPrediction(Protocol):
    """Normalized outputs supplied by an exported Anomalib model."""

    pred_score: FloatMap | None
    anomaly_map: FloatMap | None


class Predictor(Protocol):
    """Minimal existing-inferencer boundary for offline inspection."""

    def predict(self, image: Image) -> ModelPrediction:
        """Predict from one RGB image with preprocessing embedded in the export."""
        ...


@dataclass(frozen=True)
class DetectionSettings:
    """Explicit unqualified inspection inputs, separate from marker regions.

    Args:
        model_path (Path): Trusted Anomalib OpenVINO XML or ONNX export.
        region (Region): Specimen inspection region, not a synchronization mark.
        threshold (float): Explicit decision threshold on a normalized model score.

    Raises:
        ValueError: If the threshold or model extension is invalid.
        FileNotFoundError: If the model or required OpenVINO weights are missing.
    """

    model_path: Path
    region: Region
    threshold: float

    def __post_init__(self) -> None:
        if isinstance(self.threshold, bool) or not isfinite(self.threshold) or not 0 < self.threshold < 1:
            raise ValueError("Choose a finite inspection threshold strictly between zero and one")
        if self.model_path.suffix.lower() not in {".xml", ".onnx"}:
            raise ValueError("Select a trusted Anomalib .xml or .onnx export, not a pickle/checkpoint")
        if not self.model_path.is_file():
            raise FileNotFoundError("Exported detector model does not exist")
        if self.model_path.suffix.lower() == ".xml" and not self.model_path.with_suffix(".bin").is_file():
            raise FileNotFoundError("OpenVINO XML requires its matching .bin weights")


@dataclass(frozen=True)
class DetectionResult:
    """Unqualified offline model output aligned to its exact inspection crop.

    Args:
        settings (DetectionSettings): Settings used for this prediction.
        score (float): Normalized anomaly score, not a defect probability.
        anomaly_map (FloatMap): Owned read-only map in source-ROI pixel dimensions.
        source_image (Image): Owned read-only BGR source snapshot for this result.
    """

    settings: DetectionSettings
    score: float
    anomaly_map: FloatMap
    source_image: Image

    @property
    def anomalous(self) -> bool:
        """Return whether the score reaches the explicit unqualified threshold."""
        return self.score >= self.settings.threshold


@dataclass(frozen=True)
class _RuntimePrediction:
    pred_score: FloatMap
    anomaly_map: FloatMap


class _OpenVINOModel:
    def __init__(self, path: Path) -> None:
        try:
            runtime = import_module("openvino")
        except ImportError as error:
            raise ImportError("OpenVINO unavailable: install the monitor's optional inference dependencies") from error
        core = runtime.Core()
        if path.suffix.lower() == ".xml":
            model = core.read_model(str(path), str(path.with_suffix(".bin")))
        else:
            model = core.read_model(str(path))
        self._model = core.compile_model(model, "CPU")
        if len(self._model.inputs) != 1:
            raise ValueError("Detector export must have one RGB NCHW image input")
        shape = self._model.input(0).partial_shape
        if not shape.rank.is_static or shape.rank.get_length() != 4:
            raise ValueError("Detector export requires an NCHW image input")
        if (shape[0].is_static and shape[0].get_length() != 1) or (shape[1].is_static and shape[1].get_length() != 3):
            raise ValueError("Detector export must accept one three-channel RGB image")
        self._height = shape[2].get_length() if shape[2].is_static else None
        self._width = shape[3].get_length() if shape[3].is_static else None
        outputs = {name: port for port in self._model.outputs for name in port.get_names()}
        if not {"pred_score", "anomaly_map"}.issubset(outputs):
            raise ValueError("Anomalib export must expose named pred_score and anomaly_map outputs")
        self._score_port = outputs["pred_score"]
        self._map_port = outputs["anomaly_map"]

    def predict(self, image: Image) -> ModelPrediction:
        height, width = self._height or image.shape[0], self._width or image.shape[1]
        if image.shape[:2] != (height, width):
            image = cv2.resize(image, (width, height), interpolation=cv2.INTER_LINEAR)
        batch = np.transpose(image.astype(np.float32) / 255.0, (2, 0, 1))[None]
        outputs = self._model([batch])
        return _RuntimePrediction(outputs[self._score_port], outputs[self._map_port])


def load_exported_model(path: Path) -> Predictor:
    """Load an Anomalib CPU export lazily through the optional OpenVINO runtime.

    Args:
        path (Path): Trusted OpenVINO or ONNX model selected by the operator.

    Returns:
        Predictor: CPU runtime adapter using the repository's RGB/NCHW convention.

    Raises:
        ImportError: If the optional OpenVINO environment is unavailable.
        RuntimeError: If the exported model cannot be compiled.
        ValueError: If the export lacks the required input/output contract.
    """
    return _OpenVINOModel(path)


def inspect_image(image: Image, settings: DetectionSettings, predictor: Predictor) -> DetectionResult:
    """Inspect one source crop, rejecting malformed or unnormalized outputs.

    Args:
        image (Image): Exact source BGR uint8 image; never modified.
        settings (DetectionSettings): Explicit frozen model, ROI and threshold.
        predictor (Predictor): Loaded exported-model inferencer.

    Returns:
        DetectionResult: Score and map aligned to the exact source crop.

    Raises:
        ValueError: If the image, score or anomaly map cannot support an assessment.
        RuntimeError: If model inference fails.
    """
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or image.size == 0:
        raise ValueError("Inspection requires a nonempty BGR uint8 source image")
    source = image.copy()
    source.flags.writeable = False
    left, top, right, bottom = settings.region.pixels(source)
    rgb = cv2.cvtColor(source[top:bottom, left:right], cv2.COLOR_BGR2RGB)
    prediction = predictor.predict(rgb)
    if prediction.pred_score is None or prediction.anomaly_map is None:
        raise ValueError("Detector output is incomplete; score and anomaly map are required")
    scores = np.asarray(prediction.pred_score, dtype=np.float32)
    if scores.size != 1 or not np.isfinite(scores).all() or not ((scores >= 0) & (scores <= 1)).all():
        raise ValueError("Detector must return one finite normalized image score")
    heatmap = np.asarray(prediction.anomaly_map, dtype=np.float32)
    while heatmap.ndim > 2 and heatmap.shape[0] == 1:
        heatmap = heatmap[0]
    if (
        heatmap.ndim != 2
        or heatmap.size == 0
        or not np.isfinite(heatmap).all()
        or not ((heatmap >= 0) & (heatmap <= 1)).all()
    ):
        raise ValueError("Detector must return one finite normalized two-dimensional anomaly map")
    aligned = cv2.resize(heatmap, (right - left, bottom - top), interpolation=cv2.INTER_LINEAR).copy()
    aligned.flags.writeable = False
    return DetectionResult(settings, float(scores.reshape(-1)[0]), aligned, source)


@dataclass(frozen=True)
class DetectionFailure:
    """Unavailable offline result, never a normal assessment.

    Args:
        reason (str): Model-loading, inference or output-validation failure.
    """

    reason: str


class InspectionWorker:
    """Single-use bounded offline job independent of Qt and camera capture.

    Args:
        image (Image): Source snapshot copied before background processing starts.
        settings (DetectionSettings): Frozen inspection settings.
        loader (Callable[[Path], Predictor]): CPU exported-model loader.
    """

    def __init__(
        self,
        image: Image,
        settings: DetectionSettings,
        loader: Callable[[Path], Predictor] = load_exported_model,
    ) -> None:
        self.results: Queue[DetectionResult | DetectionFailure] = Queue(maxsize=1)
        self._image = image.copy()
        self._image.flags.writeable = False
        self._settings = settings
        self._loader = loader
        self._thread = Thread(target=self._run, name="rdt-offline-inspection", daemon=True)

    @property
    def alive(self) -> bool:
        """Return whether the job is still loading or running the model."""
        return self._thread.is_alive()

    def start(self) -> None:
        """Start once; another inspection requires a fresh worker."""
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        """Wait outside the UI thread, primarily for deterministic tests.

        Args:
            timeout (float | None): Maximum wait, or no limit.
        """
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            model = self._loader(self._settings.model_path)
            result: DetectionResult | DetectionFailure = inspect_image(self._image, self._settings, model)
        except (ImportError, OSError, RuntimeError, ValueError, TypeError, cv2.error) as error:
            result = DetectionFailure(str(error) or "Detector failed without a result")
        self.results.put_nowait(result)


class ImageImportWorker:
    """Read and decode a single offline source without blocking Qt or camera capture.

    Args:
        path (Path): Explicit image filename selected by the operator.
        expected_sha256 (str | None): Candidate integrity hash, when reviewing collected images.
    """

    max_bytes = 128 * 1024 * 1024

    def __init__(self, path: Path, expected_sha256: str | None = None) -> None:
        self.results: Queue[Image | DetectionFailure] = Queue(maxsize=1)
        self._path = path
        self._expected_sha256 = expected_sha256
        self._thread = Thread(target=self._run, name="rdt-image-import", daemon=True)

    @property
    def alive(self) -> bool:
        """Return whether file loading or decoding remains in progress."""
        return self._thread.is_alive()

    def start(self) -> None:
        """Start the single-use source import."""
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        """Wait outside Qt, primarily for deterministic tests.

        Args:
            timeout (float | None): Maximum wait, or no limit.
        """
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            if self._path.stat().st_size > self.max_bytes:
                raise ValueError("Image file exceeds the 128 MiB import limit")
            with self._path.open("rb") as source:
                encoded = source.read(self.max_bytes + 1)
            if len(encoded) > self.max_bytes:
                raise ValueError("Image file exceeds the 128 MiB import limit")
            if self._expected_sha256 is not None and sha256(encoded).hexdigest() != self._expected_sha256:
                raise ValueError("Candidate image changed; reload and review the collection")
            image = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None or image.size == 0:
                raise ValueError("Selected file is not a readable image")
            image.flags.writeable = False
            result: Image | DetectionFailure = image
        except (OSError, ValueError, cv2.error) as error:
            result = DetectionFailure(str(error) or "Image import failed")
        self.results.put_nowait(result)
