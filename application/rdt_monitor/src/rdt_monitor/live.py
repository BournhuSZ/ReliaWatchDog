# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Bounded, unqualified cycle-bound inference with per-cycle local evidence."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread

import cv2
import numpy as np

from rdt_monitor.collection import atomic_bytes, local_file
from rdt_monitor.detection import DetectionResult, Predictor, inspect_image, load_exported_model
from rdt_monitor.domain import SyncResult
from rdt_monitor.lifecycle import InspectionOutcome
from rdt_monitor.recipes import RecipeDetector, file_digest
from rdt_monitor.replay import Image


@dataclass(frozen=True)
class CycleDetection:
    """Exact cycle identity and result; failures never imply no defect.

    Args:
        event (SyncResult): Completed stable-return observation.
        result (DetectionResult | None): Valid output, if inference succeeded.
        evidence (Path | None): Committed result or grey failure record, if storage permits.
        reason (str): Failure reason, or empty on success.
    """

    event: SyncResult
    result: DetectionResult | None
    evidence: Path | None
    reason: str = ""


class LiveInspection:
    """One model-owning worker with bounded intake, results and per-run storage.

    Args:
        binding (RecipeDetector): Frozen model hashes, ROI, threshold and review margin.
        recipe_path (Path): Selected recipe location.
        folder (Path): New unique evidence directory for this run.
        loader (Callable[[Path], Predictor]): Model loader; injectable for offline tests.
        capacity (int): Queued frames, excluding the one in inference.
        budget_bytes (int): Per-run evidence ceiling; exhaustion fails visibly.
        output_root (Path | None): Explicit results root; defaults to the recipe directory.
    """

    def __init__(
        self,
        binding: RecipeDetector,
        recipe_path: Path,
        folder: Path,
        loader: Callable[[Path], Predictor] = load_exported_model,
        capacity: int = 4,
        budget_bytes: int = 512 * 1024 * 1024,
        output_root: Path | None = None,
    ) -> None:
        if not 1 <= capacity <= 32 or budget_bytes < 1:
            raise ValueError("Live inspection requires bounded positive capacity and evidence budget")
        self.binding, self.recipe_path, self.folder = binding, recipe_path, folder
        self._loader = loader
        self._jobs: Queue[tuple[Image, SyncResult]] = Queue(maxsize=capacity)
        self._failures: Queue[tuple[SyncResult, str]] = Queue(maxsize=128)
        self.results: Queue[CycleDetection] = Queue(maxsize=capacity * 2 + 2)
        self._stop = Event()
        self._cancel = Event()
        self.finished = Event()
        self.failure = ""
        self._budget = budget_bytes
        self._written = 0
        self._output_root = output_root if output_root is not None else recipe_path.parent
        self._storage_ready = False
        self._thread = Thread(target=self._run, name="rdt-live-inspection", daemon=True)

    def start(self) -> None:
        """Start model loading off the camera and UI threads."""
        self._thread.start()

    def capture(self, image: Image, event: SyncResult) -> None:
        """Accept exact cycle pixels without waiting for inference or disk writes.

        Args:
            image (Image): Stable-return source pixels.
            event (SyncResult): Their exact cycle/frame/time identity.
        """
        if self._stop.is_set() or self.failure:
            self._reject(event, self.failure or "Inspection intake stopped")
            return
        try:
            self._jobs.put_nowait((image.copy(), event))
        except Full:
            self._reject(event, "Inference queue full; cycle uninspected")

    def _reject(self, event: SyncResult, reason: str) -> None:
        try:
            self._failures.put_nowait((event, reason))
        except Full:
            self.failure = "Inspection failure-record backlog overflow; coverage unavailable"
            self._stop.set()
            self._publish(CycleDetection(event, None, None, self.failure))

    def stop(self, *, cancel: bool = False) -> None:
        """Bar intake and drain jobs, recording deadline failures without late UI results.

        Args:
            cancel (bool): Deadline expired; late results cannot be applied to another run.
        """
        self._stop.set()
        if cancel:
            self._cancel.set()

    def _publish(self, result: CycleDetection) -> None:
        try:
            self.results.put_nowait(result)
        except Full:
            self.failure = "Inspection result backlog overflow; coverage unavailable"
            self._stop.set()

    def _cycle_folder(self, outcome: InspectionOutcome, event: SyncResult) -> Path:
        group = {
            InspectionOutcome.NO_DEFECT: "green-normal",
            InspectionOutcome.NEEDS_REVIEW: "yellow-review",
            InspectionOutcome.DEFECT: "red-defect",
            InspectionOutcome.UNINSPECTED: "grey-uninspected",
        }[outcome]
        directory = local_file(self.folder, group)
        directory.mkdir(exist_ok=True)
        return local_file(directory, f"cycle-{event.observed_cycles:08d}")

    def _evidence(
        self,
        image: Image | None,
        event: SyncResult,
        result: DetectionResult | None,
        reason: str,
        *,
        metadata_only: bool = False,
    ) -> Path:
        if not self._storage_ready:
            raise OSError("Inspection results directory unavailable; no evidence saved")
        model_verdict = self.binding.verdict(result.score) if result is not None else InspectionOutcome.UNINSPECTED
        outcome = InspectionOutcome.UNINSPECTED if reason else model_verdict
        roi = self.binding.region.pixels(image) if image is not None else None
        files: dict[str, bytes] = {}
        images: dict[str, Image] = {}
        if not metadata_only and image is not None and roi is not None:
            left, top, right, bottom = roi
            images["source.png"] = image
            images["roi.png"] = image[top:bottom, left:right]
            if result is not None:
                images["map.png"] = np.rint(result.anomaly_map * 255).astype(np.uint8)
        for name, pixels in images.items():
            success, encoded = cv2.imencode(".png", pixels)
            if not success:
                raise ValueError("Could not encode live inspection evidence")
            files[name] = encoded.tobytes()
        stage = local_file(self.folder, f"pending-cycle-{event.observed_cycles:08d}")
        existing_images = {name for name in ("source.png", "roi.png", "map.png") if local_file(stage, name).is_file()}
        saved_images = existing_images | files.keys()
        metadata = {
            "qualified": False,
            "run_id": self.folder.name.removeprefix("run-"),
            "cycle": event.observed_cycles,
            "frame_id": event.frame_id,
            "received_at": event.timestamp,
            "recorded_utc": datetime.now(UTC).isoformat(),
            "recipe": str(self.recipe_path),
            "model_file": self.binding.model_file,
            "model_sha256": self.binding.model_sha256,
            "weights_sha256": self.binding.weights_sha256,
            "threshold": self.binding.threshold,
            "review_margin": self.binding.review_margin,
            "score": result.score if result is not None else None,
            "outcome": outcome.value,
            "model_verdict": model_verdict.value,
            "error": reason,
            "roi": list(roi) if roi is not None else None,
            "region": {name: getattr(self.binding.region, name) for name in ("left", "top", "width", "height")},
            "images": [name for name in ("source.png", "roi.png", "map.png") if name in saved_images],
            "missing_images": sorted({"source.png", "roi.png", "map.png"} - saved_images),
        }
        record = "result.json"
        files[record] = json.dumps(metadata, allow_nan=False).encode("utf-8")
        size = sum(
            len(content) - (local_file(stage, name).stat().st_size if local_file(stage, name).exists() else 0)
            for name, content in files.items()
        )
        if self._written + size > self._budget:
            raise OSError(
                f"Per-run evidence budget exhausted ({self._budget / (1024 * 1024):g} MiB); "
                "stop and archive before restarting"
            )
        stage.mkdir(exist_ok=True)
        for name, content in files.items():
            if self._cancel.is_set() and not reason:
                raise RuntimeError("Inspection finalization deadline exceeded")
            destination = local_file(stage, name)
            previous_size = destination.stat().st_size if destination.exists() else 0
            atomic_bytes(destination, content)
            self._written -= previous_size
            self._written += len(content)
        if self._cancel.is_set() and not reason:
            raise RuntimeError("Inspection finalization deadline exceeded")
        destination = self._cycle_folder(outcome, event)
        stage.rename(destination)
        return local_file(destination, record)

    def _complete(self, image: Image | None, event: SyncResult, result: DetectionResult | None, reason: str) -> None:
        evidence = None
        try:
            evidence = self._evidence(image, event, result, reason)
        except (OSError, ValueError, RuntimeError, cv2.error) as error:
            reason = f"{reason}; Evidence incomplete: {error}" if reason else f"Evidence incomplete: {error}"
            try:
                evidence = self._evidence(image, event, result, reason, metadata_only=True)
            except (OSError, ValueError, RuntimeError, cv2.error) as record_error:
                reason = f"{reason}; Failure record not saved: {record_error}"
        if not self._cancel.is_set():
            self._publish(CycleDetection(event, result, evidence, reason))

    def _run(self) -> None:
        try:
            root = self._output_root
            if (
                root.is_symlink()
                or root.resolve() != root.absolute()
                or self.folder.is_symlink()
                or not self.folder.resolve().is_relative_to(root.resolve())
                or self.folder.resolve() == root.resolve()
            ):
                raise ValueError("Live evidence directory escapes the selected results root")
            root.mkdir(parents=True, exist_ok=True)
            self.folder.mkdir(parents=True, exist_ok=False)
            self._storage_ready = True
        except (OSError, ValueError) as error:
            self.failure = f"Live evidence unavailable: {error}"
        try:
            settings = self.binding.settings(self.recipe_path)
            if (
                file_digest(settings.model_path) != self.binding.model_sha256
                or file_digest(settings.model_path.with_suffix(".bin")) != self.binding.weights_sha256
            ):
                raise ValueError("Recipe detector changed; save and review a new inspection recipe")
            predictor = self._loader(settings.model_path)
        except (OSError, ValueError, RuntimeError, ImportError) as error:
            self.failure = f"{self.failure}; Live detector unavailable: {error}".lstrip("; ")
            predictor = None
        try:
            while True:
                try:
                    event, reason = self._failures.get_nowait()
                except Empty:
                    pass
                else:
                    self._complete(None, event, None, reason)
                    continue
                try:
                    image, event = self._jobs.get(timeout=0.05)
                except Empty:
                    if self._stop.is_set():
                        break
                    continue
                if self._cancel.is_set():
                    self._complete(image, event, None, "Inspection finalization deadline exceeded")
                    continue
                if self.failure or predictor is None:
                    self._complete(image, event, None, self.failure)
                    continue
                result = None
                reason = ""
                try:
                    result = inspect_image(image, settings, predictor)
                    if self._cancel.is_set():
                        reason = "Inspection finalization deadline exceeded"
                except (OSError, ValueError, RuntimeError, cv2.error) as error:
                    reason = str(error)
                self._complete(image, event, result, reason)
        finally:
            self.finished.set()
