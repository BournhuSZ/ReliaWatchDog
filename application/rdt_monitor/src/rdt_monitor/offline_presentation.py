# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Qt Quick offline inspection adapter, isolated from live runs and evidence."""

from __future__ import annotations

from pathlib import Path
from queue import Empty
from typing import TYPE_CHECKING

import cv2
from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from rdt_monitor.detection import (
    DetectionFailure,
    DetectionResult,
    DetectionSettings,
    ImageImportWorker,
    InspectionWorker,
)
from rdt_monitor.rendering import inspection_overlay
from rdt_monitor.replay import Image, Region

if TYPE_CHECKING:
    from rdt_monitor.dialogs import MonitorDialogs
    from rdt_monitor.presentation import FrameProvider


class OfflineInspectionModel(QObject):
    """Own one bounded offline job without camera, recipe or run dependencies.

    Args:
        provider (FrameProvider): Separate image provider registered as ``offline``.
        parent (QObject | None): Lifetime owner.
    """

    changed = Signal()

    def __init__(self, provider: FrameProvider, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.dialogs: MonitorDialogs | None = None
        self._source: Image | None = None
        self._result: DetectionResult | None = None
        self._region: Region | None = None
        self._model: Path | None = None
        self._image_path = self._image_url = self._error = ""
        self._threshold = 0.5
        self._threshold_valid = True
        self._heatmap = True
        self._generation = self._job_generation = 0
        self._worker: ImageImportWorker | InspectionWorker | None = None
        self._importing = False
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self.poll)

    @Property("QVariantMap", notify=changed)
    def status(self) -> dict[str, object]:
        """Return offline-only inputs, readiness and result labels."""
        result = self._result
        region = self._region
        return dict(
            busy=self._worker is not None,
            imagePath=self._image_path,
            modelPath=str(self._model) if self._model else "",
            imageSource=self._image_url,
            region=[region.left, region.top, region.width, region.height] if region else [],
            threshold=self._threshold,
            heatmap=self._heatmap,
            canInspect=self._worker is None
            and self._source is not None
            and region is not None
            and self._model is not None
            and self._threshold_valid,
            canSave=self._worker is None and result is not None,
            hasImage=self._source is not None,
            error=self._error
            or ("Choose a finite threshold strictly between zero and one" if not self._threshold_valid else ""),
            score=f"{result.score:.4f}" if result else "-",
            tone=("defect" if result.anomalous else "normal") if result else "neutral",
            assessment=("Unqualified / anomaly" if result.anomalous else "Unqualified / normal")
            if result
            else ("Not inspected / background job running" if self._worker else "Not inspected"),
        )

    def _invalidate(self) -> None:
        self._generation += 1
        self._result = None
        self._error = ""

    def _render(self) -> None:
        image = self._composite()
        self._image_url = (
            self.provider.publish("preview", image).replace("image://frames/", "image://offline/")
            if image is not None
            else ""
        )
        if image is None:
            self.provider.clear()
        self.changed.emit()

    def _composite(self) -> Image | None:
        if self._result is not None:
            return inspection_overlay(self._result) if self._heatmap else self._result.source_image.copy()
        return self._source

    @Slot()
    def chooseImage(self) -> None:
        """Choose an offline image; cancellation retains the current inputs/result."""
        if self._worker is None and self.dialogs is not None:
            path = self.dialogs.choose_image()
            if path is not None:
                self.loadImage(str(path))

    @Slot(str)
    def loadImage(self, path: str) -> None:
        """Import one explicitly selected image using the existing bounded worker."""
        if self._worker is not None:
            return
        self._invalidate()
        self._source = None
        self._region = None
        self._image_path = path
        self._importing = True
        self._job_generation = self._generation
        self._worker = ImageImportWorker(Path(path))
        self._start()

    @Slot()
    def chooseModel(self) -> None:
        """Select a trusted exported detector without loading it on the GUI thread."""
        if self._worker is None and self.dialogs is not None:
            path = self.dialogs.choose_model()
            if path is not None:
                self.loadModel(str(path))

    @Slot(str)
    def loadModel(self, path: str) -> None:
        """Validate the existing export file contract and invalidate prior results."""
        if self._worker is not None:
            return
        self._invalidate()
        self._model = None
        try:
            DetectionSettings(Path(path), Region(0, 0, 1, 1), 0.5)
            self._model = Path(path)
        except (OSError, ValueError) as error:
            self._error = str(error)
        self._render()

    @Slot(float, float, float, float)
    def setRegion(self, left: float, top: float, width: float, height: float) -> None:
        """Validate normalized ROI edits; invalid geometry cannot retain a result."""
        if self._worker is not None or self._source is None:
            return
        self._invalidate()
        self._region = None
        try:
            self._region = Region(left, top, width, height)
        except ValueError as error:
            self._error = str(error)
        self._render()

    @Slot()
    def clearRegion(self) -> None:
        """Remove the ROI and assessment without changing live setup."""
        if self._worker is None:
            self._region = None
            self._invalidate()
            self._render()

    @Slot(float)
    def setThreshold(self, value: float) -> None:
        """Accept only the existing finite open-interval threshold contract."""
        if self._worker is not None:
            return
        self._invalidate()
        self._threshold = value
        if not 0 < value < 1:
            self._threshold_valid = False
            self._error = "Choose a finite threshold strictly between zero and one"
        else:
            self._threshold_valid = True
        self._render()

    @Slot(bool)
    def setHeatmap(self, enabled: bool) -> None:
        """Change display only, retaining exact source pixels and result identity."""
        self._heatmap = enabled
        self._render()

    @Slot()
    def inspect(self) -> None:
        """Start one background job; never attach it to a live cycle or evidence store."""
        if not self.status["canInspect"]:
            return
        self._invalidate()
        try:
            settings = DetectionSettings(self._model, self._region, self._threshold)
            self._worker = InspectionWorker(self._source, settings)
            self._importing = False
            self._job_generation = self._generation
            self._start()
        except (OSError, RuntimeError, ValueError) as error:
            self._worker = None
            self._error = str(error)
            self._render()

    def _start(self) -> None:
        try:
            self._worker.start()
            self._timer.start()
        except RuntimeError as error:
            self._worker = None
            self._error = str(error)
        self._render()

    @Slot()
    def poll(self) -> None:
        """Settle only the current generation without waiting for a worker thread."""
        worker = self._worker
        if worker is None:
            return
        try:
            result = worker.results.get_nowait()
        except Empty:
            if worker.alive:
                return
            # The job may publish between the first read and the liveness check.
            try:
                result = worker.results.get_nowait()
            except Empty:
                result = DetectionFailure("Offline worker ended without a result")
        self._worker = None
        self._timer.stop()
        if self._job_generation == self._generation:
            if isinstance(result, DetectionFailure):
                self._error = result.reason[:1000]
            elif self._importing:
                self._source = result
            else:
                self._result = result
        self._render()

    @Slot()
    def chooseSnapshot(self) -> None:
        """Choose a manual PNG snapshot destination, separate from live evidence."""
        if self.status["canSave"] and self.dialogs is not None:
            path = self.dialogs.choose_snapshot()
            if path is not None:
                self.saveSnapshot(str(path))

    @Slot(str)
    def saveSnapshot(self, path: str) -> None:
        """Save the exact displayed source/overlay as an unqualified manual snapshot."""
        if not self.status["canSave"]:
            return
        try:
            if Path(path).suffix.lower() != ".png":
                raise ValueError("Choose a .png filename for the offline snapshot")
            success, encoded = cv2.imencode(".png", self._composite())
            if not success:
                raise ValueError("Could not encode offline snapshot")
            Path(path).write_bytes(encoded.tobytes())
            self._error = "Snapshot saved / not qualified inspection evidence"
        except (OSError, ValueError, cv2.error) as error:
            self._error = f"Snapshot save failed: {error}"
        self.changed.emit()

    @Slot()
    def reset(self) -> None:
        """Discard closed-screen data and invalidate late jobs without blocking Qt."""
        self._invalidate()
        self._source = self._result = self._region = self._model = None
        self._image_path = ""
        self._threshold = 0.5
        self._threshold_valid = True
        self._render()

    def dispose(self) -> None:
        """Stop GUI callbacks; the existing single-use worker may finish as a daemon."""
        self.reset()
        self._timer.stop()
