# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Qt-independent coordination of cycle tickets, results and run finalization."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty
from time import monotonic
from uuid import uuid4

from rdt_monitor.camera import CameraFrame, CameraPreview, CameraStatus, MarkerTrial
from rdt_monitor.domain import InspectionRecord, RunRecord, SyncResult, SyncState
from rdt_monitor.lifecycle import (
    CycleTicket,
    InspectionOutcome,
    Readiness,
    RunLifecycle,
    RunMode,
    RunSnapshot,
    RunState,
)
from rdt_monitor.live import CycleDetection, LiveInspection
from rdt_monitor.recipes import (
    RecipeDetector,
    SpecimenDetails,
    StoredRecipe,
    load_recipe,
    save_run_setup,
    specimen_setup,
)
from rdt_monitor.replay import (
    FrameSource,
    Preview,
    ReplayPipeline,
    ReplayRecipe,
    ReplayStatus,
    SyntheticSource,
    VideoSource,
)


@dataclass(frozen=True)
class InspectionUpdate:
    """Settled result for presentation, with model outcome separate from validity.

    Args:
        ticket (CycleTicket): Admitted run/cycle/frame identity.
        detection (CycleDetection): Exact inference and evidence completion.
        outcome (InspectionOutcome): Model outcome, including defects without evidence.
        verdict (InspectionOutcome): Evidence-qualified outcome to display.
        evidence_saved (bool): Whether all required evidence was committed.
        is_latest (bool): Whether this completion may replace the latest displayed ROI.
        binding (RecipeDetector): Frozen detector settings, retained after worker retirement.
    """

    ticket: CycleTicket
    detection: CycleDetection
    outcome: InspectionOutcome
    verdict: InspectionOutcome
    evidence_saved: bool
    is_latest: bool
    binding: RecipeDetector


@dataclass(frozen=True)
class StopUpdate:
    """Finalized run and the latest ticket invalidated by a stop deadline.

    Args:
        snapshot (RunSnapshot): Final authoritative lifecycle state.
        timed_out_ticket (CycleTicket | None): Latest unavailable result, if it replaces the displayed cycle.
    """

    snapshot: RunSnapshot
    timed_out_ticket: CycleTicket | None = None


@dataclass(frozen=True)
class ObservationUpdate:
    """Source event and its admitted history entry, or explicit accounting error."""

    event: SyncResult
    record: InspectionRecord | None = None
    error: str = ""


@dataclass(frozen=True)
class InspectionPoll:
    """Bounded inspection completions and worker faults for one GUI update."""

    updates: tuple[InspectionUpdate, ...] = ()
    rejected: tuple[str, ...] = ()
    failure: str = ""
    unavailable: CycleTicket | None = None


@dataclass(frozen=True)
class SourcePoll:
    """Already-consumed source data; views never dequeue worker events themselves."""

    observations: tuple[ObservationUpdate, ...] = ()
    inspections: InspectionPoll = InspectionPoll()
    frame: CameraFrame | None = None
    preview: Preview | None = None
    stopped: StopUpdate | None = None


@dataclass(frozen=True)
class MonitorSnapshot:
    """Coherent domain and command state for either desktop presentation."""

    run: RunSnapshot
    source_mode: str
    camera: CameraStatus | None
    synchronization: SyncResult | None
    replay: ReplayStatus | None
    can_connect: bool
    can_disconnect: bool
    can_load_recipe: bool
    can_start: bool
    can_stop: bool
    busy: bool
    fault: str


