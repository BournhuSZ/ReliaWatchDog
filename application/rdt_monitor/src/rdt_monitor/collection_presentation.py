# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""QML candidate collection/review and explicit separate-process training."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from queue import Empty
from time import monotonic
from typing import TYPE_CHECKING
from uuid import uuid4

from PySide6.QtCore import Property, QObject, QProcess, QProcessEnvironment, Signal, Slot

from rdt_monitor.collection import (
    CandidateCollection,
    CollectionDocument,
    CollectionSettings,
    load_document,
    local_file,
    metadata_digest,
)
from rdt_monitor.detection import DetectionFailure, DetectionSettings, ImageImportWorker
from rdt_monitor.domain import SyncState
from rdt_monitor.recipes import RecipeProfile, load_recipe, save_inspection_recipe
from rdt_monitor.replay import Region

if TYPE_CHECKING:
    from rdt_monitor.camera import CameraPreview, MarkerTrial
    from rdt_monitor.dialogs import MonitorDialogs
    from rdt_monitor.presentation import FrameProvider, MonitorViewModel
    from rdt_monitor.replay import Image


class CollectionModel(QObject):
    """Keep collection jobs and training separate from active detector selection.

    Args:
        owner (MonitorViewModel): Shared controller adapter, never a second camera consumer.
        provider (FrameProvider): Separate bounded ``collection`` image provider.
    """

    changed = Signal()
    inspectRequested = Signal()

    def __init__(self, owner: MonitorViewModel, provider: FrameProvider) -> None:
        super().__init__(owner)
        self.owner = owner
        self.provider = provider
        self.dialogs: MonitorDialogs | None = None
        training_python = Path(__file__).resolve().parents[4] / ".venv/Scripts/python.exe"
        self._values = dict(
            folder=str(Path.cwd() / "datasets/rdt-candidates"),
            python=str(training_python if training_python.is_file() else Path(sys.executable)),
            mode="auto",
            target=30,
            threshold=0.5,
            margin=0.05,
            familyName="",
            notes="",
        )
        self._source: Image | None = None
        self._region: Region | None = None
        self._coordinates = [0.0] * 4
        self._collection: CandidateCollection | None = None
        self._document: CollectionDocument | None = None
        self._progress: CollectionDocument | None = None
        self._manifest: Path | None = None
        self._export: Path | None = None
        self._digest = self._image_url = self._error = self._log = ""
        self._state = "Unqualified / no collected candidates"
        self._review_worker: ImageImportWorker | None = None
        self._reviewed: set[int] = set()
        self._index = 0
        self._crop = False
        self._confirmed = False
        self._trial: MarkerTrial | None = None
        self._camera: CameraPreview | None = None
        self._manual_count = 0
        self._last_manual_frame = self._last_published_frame = -1
        self._closing = False
        self._result_path: Path | None = None
        self._status = {}
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.finished.connect(self._training_finished)
        self.process.errorOccurred.connect(self._training_error)
        self.refresh()

    @property
    def capture_active(self) -> bool:
        """Return whether capture/drain still owns a camera callback or writer."""
        return self._collection is not None

    @property
    def training(self) -> bool:
        """Return whether the separate process still owns a training job."""
        return self.process.state() != QProcess.ProcessState.NotRunning

    @Property("QVariantMap", notify=changed)
    def status(self) -> dict[str, object]:
        """Return current workflow gates and bounded presentation values."""
        return self._status.copy()

    def refresh(self) -> None:
        """Recompute command gates from source ownership and persisted review progress."""
        controller = self.owner.controller
        camera, frame = controller.camera, controller.last_frame
        trial = camera.trial if camera else None
        lost = trial is not None and trial.result is not None and trial.result.state == SyncState.LOST
        streaming = camera is not None and camera.alive and camera.status.state == "Streaming"
        fresh = frame is not None and 0 <= monotonic() - frame.received_at <= 1
        setup_open = bool(self.owner.status.get("setupOpen", False))
        busy = self.capture_active or self.training or self._review_worker is not None or self._closing or setup_open
        document = self._progress if self.capture_active else self._document
        count = len(document.records) if document else 0
        complete = self._document is not None and self._document.state in {"Awaiting confirmation", "Confirmed normal"}
        reviewed = complete and len(self._reviewed) == len(self._document.records) and self._review_worker is None
        manual = self._values["mode"] == "manual"
        ready = (
            streaming
            and controller.worker is None
            and controller.stopping_trial is None
            and not controller.disconnect_after_stop
            and (controller.retired_worker is None or controller.retired_worker.finished.is_set())
            and not controller.closing
            and (
                bool(self.owner.specimenId.strip())
                if manual
                else trial is not None and not lost and controller.run_record is not None
            )
        )
        status = dict(
            values=self._values.copy(),
            imageSource=self._image_url,
            region=[] if self._crop and self._document else self._rectangle(),
            coordinates=self._coordinates.copy(),
            state=self._state,
            error=self._error or (self._document.reason if self._document else ""),
            log=self._log,
            saved=count,
            observed=trial.result.observed_cycles if trial is not None and trial.result is not None else 0,
            target=document.settings.target if document else self._values["target"],
            reviewed=len(self._reviewed),
            sample=self._index + 1 if self._document and self._document.records else 0,
            samples=len(self._document.records) if self._document else 0,
            sampleCycle=self._document.records[self._index].cycle if self._document and self._document.records else 0,
            sampleFrame=self._document.records[self._index].frame_id
            if self._document and self._document.records
            else -1,
            crop=self._crop,
            confirmed=self._confirmed,
            captureActive=self.capture_active,
            training=self.training,
            editable=not busy and self._document is None,
            canChoose=not busy,
            canUseFrame=not busy and streaming and fresh,
            canConfigure=not busy
            and not manual
            and streaming
            and (trial is None or lost)
            and controller.worker is None,
            restart=bool(lost),
            canCollect=bool(
                ready and not busy and self._document is None and self._source is not None and self._region is not None
            ),
            canCapture=bool(
                manual
                and not self._closing
                and not controller.closing
                and not setup_open
                and self.capture_active
                and not self._collection.finished.is_set()
                and streaming
                and camera is self._camera
                and fresh
                and frame.frame_id > self._last_manual_frame
                and count == self._manual_count
                and count < document.settings.target
            ),
            canStop=self.capture_active and not self._collection.finished.is_set(),
            canReview=bool(self._document and self._document.records and not busy),
            canConfirm=bool(reviewed and not busy),
            canTrain=bool(reviewed and not busy and self._confirmed and self._values["python"]),
            canExport=self._export is not None and not busy,
            canLoadExport=self._document is not None and not busy,
            manifest=str(self._manifest or ""),
            export=str(self._export or ""),
            identity=f"{document.settings.specimen_id} · {document.settings.run_id} · {document.settings.cycle_mode}"
            if document
            else self.owner.specimenId or "Enter an actual specimen ID",
            sourceStatus="Camera unavailable"
            if not streaming
            else "Synchronization lost / restart required"
            if lost
            else "Fresh camera frame"
            if fresh
            else "Waiting for a fresh frame",
        )
        if status != self._status:
            self._status = status
            self.changed.emit()

    def _rectangle(self) -> list[float]:
        region = self._region
        return [region.left, region.top, region.width, region.height] if region else []

    def _fail(self, reason: str) -> None:
        self._error = reason[:2000]
        self.refresh()

    def _publish(self, image: Image) -> None:
        self._image_url = self.provider.publish("preview", image).replace("image://frames/", "image://collection/")

    @Slot(str, "QVariant")
    def editField(self, name: str, value: object) -> None:
        """Edit idle inputs; capture protocol/ROI are immutable after collection begins."""
        self.refresh()
        if not self._status["canChoose"] or name not in self._values:
            return
        if self._document is not None and name in {"mode", "target", "folder"}:
            return
        self._values[name] = value
        self._confirmed = False
        self.refresh()

    @Slot()
    def chooseFolder(self) -> None:
        """Choose explicit candidate storage without starting collection."""
        if self.status["canChoose"] and self.dialogs:
            path = self.dialogs.choose_collection_folder(str(self._values["folder"]))
            if path:
                self.editField("folder", str(path))

    @Slot()
    def choosePython(self) -> None:
        """Select the trusted separate training interpreter."""
        if self.status["canChoose"] and self.dialogs:
            path = self.dialogs.choose_training_python(str(self._values["python"]))
            if path:
                self.editField("python", str(path))

    @Slot()
    def configureSynchronization(self) -> None:
        """Use the existing explicit camera setup flow for counting-only auto capture."""
        self.refresh()
        if self._status["canConfigure"]:
            self.owner.startInspection()
            self.refresh()

    @Slot()
    def useCameraFrame(self) -> None:
        """Start a new draft from one fresh frame; never auto-label it undamaged."""
        self.refresh()
        if not self._status["canUseFrame"]:
            return
        self._source = self.owner.controller.last_frame.image.copy()
        self._document = self._manifest = self._export = None
        self._region = None
        self._coordinates = [0.0] * 4
        self._reviewed.clear()
        self._confirmed = False
        self._crop = False
        self._error = ""
        self._state = "Unqualified / choose a training ROI"
        self._publish(self._source)
        self.refresh()

    @Slot(float, float, float, float)
    def setRegion(self, left: float, top: float, width: float, height: float) -> None:
        """Set the independent training crop before collection begins."""
        self.refresh()
        if not self._status["editable"]:
            return
        self._region = None
        self._coordinates = [left, top, width, height]
        self._confirmed = False
        try:
            region = Region(left, top, width, height)
            if self._source is not None:
                region.pixels(self._source)
            self._region = region
            self._error = ""
            self._state = "Unqualified / training ROI selected"
        except ValueError as error:
            self._error = str(error)
        self.refresh()

    @Slot()
    def collect(self) -> None:
        """Attach the existing bounded writer only after explicit source/ROI validation."""
        self.refresh()
        if not self._status["canCollect"]:
            self._fail(
                "Select a fresh camera frame and training ROI; auto mode also requires counting-only synchronization."
            )
            return
        controller = self.owner.controller
        camera, run = controller.camera, controller.run_record
        try:
            target = float(self._values["target"])
            if not target.is_integer():
                raise ValueError("Collect 20-30 whole images")
            manual = self._values["mode"] == "manual"
            settings = CollectionSettings(
                self.owner.specimenId.strip() if manual else run.specimen_id,
                uuid4().hex if manual else run.run_id,
                "manual-capture-v1" if manual else run.recipe_version,
                self._region,
                self._source.shape[1],
                self._source.shape[0],
                int(target),
                cycle_mode=self._values["mode"],
            )
            writer = CandidateCollection(Path(str(self._values["folder"])), settings)
            self._progress = writer.document
            writer.start()
            self._collection = writer
            self._camera = camera
            self._trial = None if manual else camera.trial
            self._manual_count = 0
            self._last_manual_frame = -1
            self._reviewed.clear()
            self._confirmed = False
            self._export = None
            self._error = ""
            if self._trial is not None:
                self._trial.set_cycle_capture(self._collection.capture)
            self._state = "Collecting unreviewed candidates / no automatic training"
        except (OSError, ValueError, RuntimeError) as error:
            if self._collection is not None:
                self._collection.interrupt(str(error))
            self._fail(str(error))
        self.refresh()

    @Slot()
    def captureCycle(self) -> None:
        """Declare one manual cycle using a distinct fresh frame and persisted-count barrier."""
        self.refresh()
        if not self._status["canCapture"]:
            self._fail("Wait for a fresh distinct frame and the previous cycle image to finish saving.")
            return
        frame = self.owner.controller.last_frame
        if self._collection.capture_manual(frame.image, frame.frame_id, frame.received_at):
            self._manual_count += 1
            self._last_manual_frame = frame.frame_id
            self._error = ""
        self.refresh()

    @Slot()
    def stopCollection(self) -> None:
        """Interrupt collection explicitly; incomplete candidates cannot train."""
        if self._collection is not None:
            self._collection.interrupt("Collection stopped by user before target")
        self.refresh()

    @Slot()
    def chooseCollection(self) -> None:
        """Choose persisted candidates; cancellation leaves review unchanged."""
        self.refresh()
        if self._status["canChoose"] and self.dialogs:
            path = self.dialogs.choose_collection(str(self._values["folder"]))
            if path:
                self.loadCollection(str(path))

    @Slot(str)
    def loadCollection(self, filename: str) -> None:
        """Load a bounded manifest and require this session's complete image review."""
        self.refresh()
        if not self._status["canChoose"]:
            return
        self._document = self._manifest = self._export = None
        self._source = self._region = None
        self._coordinates = [0.0] * 4
        self._confirmed = False
        self._reviewed.clear()
        self._error = ""
        self.provider.clear()
        self._image_url = ""
        try:
            path = Path(filename)
            digest = metadata_digest(path)
            document = load_document(path, expected_sha256=digest)
            self._document, self._manifest, self._digest = document, path, digest
            self._region = document.settings.region
            self._coordinates = self._rectangle()
            self._values.update(
                mode=document.settings.cycle_mode, target=document.settings.target, folder=str(path.parent.parent)
            )
            self._index = 0
            self._state = f"Unqualified / {document.state}"
            self._error = document.reason
            if document.records:
                self._review_sample()
        except (OSError, ValueError) as error:
            self._state = "Unknown / collection unavailable"
            self._error = str(error)
        self.refresh()

    def _review_sample(self) -> None:
        if self._document is None or self._manifest is None or self._review_worker is not None:
            return
        if not 0 <= self._index < len(self._document.records):
            return
        record = self._document.records[self._index]
        self._confirmed = False
        self._reviewed.discard(self._index)
        self._error = ""
        self._image_url = ""
        self.provider.clear()
        try:
            self._review_worker = ImageImportWorker(
                local_file(self._manifest.parent, record.crop_file if self._crop else record.source_file),
                expected_sha256=record.crop_sha256 if self._crop else record.source_sha256,
            )
            self._review_worker.start()
        except (OSError, ValueError, RuntimeError) as error:
            self._review_worker = None
            self._error = str(error)

    @Slot(int)
    def selectSample(self, number: int) -> None:
        """Review one source/crop without counting an image before decoding succeeds."""
        self.refresh()
        if self._status["canReview"] and 1 <= number <= len(self._document.records):
            self._index = number - 1
            self._review_sample()
            self.refresh()

    @Slot(bool)
    def setCrop(self, crop: bool) -> None:
        """Switch exact source/crop views and invalidate undamaged confirmation."""
        self.refresh()
        if self._status["canReview"] and self._crop != crop:
            self._crop = crop
            self._review_sample()
            self.refresh()

    @Slot(bool)
    def setConfirmed(self, confirmed: bool) -> None:
        """Accept only an explicit declaration after every candidate was reviewed."""
        self.refresh()
        self._confirmed = bool(confirmed and self._status["canConfirm"])
        self.refresh()

    @Slot()
    def train(self) -> None:
        """Request training explicitly through the existing integrity-checking CLI."""
        self.refresh()
        if not self._status["canTrain"]:
            self._fail("Review every candidate, confirm undamaged and select the training Python before Train.")
            return
        self._export = None
        try:
            if metadata_digest(self._manifest) != self._digest:
                raise ValueError("Collection metadata changed; reload and review it again")
            self._result_path = local_file(self._manifest.parent, f"result-{uuid4().hex}.json")
            environment = QProcessEnvironment.systemEnvironment()
            environment.insert(
                "PYTHONPATH", str(Path(__file__).resolve().parents[1]) + os.pathsep + environment.value("PYTHONPATH")
            )
            environment.insert("PYTHONUNBUFFERED", "1")
            self.process.setProcessEnvironment(environment)
            self._log = self._error = ""
            self._state = "Training requested / verifying candidates; no live model change"
            self._launch_training(
                str(self._values["python"]),
                [
                    "-m",
                    "rdt_monitor.training",
                    "--collection",
                    str(self._manifest),
                    "--sha256",
                    self._digest,
                    "--confirm-undamaged",
                    "--result",
                    str(self._result_path),
                ],
            )
        except (OSError, ValueError) as error:
            self._fail(str(error))
        self.refresh()

    def _launch_training(self, program: str, arguments: list[str]) -> None:
        self.process.start(program, arguments)

    @Slot()
    def cancelTraining(self) -> None:
        """Cancel the separate child process without activating an export."""
        if self.training:
            self._state = "Cancelling training / no live model change"
            self.process.kill()
        self.refresh()

    def _read_output(self) -> None:
        output = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._log = (self._log + output[-12000:])[-60000:]
        self.refresh()

    def _training_error(self, error: QProcess.ProcessError) -> None:
        self._state = "Unknown / training unavailable"
        self._confirmed = False
        self._fail(f"Training process unavailable: {self.process.errorString()}")

    def _training_finished(self, code: int, status: QProcess.ExitStatus) -> None:
        self._read_output()
        self._confirmed = False
        self._state = "Unknown / training failed or cancelled; no model activated"
        try:
            if code != 0 or status != QProcess.ExitStatus.NormalExit or self._result_path is None:
                raise ValueError("Training failed or was cancelled; inspect the log")
            with self._result_path.open("rb") as stream:
                raw = json.loads(stream.read(8193))
            if (
                not isinstance(raw, dict)
                or set(raw) != {"model", "qualified"}
                or raw["qualified"] is not False
                or not isinstance(raw["model"], str)
            ):
                raise ValueError("Training result metadata is invalid")
            self._select_export((self._manifest.parent / raw["model"]).resolve())
            self._state = "Unqualified export ready / defect accuracy not validated"
            self._error = ""
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            self._error = str(error)
        if self._manifest is not None:
            try:
                self._digest = metadata_digest(self._manifest)
            except (OSError, ValueError):
                self._digest = ""
        self.refresh()

    def _select_export(self, model: Path) -> None:
        if (
            self._manifest is None
            or self._document is None
            or not model.is_relative_to(self._manifest.parent.resolve())
        ):
            raise ValueError("Select a model from a training job inside this collection")
        DetectionSettings(model, self._document.settings.region, float(self._values["threshold"]))
        if model.suffix.lower() != ".xml":
            raise ValueError("Select an OpenVINO XML export with matching BIN")
        self._export = model

    @Slot()
    def chooseExport(self) -> None:
        """Explicitly select a persisted training export from this collection."""
        self.refresh()
        if self._status["canLoadExport"] and self.dialogs:
            path = self.dialogs.choose_training_export(str(self._manifest.parent))
            if path:
                self.loadExport(str(path))

    @Slot(str)
    def loadExport(self, path: str) -> None:
        """Validate export confinement before making it available for offline inspection."""
        self.refresh()
        if not self._status["canLoadExport"]:
            return
        self._export = None
        try:
            self._select_export(Path(path).resolve())
            self._state = "Unqualified export selected / save a recipe to use it"
            self._error = ""
        except (OSError, ValueError) as error:
            self._error = str(error)
        self.refresh()

    @Slot()
    def inspectExport(self) -> None:
        """Open the independent offline tool with the selected export, never live activation."""
        self.refresh()
        if self._status["canExport"]:
            if self.owner.offline.status["busy"]:
                self._fail("Offline inspection is busy; wait before opening this export.")
                return
            self.owner.offline.loadModel(str(self._export))
            self.inspectRequested.emit()

    @Slot()
    def chooseInspectionRecipe(self) -> None:
        """Choose a synchronization source and new destination for explicit model binding."""
        self.refresh()
        if self._status["canExport"] and self.dialogs:
            source = self.dialogs.choose_recipe()
            if source:
                target = self.dialogs.choose_recipe_revision()
                if target:
                    self.saveInspectionRecipe(str(source), str(target))

    @Slot(str, str)
    def saveInspectionRecipe(self, source: str, target: str) -> None:
        """Bind a new family recipe without loading it into the active monitor."""
        self.refresh()
        if not self._status["canExport"]:
            return
        try:
            stored = load_recipe(Path(source))
            settings = self._document.settings
            if (stored.image_width, stored.image_height) != (settings.width, settings.height):
                raise ValueError("Synchronization recipe dimensions do not match the training collection")
            profile = RecipeProfile(
                stored.profile.family_id if stored.profile else uuid4().hex,
                uuid4().hex,
                str(self._values["familyName"]).strip(),
                str(self._values["notes"]).strip(),
            )
            save_inspection_recipe(
                Path(target),
                replace(stored, specimen_id=settings.specimen_id, profile=profile),
                self._export,
                settings.region,
                float(self._values["threshold"]),
                float(self._values["margin"]),
            )
            self._error = f"Inspection recipe saved: {target}. Stop, Load recipe, then Start to use it."
        except (OSError, ValueError) as error:
            self._error = f"Inspection recipe not saved: {error}"
        self.refresh()

    def poll(self) -> None:
        """Advance bounded writer/review state without dequeuing camera events or blocking."""
        if self._collection is not None:
            controller = self.owner.controller
            camera, run = controller.camera, controller.run_record
            progress = self._collection.try_document()
            if progress is not None:
                self._progress = progress
            automatic = self._progress.settings.cycle_mode == "auto"
            lost = (
                self._trial is not None
                and self._trial.result is not None
                and self._trial.result.state == SyncState.LOST
            )
            if (
                controller.closing
                or camera is not self._camera
                or camera is None
                or camera.status.state != "Streaming"
                or controller.disconnect_after_stop
                or (
                    automatic
                    and (
                        camera.trial is not self._trial
                        or run is None
                        or run.run_id != self._progress.settings.run_id
                        or lost
                    )
                )
            ):
                self._collection.interrupt("Camera/synchronization coverage ended during candidate collection")
            progress = self._progress
            self._state = (
                f"Unqualified / {progress.state} / {len(progress.records)} of {progress.settings.target} saved"
            )
            frame = controller.last_frame
            if frame is not None and frame.frame_id != self._last_published_frame:
                self._publish(frame.image)
                self._last_published_frame = frame.frame_id
            if self._collection.finished.is_set():
                folder = self._collection.folder
                if self._trial is not None:
                    self._trial.set_cycle_capture(None)
                self._trial = self._camera = None
                self._collection = None
                if not self._closing:
                    self.loadCollection(str(folder / "collection.json"))
        worker = self._review_worker
        if worker is not None:
            try:
                image = worker.results.get_nowait()
            except Empty:
                if worker.alive:
                    self.refresh()
                    return
                try:
                    image = worker.results.get_nowait()
                except Empty:
                    image = DetectionFailure("Candidate import ended without a result")
            self._review_worker = None
            if not self._closing:
                if isinstance(image, DetectionFailure):
                    self._error = image.reason[:2000]
                else:
                    self._publish(image)
                    self._reviewed.add(self._index)
        self.refresh()

    def request_close(self) -> None:
        """Bar new work and drain candidate capture using the controller's close budget."""
        self._closing = True
        self.stopCollection()

    def dispose(self) -> None:
        """Stop callbacks/jobs before the engine is destroyed."""
        self.request_close()
        self.cancelTraining()
        self.provider.clear()
