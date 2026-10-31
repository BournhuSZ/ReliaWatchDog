# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Bounded Qt Quick presentation adapters for the shared monitor controller."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from getpass import getuser
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    Property,
    QAbstractListModel,
    QModelIndex,
    QObject,
    QSettings,
    QSize,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtQuick import QQuickImageProvider

from rdt_monitor.collection_presentation import CollectionModel
from rdt_monitor.controller import MonitorController, SourcePoll
from rdt_monitor.domain import SyncState
from rdt_monitor.lifecycle import InspectionOutcome, RunMode, RunState
from rdt_monitor.live import LiveInspection
from rdt_monitor.offline_presentation import OfflineInspectionModel
from rdt_monitor.palette import colors
from rdt_monitor.paths import INSPECTION_DIRECTORY
from rdt_monitor.recipe_library import RecipeLibrary
from rdt_monitor.rendering import inspection_overlay
from rdt_monitor.replay import Image, fixture_recipe
from rdt_monitor.replay_settings import parse_settings, settings_values
from rdt_monitor.setup_presentation import SetupModel

if TYPE_CHECKING:
    from rdt_monitor.dialogs import MonitorDialogs


class EventLogModel(QAbstractListModel):
    """Expose at most 500 operator events; never retain a per-preview-frame log."""

    _roles = {
        int(Qt.ItemDataRole.UserRole) + i: name.encode()
        for i, name in enumerate(("timestamp", "runId", "cycle", "frameId", "message", "detail", "severity"), start=1)
    }

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, str | int]] = []

    def roleNames(self) -> dict[int, bytes]:
        """Return stable event roles for QML delegates."""
        return self._roles

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        """Return the number of bounded root rows."""
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> str | int | None:
        """Return one event field without exposing a mutable Python record."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows) or role not in self._roles:
            return None
        return self._rows[index.row()][self._roles[role].decode()]

    def append(
        self,
        message: str,
        detail: str,
        run_id: str = "-",
        cycle: int = 0,
        frame_id: int = -1,
        severity: str = "info",
    ) -> None:
        """Append one operator event on the GUI thread.

        Args:
            message (str): Short event name.
            detail (str): Operator-facing explanation.
            run_id (str): Owning run, or a dash for source-only events.
            cycle (int): Owning observed cycle, if applicable.
            frame_id (int): Owning source frame, or -1 when unavailable.
            severity (str): Presentation severity, independent of the inspection verdict.
        """
        if len(self._rows) >= 500:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            del self._rows[0]
            self.endRemoveRows()
        row = len(self._rows)
        self.beginInsertRows(QModelIndex(), row, row)
        self._rows.append(
            dict(
                timestamp=datetime.now(UTC).strftime("%H:%M:%S UTC"),
                runId=run_id,
                cycle=cycle,
                frameId=frame_id,
                message=message,
                detail=detail,
                severity=severity,
            )
        )
        self.endInsertRows()


class FrameProvider(QQuickImageProvider):
    """Retain one owned image per channel, rejecting obsolete generation URLs."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._lock = Lock()
        self._generation = 0
        self._images: dict[str, tuple[str, QImage]] = {}

    def publish(self, channel: str, pixels: Image) -> str:
        """Copy source pixels into a bounded, explicitly identified Qt image.

        Args:
            channel (str): Preview or inspection ROI channel.
            pixels (Image): Contiguous source BGR image, independent of the display copy.

        Returns:
            str: Exact generation URL; older URLs never alias newer acquisitions.

        Raises:
            ValueError: If a caller requests an unbounded/unknown channel.
        """
        if channel not in {"preview", "roi", "roiRaw"}:
            raise ValueError("Only bounded preview and ROI channels are supported")
        image = QImage(
            pixels.data, pixels.shape[1], pixels.shape[0], pixels.strides[0], QImage.Format.Format_BGR888
        ).copy()
        with self._lock:
            self._generation += 1
            identity = f"{channel}/{self._generation}"
            self._images[channel] = (identity, image)
        return f"image://frames/{identity}"

    def clear(self) -> None:
        """Release retained images on source/run replacement without reusing URL identities."""
        with self._lock:
            self._images.clear()

    def requestImage(self, identity: str, size: QSize, requestedSize: QSize) -> QImage:
        """Return only the exact requested acquisition, or an empty obsolete result."""
        with self._lock:
            stored = self._images.get(identity.partition("/")[0])
            image = QImage(stored[1]) if stored is not None and stored[0] == identity else QImage()
        size.setWidth(image.width())
        size.setHeight(image.height())
        if not image.isNull() and requestedSize.isValid():
            return image.scaled(
                requestedSize, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
        return image


class MonitorViewModel(QObject):
    """Expose confirmed development inspection through the shared controller.

    Args:
        controller (MonitorController | None): Exclusively owned session coordinator.
        start_timer (bool): Poll automatically; disabled by deterministic integration tests.
        dialogs (MonitorDialogs | None): Native setup/picker adapter, attached by the QML bootstrap.
        output_root (Path | None): Existing results location or an explicit test/deployment root.
        worker_factory (Callable | None): Optional live worker factory for deterministic integration.
    """

    statusChanged = Signal()
    imageChanged = Signal()
    lightModeChanged = Signal()
    closeReady = Signal()
    specimenChanged = Signal()
    overlayChanged = Signal()

    def __init__(
        self,
        controller: MonitorController | None = None,
        *,
        start_timer: bool = True,
        dialogs: MonitorDialogs | None = None,
        output_root: Path | None = None,
        worker_factory: Callable[..., LiveInspection] | None = None,
    ) -> None:
        super().__init__()
        self.controller = controller if controller is not None else MonitorController()
        self.provider = FrameProvider()
        self._offline = OfflineInspectionModel(FrameProvider(), self)
        self._setup = SetupModel(FrameProvider(), self)
        self._recipe_library = RecipeLibrary(self)
        self.events = EventLogModel(self)
        self.results = EventLogModel(self)
        self._status: dict[str, object] = {}
        self._preview = self._roi = self._notice = ""
        self._roi_raw = ""
        self._specimen = ""
        try:
            self._session_user = getuser()
        except (OSError, KeyError, ImportError):
            self._session_user = "Unavailable"
        self._dialogs = dialogs
        self._output_root = output_root if output_root is not None else INSPECTION_DIRECTORY
        self._worker_factory = worker_factory
        self._setup_open = False
        self._replay_path = ""
        self._replay_recipe = fixture_recipe()
        self._replay_revision = 1
        self._replay_error = ""
        self._overlay = True
        self._verdict_tone = "neutral"
        self._result_cycle = self._result_frame = 0
        self._score = self._threshold = self._margin = "-"
        self._evidence = "Not recorded"
        self._evidence_detail = ""
        self._verdict = "Not inspected"
        self._last_source_state = ""
        self._close_notified = False
        self._settings = QSettings("Anomalib", "RDT Reliability Monitor")
        self._light = self._settings.value("display_mode", "dark") == "light"
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.poll)
        self._training = CollectionModel(self, FrameProvider())
        self._training.changed.connect(self._refresh)
        self._refresh()
        if start_timer:
            self.timer.start()

    @Property("QVariantMap", notify=statusChanged)
    def status(self) -> dict[str, object]:
        """Return presentation-safe status, validity and command eligibility."""
        return self._status.copy()

    @Property(QObject, constant=True)
    def offline(self) -> OfflineInspectionModel:
        """Expose the independent offline screen without live-run commands."""
        return self._offline

    @Property(str, constant=True)
    def sessionUser(self) -> str:
        """Return the application process's OS user for display, without authenticating."""
        return self._session_user

    @Property(QObject, constant=True)
    def setup(self) -> SetupModel:
        """Expose the frozen-frame setup adapter independently of live frames."""
        return self._setup

    @Property(QObject, constant=True)
    def collection(self) -> CollectionModel:
        """Expose independent candidate collection, review and explicit training."""
        return self._training

    @Property(str, notify=imageChanged)
    def previewSource(self) -> str:
        """Return the current preview generation independently from inspection identity."""
        return self._preview

    @Property(str, notify=imageChanged)
    def roiSource(self) -> str:
        """Return the exact latest completed source ROI, without inventing a heatmap."""
        return self._roi if self._overlay else self._roi_raw

    @Property(str, notify=specimenChanged)
    def specimenId(self) -> str:
        """Return the actual specimen serial independently of a recipe's training specimen."""
        return self._specimen

    @Property(bool, notify=overlayChanged)
    def overlayEnabled(self) -> bool:
        """Return whether the exact completed ROI is annotated with its anomaly map."""
        return self._overlay

    @Property("QVariantMap", notify=lightModeChanged)
    def themeColors(self) -> dict[str, str]:
        """Return the existing GUI's semantic palette for QML tokens."""
        return colors(self._light)

    def attach_dialogs(self, dialogs: MonitorDialogs) -> None:
        """Attach native transient dialogs without creating another controller.

        Args:
            dialogs (MonitorDialogs): Adapter owned by this presentation session.
        """
        if self._dialogs is None:
            self._dialogs = dialogs
        self._dialogs.apply_theme(self._light)
        self._offline.dialogs = dialogs
        self._setup.dialogs = self._dialogs
        self._training.dialogs = self._dialogs

    @Slot(str)
    def setSpecimenId(self, serial: str) -> None:
        """Edit the actual serial only while the current run permits setup changes."""
        if self._status.get("canEditSpecimen") and serial != self._specimen:
            self._specimen = serial[:128]
            self._reset_inspection()
            self.specimenChanged.emit()
            self._refresh()

    @Slot(bool)
    def setOverlayEnabled(self, enabled: bool) -> None:
        """Switch between source and heatmap copies of the same completed ROI."""
        if enabled != self._overlay:
            self._overlay = enabled
            self.overlayChanged.emit()
            self.imageChanged.emit()

    @Property(QObject, constant=True)
    def recipeLibrary(self) -> RecipeLibrary:
        """Return the local recipe catalog."""
        return self._recipe_library

    @Slot()
    def chooseRecipeFolder(self) -> None:
        """Choose a library folder without loading a recipe."""
        if self._dialogs is None or not self._status.get("canLoadRecipe", False):
            return
        path = self._dialogs.choose_recipe_folder(str(self._recipe_library.directory))
        if path is not None:
            self._recipe_library.setDirectory(str(path))

    @Property(QObject, constant=True)
    def eventModel(self) -> EventLogModel:
        """Return the bounded Qt operator-event model."""
        return self.events

    @Property(QObject, constant=True)
    def resultModel(self) -> EventLogModel:
        """Return the bounded session inspection-result model."""
        return self.results

    @Property(bool, notify=lightModeChanged)
    def lightMode(self) -> bool:
        """Return the existing workstation light/dark display preference."""
        return self._light

    @Slot(bool)
    def setLightMode(self, light: bool) -> None:
        """Persist the same display preference used by the Widgets application."""
        if light != self._light:
            self._light = light
            self._settings.setValue("display_mode", "light" if light else "dark")
            if self._dialogs is not None:
                self._dialogs.apply_theme(light)
            self.lightModeChanged.emit()

    def _reset_images(self) -> None:
        self.provider.clear()
        self._preview = ""
        self._reset_inspection()

    def _reset_inspection(self) -> None:
        self._roi = self._roi_raw = ""
        self._verdict = "Not inspected"
        self._verdict_tone = "neutral"
        self._result_cycle = self._result_frame = 0
        self._score = self._threshold = self._margin = "-"
        self._evidence, self._evidence_detail = "Not recorded", ""
        self.imageChanged.emit()

    def _refresh(self) -> None:
        snapshot = self.controller.snapshot()
        camera = snapshot.camera
        pipeline = self.controller.pipeline
        live = (
            snapshot.source_mode == "camera"
            and camera is not None
            and camera.state == "Streaming"
            and camera.last_received_at is not None
            and 0 <= monotonic() - camera.last_received_at <= 1
        )
        source = (
            "Camera preview"
            if snapshot.source_mode == "camera"
            else "Synthetic fixture / not camera footage"
            if pipeline is not None and self.controller.synthetic
            else f"Recording replay / NOT LIVE · {Path(self.controller.source).name}"
            if pipeline is not None
            else "No source"
        )
        selection = self.controller.loaded_recipe
        stored = selection[0] if selection is not None else None
        recipe_name = (
            (stored.profile.name if stored.profile is not None else selection[1].stem) if stored else "No recipe loaded"
        )
        trial = self.controller.camera.trial if self.controller.camera is not None else None
        replay = snapshot.source_mode == "replay" and pipeline is not None
        if replay:
            recipe_name = (
                f"Replay synchronization · {snapshot.run.record.recipe_version if snapshot.run.record else '-'}"
            )
        lost = snapshot.synchronization is not None and snapshot.synchronization.state == SyncState.LOST
        fresh = (
            live
            and self.controller.last_frame is not None
            and 0 <= monotonic() - self.controller.last_frame.received_at <= 1
        )
        regions = []
        marker_recipe = pipeline.recipe if replay else trial.recipe if trial is not None else None
        if marker_recipe is not None:
            for name, region, role in (
                ("Start", marker_recipe.marker, "startMarker"),
                ("Position", marker_recipe.position, "positionMarker"),
            ):
                regions.append(
                    dict(
                        label=name,
                        role=role,
                        left=region.left,
                        top=region.top,
                        width=region.width,
                        height=region.height,
                    )
                )
        binding = (
            self.controller.worker.binding
            if self.controller.worker is not None
            else stored.detector
            if stored
            else None
        )
        if binding is not None and not replay:
            region = binding.region
            regions.append(
                dict(
                    label="Inspection ROI",
                    role="inspectionRoi",
                    left=region.left,
                    top=region.top,
                    width=region.width,
                    height=region.height,
                )
            )
        fault = (
            self._notice
            or snapshot.fault
            or (snapshot.run.reason if snapshot.run.stop_timed_out or snapshot.run.state == RunState.DEGRADED else "")
        )
        health_tone = (
            "review"
            if fault or snapshot.run.state in {RunState.DEGRADED, RunState.RECOVERING}
            else ("accent" if snapshot.run.state in {RunState.READY, RunState.RUNNING} else "neutral")
        )
        status = dict(
            runState=snapshot.run.state.value,
            cameraState=camera.state if camera is not None else "Disconnected",
            cameraLabel=f"Replay · {pipeline.status.state}"
            if replay
            else "Live preview"
            if live
            else "Preview stale"
            if camera is not None and camera.state == "Streaming"
            else camera.state
            if camera is not None
            else "Disconnected",
            cameraTone="accent"
            if live
            else "review"
            if camera is not None and camera.state in {"Fault", "Streaming"}
            else "neutral",
            sourceLabel=source,
            live=bool(live),
            observed=snapshot.run.observed,
            inspected=snapshot.run.inspected,
            passed=snapshot.run.passed,
            failed=snapshot.run.failed,
            reviewed=snapshot.run.reviewed,
            yieldRate=100 * snapshot.run.passed / snapshot.run.inspected if snapshot.run.inspected else None,
            uninspected=snapshot.run.uninspected,
            pending=snapshot.run.pending,
            defects=snapshot.run.defects,
            verdict=self._verdict,
            verdictTone=self._verdict_tone,
            verdictLabel={"normal": "Normal", "review": "Needs review", "defect": "Defect", "neutral": "Not inspected"}[
                self._verdict_tone
            ],
            healthTone=health_tone,
            recipeName=recipe_name,
            recipePart=(
                "Replay source · live recipe inactive"
                if replay
                else (
                    f"Part: {stored.specimen.name or 'Not recorded'} · Batch: {stored.specimen.batch or 'Not recorded'}"
                )
                if stored is not None
                else "Part: No recipe loaded"
            ),
            uutFamily=(stored.specimen.family or "Not recorded")
            if stored is not None and not replay
            else "Not recorded",
            articleNumber=(stored.specimen.article_number or "Not recorded")
            if stored is not None and not replay
            else "Not recorded",
            recipeModel=(
                "Model: None · counting-only replay"
                if replay
                else (
                    f"Model: OpenVINO IR · {stored.detector.model_sha256[:12]} · "
                    f"Threshold {stored.detector.threshold:.3f} ± {stored.detector.review_margin:.3f}"
                    if stored.detector is not None
                    else "Model: None · counting only"
                )
                if stored is not None
                else "Model: No recipe loaded"
            ),
            recipeMetadata=(
                "Replay runs do not activate a live recipe detector."
                if replay
                else (
                    f"Part: {stored.specimen.name or 'Not recorded'}\n"
                    f"Batch: {stored.specimen.batch or 'Not recorded'}\n"
                    f"UUT family: {stored.specimen.family or 'Not recorded'}\n"
                    f"Article number: {stored.specimen.article_number or 'Not recorded'}\n"
                    f"Source dimensions: {stored.image_width} × {stored.image_height}\n"
                    + (
                        f"Model: {stored.detector.model_file}\nSHA-256: {stored.detector.model_sha256}\n"
                        f"Threshold: {stored.detector.threshold} · Review margin: {stored.detector.review_margin}\n"
                        "Unqualified inspection"
                        if stored.detector is not None
                        else "No detector · counting only"
                    )
                )
                if stored is not None
                else "Load a recipe to display its saved part and model information."
            ),
            recipeMode=stored.mode_label if stored is not None and not replay else "Counting only / no detector",
            runId=snapshot.run.record.run_id if snapshot.run.record is not None else "-",
            runRecipe=snapshot.run.record.recipe_version if snapshot.run.record is not None else "-",
            runSpecimen=snapshot.run.record.specimen_id if snapshot.run.record is not None else "-",
            synchronization=snapshot.synchronization.reason if snapshot.synchronization is not None else "Not running",
            resultCycle=self._result_cycle,
            resultFrame=self._result_frame,
            score=self._score,
            threshold=self._threshold,
            reviewMargin=self._margin,
            evidence=self._evidence,
            evidenceDetail=self._evidence_detail,
            evidenceTone="normal"
            if self._evidence == "Saved"
            else "neutral"
            if self._evidence == "Not recorded"
            else "review",
            canConnect=snapshot.can_connect and not self._setup_open and not self._training.capture_active,
            cameraConnected=self.controller.camera is not None and self.controller.camera.alive,
            canDisconnect=snapshot.can_disconnect and not self._setup_open and not self._training.capture_active,
            canLoadRecipe=snapshot.can_load_recipe and not self._setup_open and not self._training.capture_active,
            canStart=snapshot.can_start and fresh and not self._setup_open and not self._training.capture_active,
            canStop=snapshot.can_stop or (pipeline is not None and pipeline.alive and not self.controller.closing),
            canEditSpecimen=not self._setup_open
            and not self._training.capture_active
            and not self.controller.closing
            and self.controller.stopping_trial is None
            and (snapshot.run.state == RunState.STOPPED or lost),
            restart=bool(lost),
            setupOpen=self._setup_open,
            regions=regions,
            coverageWarning=(
                f"{snapshot.run.uninspected} cycle(s) not inspected"
                if snapshot.run.mode != RunMode.DEVELOPMENT and snapshot.run.uninspected
                else ""
            ),
            canReplay=not snapshot.busy
            and not self.controller.closing
            and not self._setup_open
            and not self._training.capture_active,
            canStopReplay=pipeline is not None
            and pipeline.alive
            and not self.controller.closing
            and snapshot.run.state != RunState.STOPPING,
            isReplay=replay,
            replayState=pipeline.status.state if replay else "Not running",
            recordingPath=self._replay_path,
            recordingName=Path(self._replay_path).name if self._replay_path else "No recording selected",
            replaySettings=settings_values(self._replay_recipe),
            replayRevision=self._replay_revision,
            replayError=self._replay_error,
            fault=fault,
            closing=self.controller.closing,
            collectionActive=self._training.capture_active,
            collectionState=self._training.status.get("state", ""),
        )
        if status != self._status:
            self._status = status
            self.statusChanged.emit()

    @Slot(int)
    def connectCamera(self, index: int) -> None:
        """Request preview-only acquisition through the controller."""
        if self._setup_open or self._training.capture_active:
            return
        if self.controller.connect_camera(index):
            self._reset_images()
            self._notice = ""
            self.events.append("Camera connect", "Preview only; no inspection")
        self._refresh()

    @Slot()
    def disconnectCamera(self) -> None:
        """Request nonblocking device release after accepted work drains."""
        if self._setup_open or self._training.capture_active:
            return
        self.controller.disconnect_camera(monotonic())
        self._refresh()

    def _error(self, message: str) -> None:
        self._notice = message
        self.events.append("Action unavailable", message, severity="error")
        self._refresh()

    @Slot()
    def chooseRecipe(self) -> None:
        """Choose a recipe with a native picker; cancellation leaves the session unchanged."""
        if self._setup_open:
            return
        self._refresh()
        if not self._status["canLoadRecipe"]:
            self._error("Stop and finalize the current run before loading a recipe.")
            return
        if self._dialogs is None:
            self._error("Recipe picker is unavailable in this session.")
            return
        self._setup_open = True
        self._refresh()
        try:
            path = self._dialogs.choose_recipe()
        finally:
            self._setup_open = False
            self._refresh()
        if path is not None:
            self.loadRecipe(str(path))

    @Slot(str, result=bool)
    def loadRecipe(self, path: str) -> bool:
        """Validate a chosen recipe without changing the actual serial or starting a run."""
        if self._setup_open or self._training.capture_active:
            return False
        try:
            stored = self.controller.select_recipe(Path(path))
        except (OSError, ValueError, RuntimeError) as error:
            self._error(f"Recipe not loaded: {error}")
            return False
        self._reset_inspection()
        self._notice = ""
        self.events.append("Recipe loaded", f"{Path(path).name} | {stored.mode_label}")
        self._refresh()
        return True

    @Slot(str, result=bool)
    def loadLibraryRecipe(self, path: str) -> bool:
        """Load the explicitly selected catalog entry using normal eligibility checks."""
        if not path or path != self._recipe_library.status["selected"]:
            return False
        return self.loadRecipe(path)

    @Slot()
    def startInspection(self) -> None:
        """Review a frozen setup and commit a new development run only after confirmation."""
        if self._setup_open or self._training.capture_active:
            return
        self._refresh()
        if not self._specimen.strip():
            self._error("Enter the actual specimen ID before starting.")
            return
        if not self._status["canStart"]:
            if (
                self.controller.camera is not None
                and self.controller.camera.trial is not None
                and not self._status["restart"]
            ):
                return  # A delayed/repeated Start cannot reopen setup for an active run.
            self._error("Camera is not ready for setup. Wait for a fresh preview and finalize any previous run.")
            return
        if self._dialogs is None:
            self._error("Confirmed setup is unavailable in this session.")
            return
        camera, frame = self.controller.camera, self.controller.last_frame
        previous = camera.trial
        self._setup_open = True
        self._refresh()
        try:
            choice = self._dialogs.confirm_setup(
                previous.recipe if previous is not None else self.controller.camera_recipe or fixture_recipe(),
                self.controller.loaded_recipe,
                self._specimen.strip(),
                frame,
            )
            if choice is None:
                return
            if camera is not self.controller.camera:
                raise RuntimeError("Camera connection changed during setup; review it again.")
            setup = self.controller.prepare_setup(
                choice.specimen_id,
                choice.recipe,
                choice.width,
                choice.height,
                choice.details,
                choice.selection,
            )
            if previous is not None:
                self.controller.stop_camera_run(monotonic())
                self._consume(self.controller.poll_stopping(monotonic()))
                if self.controller.stopping_trial is not None:
                    raise RuntimeError("Previous run is still finalizing; wait and confirm setup again.")
            if self._worker_factory is None:
                record = self.controller.start_camera_run(setup, choice.selection, self._output_root)
            else:
                record = self.controller.start_camera_run(
                    setup,
                    choice.selection,
                    self._output_root,
                    worker_factory=self._worker_factory,
                )
            if record.specimen_id != self._specimen:
                self._specimen = record.specimen_id
                self.specimenChanged.emit()
            self._reset_inspection()
            worker = self.controller.worker
            if worker is not None:
                self._threshold, self._margin = f"{worker.binding.threshold:.3f}", f"{worker.binding.review_margin:.3f}"
            self._notice = ""
            self.events.append(
                "Monitoring started",
                "Unqualified exact-cycle inspection" if worker is not None else "Counting only; no defect detector",
                record.run_id,
            )
        except (OSError, ValueError, RuntimeError) as error:
            self._error(f"Run not started: {error}")
        finally:
            self._setup_open = False
            self._refresh()

    @Slot()
    def stopMonitoring(self) -> None:
        """Bar intake and drain accepted monitoring work without stopping a healthy preview."""
        self._training.stopCollection()
        now = monotonic()
        if self.controller.mode == "camera":
            if self.controller.stop_camera_run(now):
                self._consume(self.controller.poll_stopping(now))
        else:
            self.controller.stop_replay(now)
        self._refresh()

    @Slot()
    def openResults(self) -> None:
        """Open the existing evidence directory without changing the active run."""
        try:
            self._output_root.mkdir(parents=True, exist_ok=True)
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_root))):
                raise OSError("Desktop could not open the inspection-results folder")
        except OSError as error:
            self._error(f"Results folder unavailable: {error}")

    @Slot(str)
    def startFixture(self, specimen: str) -> None:
        """Start explicitly labeled counting-only diagnostics with fixture settings."""
        if self.controller.busy or self.controller.closing or self._setup_open or self._training.capture_active:
            return
        self.controller.source = "Synthetic marker fixture / not camera footage"
        self.controller.synthetic = True
        try:
            record = self.controller.start_replay(specimen, fixture_recipe(), 1)
        except (ValueError, RuntimeError) as error:
            self._notice = str(error)
        else:
            if record is not None:
                self._reset_images()
                self._notice = ""
                self.events.append("Replay started", "Synthetic / counting only", record.run_id)
        self._refresh()

    @Slot()
    def chooseRecording(self) -> None:
        """Choose a local file with cancellation and native-modal command barriers."""
        self._refresh()
        if not self._status["canReplay"] or self._dialogs is None:
            return
        self._setup_open = True
        self._refresh()
        try:
            path = self._dialogs.choose_recording()
        finally:
            self._setup_open = False
            self._refresh()
        if path is not None:
            self.selectRecording(str(path))

    @Slot(str)
    def selectRecording(self, path: str) -> None:
        """Select an existing local file without decoder work or live recipe mutation."""
        self._refresh()
        if not self._status["canReplay"]:
            return
        try:
            chosen = Path(path)
            if not chosen.is_file():
                raise ValueError("Select an existing local recording file")
            self._replay_path = str(chosen.resolve())
        except (OSError, ValueError) as error:
            self._replay_error = f"Recording not selected: {error}"
            self._error(self._replay_error)
            return
        self._replay_error = ""
        self.events.append("Recording selected", f"{chosen.name} | not started")
        self._refresh()

    @Slot("QVariantMap", result=bool)
    def configureReplay(self, values: dict[str, object]) -> bool:
        """Commit a whole validated replay-only edit; cancellation is handled by QML."""
        self._refresh()
        if not self._status["canReplay"]:
            return False
        try:
            recipe = parse_settings(values, self._replay_recipe)
        except ValueError as error:
            self._replay_error = f"Replay settings not applied: {error}"
            self._error(self._replay_error)
            return False
        if recipe != self._replay_recipe:
            self._replay_recipe = recipe
            self._replay_revision += 1
            self.events.append("Replay settings applied", f"replay-v{self._replay_revision} | counting only")
        self._replay_error = self._notice = ""
        self._refresh()
        return True

    @Slot(str)
    def startRecording(self, specimen: str) -> None:
        """Start the selected local recording through the shared counting-only pipeline."""
        self._refresh()
        if not self._status["canReplay"]:
            return
        if not self._replay_path or not specimen.strip():
            self._replay_error = "Select a recording and enter a specimen/replay label before Start."
            self._error(self._replay_error)
            return
        self.controller.source = self._replay_path
        self.controller.synthetic = False
        try:
            record = self.controller.start_replay(specimen, self._replay_recipe, self._replay_revision)
        except (ValueError, RuntimeError) as error:
            self._replay_error = f"Replay not started: {error}"
            self._error(self._replay_error)
            return
        if record is not None:
            self._reset_images()
            self._notice = self._replay_error = ""
            self.events.append(
                "Replay started", "Local recording / counting only; no defect assessment or evidence", record.run_id
            )
        self._refresh()

    @Slot()
    def stopReplay(self) -> None:
        """Stop diagnostics without waiting for the source worker."""
        self.controller.stop_replay(monotonic())
        self._refresh()

    def _consume(self, batch: SourcePoll) -> None:
        run = self.controller.run_record
        run_id = run.run_id if run is not None else "-"
        if batch.stopped is not None:
            self.events.append("Run stopped", batch.stopped.snapshot.reason, run_id, batch.stopped.snapshot.observed)
            if batch.stopped.snapshot.stop_timed_out:
                self._notice = batch.stopped.snapshot.reason
        for update in batch.observations:
            observation = update.event
            if update.error or observation.cycle_started:
                self.events.append(
                    "Cycle accounting fault" if update.error else "Cycle complete",
                    update.error or observation.reason,
                    run_id,
                    observation.observed_cycles,
                    observation.frame_id,
                )
            if update.error:
                self._notice = update.error
        for update in batch.inspections.updates:
            result = update.detection.result
            score = f"{result.score:.3f}" if result is not None else "—"
            evidence = "Evidence saved" if update.evidence_saved else "Evidence unavailable"
            self.results.append(
                update.verdict.value,
                f"Score {score} · {evidence}",
                run_id,
                update.ticket.cycle,
                update.ticket.frame_id,
                "error"
                if update.verdict == InspectionOutcome.DEFECT
                else "warning"
                if update.verdict == InspectionOutcome.NEEDS_REVIEW
                else "info",
            )
            if update.is_latest:
                self._verdict = update.verdict.value
                self._verdict_tone = {
                    InspectionOutcome.NO_DEFECT: "normal",
                    InspectionOutcome.NEEDS_REVIEW: "review",
                    InspectionOutcome.DEFECT: "defect",
                    InspectionOutcome.UNINSPECTED: "neutral",
                }[update.verdict]
                self._result_cycle, self._result_frame = update.ticket.cycle, update.ticket.frame_id
                self._threshold, self._margin = f"{update.binding.threshold:.3f}", f"{update.binding.review_margin:.3f}"
                self._score = f"{update.detection.result.score:.3f}" if update.detection.result is not None else "-"
                self._evidence = (
                    "Saved"
                    if update.evidence_saved
                    else "Failure record saved"
                    if update.detection.evidence is not None
                    else "Unavailable"
                )
                self._evidence_detail = update.detection.reason or str(update.detection.evidence or "")
                result = update.detection.result
                if result is not None:
                    left, top, right, bottom = result.settings.region.pixels(result.source_image)
                    self._roi_raw = self.provider.publish("roiRaw", result.source_image[top:bottom, left:right].copy())
                    self._roi = self.provider.publish("roi", inspection_overlay(result)[top:bottom, left:right].copy())
                else:
                    self._roi = self._roi_raw = ""
                self.imageChanged.emit()
        if batch.inspections.rejected:
            self._notice = batch.inspections.rejected[-1]
        if batch.inspections.failure:
            self._notice = batch.inspections.failure
        if batch.inspections.unavailable is not None or (batch.stopped and batch.stopped.timed_out_ticket is not None):
            self._roi = self._roi_raw = ""
            self._verdict = "Not inspected"
            self._verdict_tone = "neutral"
            ticket = batch.inspections.unavailable or batch.stopped.timed_out_ticket
            self.results.append(
                "Uninspected", "Inspection unavailable or incomplete", run_id, ticket.cycle, ticket.frame_id, "warning"
            )
            self._result_cycle, self._result_frame = ticket.cycle, ticket.frame_id
            self._score, self._evidence = "-", "Incomplete" if batch.stopped is not None else "Unavailable"
            self.imageChanged.emit()
        frame = (
            batch.frame.image
            if batch.frame is not None
            else batch.preview.frame.image
            if batch.preview is not None
            else None
        )
        if frame is not None:
            self._preview = self.provider.publish("preview", frame)
            self.imageChanged.emit()

    @Slot()
    def poll(self) -> None:
        """Apply bounded controller updates on the GUI thread, then notify changed properties."""
        now = monotonic()
        if self.controller.stopping_trial is not None:
            self._consume(self.controller.poll_stopping(now))
        if self.controller.mode == "camera":
            self._consume(self.controller.poll_camera())
            camera = self.controller.camera
            if camera is not None and not camera.alive and camera.trial is not None:
                self.controller.stop_camera_run(now)
        else:
            self._consume(self.controller.poll_replay(now))
        self._training.poll()
        snapshot = self.controller.snapshot()
        state = (
            snapshot.camera.state
            if snapshot.source_mode == "camera" and snapshot.camera is not None
            else (snapshot.replay.state if snapshot.replay is not None else "Stopped")
        )
        reason = (
            snapshot.camera.reason
            if snapshot.source_mode == "camera" and snapshot.camera is not None
            else (snapshot.replay.reason if snapshot.replay is not None else "No active source")
        )
        if state != self._last_source_state:
            self.events.append(state, reason)
            self._last_source_state = state
        if state == "Fault":
            self._notice = reason
        self._refresh()
        if (
            self.controller.closing
            and self.controller.close_ready(now, collecting=self._training.capture_active)
            and not self._close_notified
        ):
            self._close_notified = True
            self.closeReady.emit()

    @Slot(result=bool)
    def requestClose(self) -> bool:
        """Return whether Qt may close, keeping polling active during asynchronous drain."""
        if self._setup_open:
            return False
        if self._training.training:
            self._error("Training is active. Wait for completion or cancel it before exiting.")
            return False
        now = monotonic()
        self._training.request_close()
        if self.controller.close_ready(now, collecting=self._training.capture_active):
            self.timer.stop()
            return True
        self.controller.request_close(now)
        self._refresh()
        return False

    @Slot()
    def dispose(self) -> None:
        """Stop callbacks before engine destruction, retaining development exit semantics."""
        self.timer.stop()
        self._offline.dispose()
        self._setup.dispose()
        self._training.dispose()
        self.controller.request_close(monotonic())