class MonitorController:
    """Coordinate run accounting on one owning thread without depending on Qt.

    Camera/replay workers own acquisition and synchronization. The controller
    drains source events before inspection completions and owns command eligibility,
    configuration commit, admission, validity, history, defect latching and finalization.

    Args:
        capacity (int): Maximum lifecycle pending tickets.
        stop_timeout (float): Existing development finalization budget in seconds.
    """

    def __init__(self, *, capacity: int = 128, stop_timeout: float = 3) -> None:
        self.lifecycle = RunLifecycle(capacity=capacity, stop_timeout=stop_timeout)
        self.run_record: RunRecord | None = None
        self.worker: LiveInspection | None = None
        self.retired_worker: LiveInspection | None = None
        self.tickets: dict[int, CycleTicket] = {}
        self._events: dict[int, SyncResult] = {}
        self.defects = 0
        self.latest_cycle = 0
        self.fault = ""
        self.inspections: list[InspectionRecord] = []
        self.camera: CameraPreview | None = None
        self.pipeline: ReplayPipeline | None = None
        self.stopping_trial: MarkerTrial | None = None
        self.disconnect_after_stop = False
        self.mode = "replay"
        self.source: str | None = None
        self.synthetic = False
        self.camera_recipe: ReplayRecipe | None = None
        self.loaded_recipe: tuple[StoredRecipe, Path] | None = None
        self.last_frame: CameraFrame | None = None
        self.closing = False
        self.close_requested_at: float | None = None

    @property
    def busy(self) -> bool:
        """Return whether a source, run or retired worker still owns resources."""
        return (
            (self.retired_worker is not None and not self.retired_worker.finished.is_set())
            or self.stopping_trial is not None
            or self.lifecycle.snapshot.state != RunState.STOPPED
            or (self.pipeline is not None and self.pipeline.alive)
            or (self.camera is not None and self.camera.alive)
        )

    def snapshot(self) -> MonitorSnapshot:
        """Return run/source health and Python-owned command eligibility.

        Returns:
            MonitorSnapshot: State sampled on the controller's owning thread.
        """
        camera = self.camera.status if self.camera is not None else None
        trial = self.camera.trial if self.camera is not None else None
        result = trial.result if trial is not None else None
        lost = result is not None and result.state == SyncState.LOST
        stopped = self.lifecycle.snapshot.state == RunState.STOPPED
        can_start = (
            camera is not None
            and camera.state == "Streaming"
            and self.camera.alive
            and (trial is None or lost)
            and not self.closing
            and self.stopping_trial is None
            and not self.disconnect_after_stop
            and (self.retired_worker is None or self.retired_worker.finished.is_set())
        )
        return MonitorSnapshot(
            self.lifecycle.snapshot,
            self.mode,
            camera,
            result,
            self.pipeline.status if self.pipeline is not None else None,
            not self.busy and not self.closing,
            self.camera is not None and self.camera.alive and not self.disconnect_after_stop and not self.closing,
            stopped
            and not self.closing
            and self.stopping_trial is None
            and (not self.disconnect_after_stop or self.camera is None or not self.camera.alive),
            bool(can_start),
            trial is not None and self.stopping_trial is None and not self.closing,
            self.busy,
            self.fault,
        )

    def connect_camera(self, index: int, *, factory: Callable[[int], CameraPreview] = CameraPreview) -> bool:
        """Open one source worker after rechecking exclusive source ownership.

        Args:
            index (int): Explicit generic OpenCV index, not verified camera identity.
            factory (Callable[[int], CameraPreview]): Injectable worker constructor.

        Returns:
            bool: Whether a new connection was started.
        """
        if not self.snapshot().can_connect:
            return False
        self.camera = factory(index)
        self.mode = "camera"
        self.disconnect_after_stop = False
        self.run_record = None
        self.inspections.clear()
        self.last_frame = None
        self.camera.start()
        return True

    def select_recipe(self, path: Path) -> StoredRecipe:
        """Validate and select a recipe without changing specimen identity or starting.

        Args:
            path (Path): Operator-selected recipe file.

        Returns:
            StoredRecipe: Loaded settings; UI retains its current specimen serial.

        Raises:
            RuntimeError: If a run is active, stopping or closing.
            OSError: If the recipe cannot be read.
            ValueError: If the recipe or detector reference is invalid.
        """
        if not self.snapshot().can_load_recipe:
            raise RuntimeError("Stop the current run before loading a recipe.")
        stored = load_recipe(path)
        if stored.detector is not None:
            stored.detector.settings(path)
        self.loaded_recipe = (stored, path)
        self.camera_recipe = stored.recipe
        return stored

    def prepare_setup(
        self,
        specimen_id: str,
        recipe: ReplayRecipe,
        width: int,
        height: int,
        details: SpecimenDetails,
        selection: tuple[StoredRecipe, Path] | None,
    ) -> StoredRecipe:
        """Build a confirmed candidate while checking that its source recipe is unchanged.

        Args:
            specimen_id (str): Actual current specimen serial.
            recipe (ReplayRecipe): Confirmed marker/timing settings.
            width (int): Frozen setup image width.
            height (int): Frozen setup image height.
            details (SpecimenDetails): Actual specimen name/batch.
            selection (tuple[StoredRecipe, Path] | None): Recipe reviewed in setup.

        Returns:
            StoredRecipe: Specimen-specific candidate preserving the trained ROI and detector.

        Raises:
            OSError: If the reviewed recipe cannot be reread.
            ValueError: If it changed or the candidate is invalid.
        """
        if selection is None:
            return StoredRecipe(specimen_id, recipe, width, height, specimen=details)
        original, source = selection
        if load_recipe(source) != original:
            raise ValueError("Reusable recipe changed during setup; load it again")
        return specimen_setup(original, specimen_id, recipe, details)

    def start_camera_run(
        self,
        setup: StoredRecipe,
        selection: tuple[StoredRecipe, Path] | None,
        output_root: Path,
        *,
        worker_factory: Callable[..., LiveInspection] = LiveInspection,
    ) -> RunRecord:
        """Commit the frozen setup before attaching exact-cycle capture to the camera.

        Args:
            setup (StoredRecipe): Explicitly confirmed specimen configuration.
            selection (tuple[StoredRecipe, Path] | None): Original reusable recipe and path.
            output_root (Path): Existing inspection-results root.
            worker_factory (Callable[..., LiveInspection]): Injectable live-worker constructor.

        Returns:
            RunRecord: New immutable run identity after successful source attachment.

        Raises:
            RuntimeError: If camera/run ownership prevents Start or attachment fails.
            OSError: If required setup persistence fails.
            ValueError: If configuration persistence or source recipe validation fails.
        """
        if not self.snapshot().can_start or self.lifecycle.snapshot.state != RunState.STOPPED:
            raise RuntimeError("Camera is not ready or the previous run is still stopping")
        if selection is not None and load_recipe(selection[1]) != selection[0]:
            raise ValueError("Reusable recipe changed during setup; load it again")
        run_id = uuid4().hex[:12]
        setup_path = save_run_setup(output_root, run_id, setup, selection[1] if selection is not None else None)
        worker = None
        if selection is not None and setup.detector is not None:
            worker = worker_factory(
                setup.detector,
                selection[1],
                output_root / f"run-{run_id}",
                output_root=output_root,
                setup_path=setup_path,
            )
            worker.start()
        try:
            if worker is None:
                self.camera.start_trial(setup.recipe)
            else:
                self.camera.start_trial(setup.recipe, capture=worker.capture)
        except RuntimeError:
            if worker is not None:
                worker.stop(cancel=True)
                if not worker.finished.is_set():
                    self.retired_worker = worker
            raise
        record = RunRecord(
            run_id,
            setup.specimen_id,
            f"{setup.profile.revision if setup.profile is not None else 'legacy'} / {setup_path.stem}",
            f"Generic camera index {self.camera.status.index}",
            datetime.now(UTC),
        )
        self.start(
            record,
            mode=RunMode.EXPERIMENTAL_INSPECTION if worker is not None else RunMode.DEVELOPMENT,
            readiness=Readiness(),
            worker=worker,
        )
        self.camera_recipe = setup.recipe
        self.loaded_recipe = selection
        return record

    def start_replay(
        self,
        specimen_id: str,
        recipe: ReplayRecipe,
        revision: int,
        *,
        factory: Callable[[Callable[[], FrameSource], ReplayRecipe], ReplayPipeline] = ReplayPipeline,
    ) -> RunRecord | None:
        """Start an explicitly selected counting-only replay without camera ownership.

        Args:
            specimen_id (str): Actual operator-entered specimen/replay label.
            recipe (ReplayRecipe): Frozen replay synchronization settings.
            revision (int): Existing UI settings revision.
            factory (Callable): Injectable replay-worker constructor.

        Returns:
            RunRecord | None: New run, or None when a source/run already owns resources.

        Raises:
            ValueError: If specimen identity is blank.
        """
        if self.source is None or self.busy or self.closing:
            return None
        if not specimen_id.strip():
            raise ValueError("Enter a specimen or replay label to start.")
        source = self.source
        source_factory = SyntheticSource if self.synthetic else lambda: VideoSource(source)
        pipeline = factory(source_factory, recipe)
        record = RunRecord(uuid4().hex[:12], specimen_id.strip(), f"replay-v{revision}", source, datetime.now(UTC))
        self.start(record, mode=RunMode.DEVELOPMENT, readiness=Readiness())
        self.mode = "replay"
        self.pipeline = pipeline
        try:
            pipeline.start()
        except RuntimeError:
            pipeline.stop()
            now = monotonic()
            self.lifecycle.request_stop(now)
            self.lifecycle.finish_stop(now, source_quiescent=not pipeline.alive)
            raise
        return record

    def stop_replay(self, now: float) -> bool:
        """Request replay stop without joining the decoder worker.

        Args:
            now (float): Host monotonic time.

        Returns:
            bool: Whether a live replay received the stop request.
        """
        if self.pipeline is None or not self.pipeline.alive:
            return False
        self.lifecycle.request_stop(now)
        self.pipeline.stop()
        return True

    def stop_camera_run(self, now: float) -> bool:
        """Bar new observations and retain the callback barrier for asynchronous drain.

        Args:
            now (float): Host monotonic time.

        Returns:
            bool: Whether a trial is finalizing.
        """
        if self.camera is None:
            return False
        if self.stopping_trial is None:
            self.stopping_trial = self.camera.stop_trial()
        if self.stopping_trial is None:
            return False
        self.lifecycle.request_stop(now)
        return True

    def disconnect_camera(self, now: float) -> bool:
        """Request release only after accepted run work has finalized.

        Args:
            now (float): Host monotonic time.

        Returns:
            bool: Whether an active camera received the disconnect request.
        """
        if self.camera is None or not self.camera.alive:
            return False
        self.disconnect_after_stop = True
        if self.camera.trial is not None or self.stopping_trial is not None:
            self.stop_camera_run(now)
        else:
            self.camera.stop()
        return True

    def request_close(self, now: float) -> bool:
        """Bar new source commands and request nonblocking shutdown.

        Args:
            now (float): Host monotonic time.

        Returns:
            bool: Whether the adapter must schedule its first close-deadline callback.
        """
        first = self.close_requested_at is None
        self.closing = True
        if first:
            self.close_requested_at = now
        self.stop_replay(now)
        self.disconnect_camera(now)
        return first

    def close_ready(self, now: float, *, collecting: bool = False) -> bool:
        """Apply the existing three-second development close budget without waiting.

        Args:
            now (float): Host monotonic time.
            collecting (bool): Auxiliary collection work still owns resources.

        Returns:
            bool: Whether sources have exited or the development abandonment deadline expired.
        """
        return not (self.busy or collecting) or (
            self.close_requested_at is not None and now - self.close_requested_at >= 3
        )

    def poll_inspections(self) -> InspectionPoll:
        """Consume at most one bounded result-queue capacity on the owning thread.

        Returns:
            InspectionPoll: Settled results, rejected completions and explicit worker failure.
        """
        worker = self.worker
        if worker is None:
            return InspectionPoll()
        updates, rejected = [], []
        for _ in range(worker.results.maxsize):
            try:
                detection = worker.results.get_nowait()
            except Empty:
                break
            try:
                updates.append(self.settle(worker, detection))
            except ValueError as error:
                rejected.append(str(error))
        unavailable = None
        if worker.failure:
            self.fault = worker.failure
            if "backlog overflow" in worker.failure:
                unavailable = self.fail_pending(worker.failure)
        return InspectionPoll(tuple(updates), tuple(rejected), worker.failure, unavailable)

    def _drain_observations(
        self, source: MarkerTrial | ReplayPipeline, *, draining: bool
    ) -> tuple[ObservationUpdate, ...]:
        updates = []
        for _ in range(source.events.maxsize):
            try:
                event = source.events.get_nowait()
            except Empty:
                break
            try:
                updates.append(ObservationUpdate(event, self.observe(event, source_draining=draining)))
            except (RuntimeError, ValueError) as error:
                reason = f"Cycle accounting failed: {error}"
                self.lifecycle.coverage_lost(reason)
                updates.append(ObservationUpdate(event, error=str(error)))
        return tuple(updates)

    def poll_stopping(self, now: float) -> SourcePoll:
        """Drain accepted cycles before stopping inference or releasing the device.

        Args:
            now (float): Host monotonic time.

        Returns:
            SourcePoll: Bounded drained data and optional final stop state.
        """
        trial = self.stopping_trial
        if trial is None:
            return SourcePoll()
        observations = self._drain_observations(trial, draining=True)
        worker = self.worker
        if worker is not None and trial.stopped:
            worker.stop()
        inspections = self.poll_inspections()
        quiescent = trial.stopped and trial.events.empty() and (worker is None or worker.finished.is_set())
        stopped = self.finish_stop(now, source_quiescent=quiescent)
        if stopped is not None:
            self.stopping_trial = None
            if self.disconnect_after_stop and self.camera is not None:
                self.camera.stop()
        return SourcePoll(observations, inspections, stopped=stopped)

    def poll_camera(self) -> SourcePoll:
        """Consume active camera events before model results and the lossy preview.

        Returns:
            SourcePoll: Bounded observations/results and at most one fresh preview frame.
        """
        if self.camera is None:
            return SourcePoll()
        trial = self.camera.trial
        observations = self._drain_observations(trial, draining=False) if trial is not None else ()
        inspections = self.poll_inspections() if trial is not None else InspectionPoll()
        if trial is not None:
            result, status = trial.result, self.camera.status
            if result is not None and result.state == SyncState.LOST:
                self.lifecycle.coverage_lost(result.reason)
            elif status.state in {"Fault", "Stopped"}:
                self.lifecycle.coverage_lost(status.reason)
        try:
            frame = self.camera.frames.get_nowait()
        except Empty:
            frame = None
        if frame is not None:
            self.last_frame = frame
        return SourcePoll(observations, inspections, frame=frame)

    def poll_replay(self, now: float) -> SourcePoll:
        """Drain replay independently of preview replacement and finalize ended sources.

        Args:
            now (float): Host monotonic time.

        Returns:
            SourcePoll: Bounded admitted replay events and latest preview.
        """
        if self.pipeline is None:
            return SourcePoll()
        observations = self._drain_observations(
            self.pipeline, draining=self.lifecycle.snapshot.state == RunState.STOPPING
        )
        try:
            preview = self.pipeline.previews.get_nowait()
        except Empty:
            preview = None
        if not self.pipeline.alive:
            self.lifecycle.request_stop(now)
            self.lifecycle.finish_stop(now, source_quiescent=self.pipeline.events.empty())
        return SourcePoll(observations, preview=preview)

    def start(
        self,
        record: RunRecord,
        *,
        mode: RunMode,
        readiness: Readiness,
        worker: LiveInspection | None = None,
    ) -> None:
        """Start a new run and reset presentation-independent inspection state.

        Args:
            record (RunRecord): New immutable run identity.
            mode (RunMode): Counting, experimental or readiness-gated inspection.
            readiness (Readiness): Current station prerequisites.
            worker (LiveInspection | None): Worker exclusively associated with this run.

        Raises:
            RuntimeError: If a retired worker is unfinished or a run cannot start.
            ValueError: If the lifecycle rejects the run identity or mode.
        """
        if self.retired_worker is not None and not self.retired_worker.finished.is_set():
            raise RuntimeError("Previous detector is still finishing after Stop; wait before starting a new run.")
        self.lifecycle.start(record, mode=mode, readiness=readiness)
        self.run_record = record
        self.worker = worker
        self.retired_worker = None
        self.tickets.clear()
        self._events.clear()
        self.defects = self.latest_cycle = 0
        self.fault = ""
        self.inspections.clear()

    def observe(self, event: SyncResult, *, source_draining: bool = False) -> InspectionRecord | None:
        """Admit an exact cycle and append bounded inspection-position history.

        Args:
            event (SyncResult): Source-worker synchronization event.
            source_draining (bool): Admit already accepted development events during Stop.

        Returns:
            InspectionRecord | None: New history entry, or None for a non-inspection event.

        Raises:
            RuntimeError: If cycle admission is unavailable or exceeds capacity.
            ValueError: If the source cycle/frame/time sequence is invalid.
        """
        if not event.inspection_ready or self.run_record is None:
            return None
        snapshot = self.lifecycle.snapshot
        if snapshot.record is not None and snapshot.record == self.run_record:
            ticket = self.lifecycle.observe_cycle(
                event.observed_cycles, event.frame_id, event.timestamp, source_draining=source_draining
            )
            if self.worker is not None:
                self.tickets[event.frame_id] = ticket
                self._events[event.frame_id] = event
        reason = (
            "Awaiting cycle inference; result and evidence pending"
            if self.worker is not None
            else "Unknown / Uninspected: counting-only run; no detector bound"
        )
        record = InspectionRecord(
            self.run_record.run_id, event.observed_cycles, event.frame_id, event.timestamp, reason
        )
        self.inspections.append(record)
        if len(self.inspections) > 500:
            del self.inspections[0]
        return record

    def settle(self, worker: LiveInspection, detection: CycleDetection) -> InspectionUpdate:
        """Settle one current-worker result, preserving latch and latest-cycle ordering.

        Args:
            worker (LiveInspection): Worker from whose bounded result queue this was read.
            detection (CycleDetection): Completion with its original source identity.

        Returns:
            InspectionUpdate: Validated completion ready for rendering.

        Raises:
            ValueError: If worker, run or source identity is stale, unmatched or duplicated.
        """
        event = detection.event
        ticket = self.tickets.get(event.frame_id)
        if (
            worker is not self.worker
            or ticket is None
            or self.run_record is None
            or ticket.run_id != self.run_record.run_id
            or event != self._events.get(event.frame_id)
        ):
            raise ValueError("Live result has no matching cycle ticket; result rejected.")
        outcome = (
            worker.binding.verdict(detection.result.score)
            if detection.result is not None
            else InspectionOutcome.UNINSPECTED
        )
        evidence_saved = detection.evidence is not None and not detection.reason
        self.lifecycle.settle(ticket, outcome, evidence_saved=evidence_saved)
        del self.tickets[event.frame_id]
        del self._events[event.frame_id]
        if outcome == InspectionOutcome.DEFECT:
            self.defects += 1
        is_latest = ticket.cycle >= self.latest_cycle
        if is_latest:
            self.latest_cycle = ticket.cycle
        if detection.reason:
            self.fault = detection.reason
        return InspectionUpdate(
            ticket,
            detection,
            outcome,
            outcome if evidence_saved else InspectionOutcome.UNINSPECTED,
            evidence_saved,
            is_latest,
            worker.binding,
        )

    def fail_pending(self, reason: str) -> CycleTicket | None:
        """Mark pending coverage unavailable after a result-backlog overflow.

        Args:
            reason (str): Worker failure to retain for operator presentation.

        Returns:
            CycleTicket | None: Latest unavailable cycle if it replaces the displayed result.
        """
        self.fault = reason
        latest = None
        for ticket in self.tickets.values():
            self.lifecycle.settle(ticket, InspectionOutcome.UNINSPECTED, evidence_saved=False)
            if ticket.cycle >= self.latest_cycle:
                self.latest_cycle = ticket.cycle
                latest = ticket
        self.tickets.clear()
        self._events.clear()
        return latest

    def finish_stop(self, now: float, *, source_quiescent: bool) -> StopUpdate | None:
        """Finalize asynchronously, retaining an unfinished worker until it exits.

        Args:
            now (float): Host monotonic time for the existing lifecycle deadline.
            source_quiescent (bool): Source callbacks/events and inference worker are drained.

        Returns:
            StopUpdate | None: Final state, or None while the lifecycle is still draining.

        Raises:
            RuntimeError: If the lifecycle has not received a Stop request.
            ValueError: If the monotonic time is invalid.
        """
        if not self.lifecycle.finish_stop(now, source_quiescent=source_quiescent):
            return None
        last = None
        if self.worker is not None:
            self.worker.stop(cancel=True)
            if not self.worker.finished.is_set():
                self.retired_worker = self.worker
            if self.lifecycle.snapshot.stop_timed_out and self.tickets:
                candidate = max(self.tickets.values(), key=lambda ticket: ticket.cycle)
                if candidate.cycle >= self.latest_cycle:
                    self.latest_cycle = candidate.cycle
                    last = candidate
            self.worker = None
            self.tickets.clear()
            self._events.clear()
        return StopUpdate(self.lifecycle.snapshot, last)
