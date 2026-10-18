# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Explicit run transitions and bounded cycle accounting, independent of Qt."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from rdt_monitor.domain import RunRecord


class RunState(StrEnum):
    """Run health, separate from camera connection and individual defect outcomes."""

    STOPPED = "Stopped"
    READY = "Ready"
    RUNNING = "Running"
    DEGRADED = "Degraded"
    RECOVERING = "Recovering"
    STOPPING = "Stopping"


class RunMode(StrEnum):
    """Distinguish unqualified counting from inspection with readiness requirements."""

    DEVELOPMENT = "Development"
    EXPERIMENTAL_INSPECTION = "Unqualified live inspection"
    INSPECTION = "Inspection"


class InspectionOutcome(StrEnum):
    """An individual inspection outcome, never inferred from a missing result."""

    NO_DEFECT = "No defect detected"
    NEEDS_REVIEW = "Needs review"
    DEFECT = "Defect detected"
    UNINSPECTED = "Uninspected"


@dataclass(frozen=True)
class Readiness:
    """Verified prerequisites supplied by the station's readiness checks.

    Args:
        camera_verified (bool): Approved camera identity and settings match.
        recipe_approved (bool): Immutable recipe passed qualification and approval.
        detector_ready (bool): Bound detector is loaded and passed readiness checks.
        evidence_ready (bool): Evidence writer and configured retention are available.
        authorization_ready (bool): Protected configuration authorization is available.
    """

    camera_verified: bool = False
    recipe_approved: bool = False
    detector_ready: bool = False
    evidence_ready: bool = False
    authorization_ready: bool = False

    @property
    def blockers(self) -> tuple[str, ...]:
        """Return unmet inspection prerequisites in a stable, user-readable order."""
        checks = (
            (self.camera_verified, "Verified camera identity"),
            (self.recipe_approved, "Approved recipe"),
            (self.detector_ready, "Live defect detector"),
            (self.evidence_ready, "Durable evidence and retention"),
            (self.authorization_ready, "Windows administrator authorization"),
        )
        return tuple(reason for passed, reason in checks if passed is not True)


@dataclass(frozen=True)
class CycleTicket:
    """Exact identity of one observed cycle admitted to a run.

    Args:
        run_id (str): Immutable run identity.
        segment (int): Coverage segment within this run.
        cycle (int): Cumulative observed cycle number, excluding unknown gaps.
        frame_id (int): Exact inspection-position source frame.
        timestamp (float): Source or host-read monotonic time.
    """

    run_id: str
    segment: int
    cycle: int
    frame_id: int
    timestamp: float


@dataclass(frozen=True)
class RunSnapshot:
    """Read-only run state; memory-only until a persistence adapter records it.

    Args:
        record (RunRecord | None): Frozen run identity, retained after Stop.
        mode (RunMode): Development counting or readiness-gated inspection.
        state (RunState): Current lifecycle state.
        segment (int): Coverage segment, incremented only after confirmed recovery.
        observed (int): Visually completed cycles; not physical cycles during gaps.
        inspected (int): Completed inspections with confirmed saved evidence.
        uninspected (int): Explicitly failed or counting-only cycles.
        pending (int): Accepted cycle jobs still awaiting finalization.
        defects (int): Recorded defect outcomes; later good outcomes do not clear these.
        gaps (int): Distinct coverage interruptions.
        reason (str): Current transition or failure explanation.
        stop_timed_out (bool): Whether the latest Stop exceeded its finalization budget.
    """

    record: RunRecord | None
    mode: RunMode
    state: RunState
    segment: int
    observed: int
    inspected: int
    uninspected: int
    pending: int
    defects: int
    gaps: int
    reason: str
    stop_timed_out: bool


class RunLifecycle:
    """Own ordered run commands and bounded pending cycle identities.

    The coordinator calls this object from one owning thread. Recovery keeps the
    same run identity but requires fresh readiness and a new synchronization
    baseline. It never reconnects hardware or declares approval itself.

    Args:
        capacity (int): Maximum accepted, unfinished inspection jobs.
        stop_timeout (float): Configured monotonic-time budget to finalize jobs.

    Raises:
        ValueError: If capacity or stop timeout is invalid.
    """

    def __init__(self, *, capacity: int, stop_timeout: float) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("Pending cycle capacity must be a positive integer")
        if isinstance(stop_timeout, bool) or not isfinite(stop_timeout) or stop_timeout <= 0:
            raise ValueError("Stop timeout must be finite and positive")
        self._capacity = capacity
        self._stop_timeout = stop_timeout
        self._record: RunRecord | None = None
        self._mode = RunMode.DEVELOPMENT
        self._state = RunState.STOPPED
        self._reason = "No run"
        self._segment = 0
        self._segment_cycle = 0
        self._last_frame = -1
        self._last_timestamp = -1.0
        self._observed = 0
        self._inspected = 0
        self._uninspected = 0
        self._defects = 0
        self._gaps = 0
        self._pending: dict[int, CycleTicket] = {}
        self._deadline: float | None = None
        self._stop_timed_out = False

    @property
    def snapshot(self) -> RunSnapshot:
        """Return immutable counters and identity without exposing mutable jobs."""
        return RunSnapshot(
            self._record,
            self._mode,
            self._state,
            self._segment,
            self._observed,
            self._inspected,
            self._uninspected,
            len(self._pending),
            self._defects,
            self._gaps,
            self._reason,
            self._stop_timed_out,
        )

    def start(self, record: RunRecord, *, mode: RunMode, readiness: Readiness) -> None:
        """Start a new run, checking production prerequisites before changing state.

        Args:
            record (RunRecord): New immutable identity, including the specimen.
            mode (RunMode): Explicitly selected development or inspection mode.
            readiness (Readiness): Fresh readiness results.

        Raises:
            RuntimeError: If another run is active or inspection is not ready.
            ValueError: If the run identity or mode is invalid or reused.
        """
        if self._state != RunState.STOPPED:
            raise RuntimeError("Stop and finalize the current run before starting another")
        if not isinstance(mode, RunMode):
            raise ValueError("An explicit run mode is required")
        identities = (record.run_id, record.specimen_id, record.recipe_version, record.source)
        if any(not value.strip() for value in identities):
            raise ValueError("Run, specimen, recipe, and source identities must not be blank")
        if self._record is not None and record.run_id == self._record.run_id:
            raise ValueError("A new Start requires a new run ID")
        if mode == RunMode.INSPECTION and readiness.blockers:
            raise RuntimeError(f"Inspection unavailable: {', '.join(readiness.blockers)}")
        self._record = record
        self._mode = mode
        self._state = RunState.READY
        self._reason = "Waiting for a fresh complete cycle"
        self._segment = 1
        self._segment_cycle = 0
        self._last_frame = -1
        self._last_timestamp = -1.0
        self._observed = self._inspected = self._uninspected = self._defects = self._gaps = 0
        self._pending.clear()
        self._deadline = None
        self._stop_timed_out = False

    def observe_cycle(
        self, segment_cycle: int, frame_id: int, timestamp: float, *, source_draining: bool = False
    ) -> CycleTicket:
        """Admit an exact completed cycle once, never counting unknown gap cycles.

        Args:
            segment_cycle (int): Next sequential cycle in the current synchronizer.
            frame_id (int): Exact stable-return frame identity.
            timestamp (float): Source or host-read completion time.
            source_draining (bool): Account for a development event accepted by the
                source before Stop, delivered after its admission barrier.

        Returns:
            CycleTicket: Identity required to settle an accepted inspection.

        Raises:
            RuntimeError: If intake has stopped or pending capacity is exhausted.
            ValueError: If cycle/frame/time order is invalid.
        """
        draining = source_draining and self._state == RunState.STOPPING and self._mode != RunMode.INSPECTION
        degraded_experiment = self._mode == RunMode.EXPERIMENTAL_INSPECTION and self._state == RunState.DEGRADED
        if (
            self._state not in {RunState.READY, RunState.RUNNING, RunState.RECOVERING}
            and not draining
            and not degraded_experiment
        ) or self._record is None:
            raise RuntimeError("Run is not accepting cycles")
        if (
            isinstance(segment_cycle, bool)
            or not isinstance(segment_cycle, int)
            or segment_cycle != self._segment_cycle + 1
            or isinstance(frame_id, bool)
            or not isinstance(frame_id, int)
            or frame_id <= self._last_frame
            or isinstance(timestamp, bool)
            or not isfinite(timestamp)
            or timestamp < 0
            or timestamp <= self._last_timestamp
        ):
            raise ValueError("Completed cycle identity must be sequential with increasing frame/time")
        self._segment_cycle = segment_cycle
        self._last_frame, self._last_timestamp = frame_id, timestamp
        self._observed += 1
        ticket = CycleTicket(self._record.run_id, self._segment, self._observed, frame_id, timestamp)
        if self._mode == RunMode.DEVELOPMENT:
            self._uninspected += 1
        elif len(self._pending) >= self._capacity:
            self._uninspected += 1
            self.coverage_lost("Inspection backlog capacity exceeded; cycle uninspected")
            raise RuntimeError(self._reason)
        else:
            self._pending[ticket.cycle] = ticket
        if not draining and not degraded_experiment:
            self._state = RunState.RUNNING
            self._reason = "Development counting only" if self._mode == RunMode.DEVELOPMENT else "Cycle admitted"
        return ticket

    def settle(self, ticket: CycleTicket, outcome: InspectionOutcome, *, evidence_saved: bool) -> None:
        """Finalize one accepted cycle without accepting duplicate or stale results.

        Args:
            ticket (CycleTicket): Exact identity returned by admission.
            outcome (InspectionOutcome): Detector outcome or explicit failure.
            evidence_saved (bool): Confirmed durable evidence-write result.

        Raises:
            ValueError: If the result is invalid, stale, duplicated, or belongs to another run.
        """
        if not isinstance(outcome, InspectionOutcome) or not isinstance(evidence_saved, bool):
            raise ValueError("Explicit outcome and evidence-write result are required")
        if self._pending.get(ticket.cycle) != ticket:
            raise ValueError("Cycle is not pending in this run")
        del self._pending[ticket.cycle]
        if outcome == InspectionOutcome.DEFECT:
            self._defects += 1
        if outcome == InspectionOutcome.UNINSPECTED or not evidence_saved:
            self._uninspected += 1
            if self._state != RunState.STOPPING:
                self.coverage_lost("Inspection or required evidence unavailable")
        else:
            self._inspected += 1

    def coverage_lost(self, reason: str) -> None:
        """Mark a coverage gap without stopping the application or inventing cycles.

        Args:
            reason (str): Nonempty coverage or readiness failure explanation.

        Raises:
            ValueError: If the failure has no explanation.
        """
        if not reason.strip():
            raise ValueError("Coverage loss requires a reason")
        if self._state in {RunState.STOPPED, RunState.STOPPING}:
            return
        if self._state != RunState.DEGRADED:
            self._gaps += 1
        self._state = RunState.DEGRADED
        self._reason = reason

    def recover(self, readiness: Readiness) -> None:
        """Open a new coverage segment only after readiness is re-established.

        Args:
            readiness (Readiness): Fresh results, including verified camera binding.

        Raises:
            RuntimeError: If the run is stopped, jobs remain, or prerequisites are missing.
        """
        if self._state != RunState.DEGRADED:
            raise RuntimeError("Only an active degraded run can recover")
        if self._pending:
            raise RuntimeError("Settle pending jobs before opening another coverage segment")
        if readiness.blockers:
            raise RuntimeError(f"Recovery unavailable: {', '.join(readiness.blockers)}")
        self._segment += 1
        self._segment_cycle = 0
        self._last_frame, self._last_timestamp = -1, -1.0
        self._state = RunState.RECOVERING
        self._reason = "Waiting for a fresh complete cycle after coverage gap"

    def request_stop(self, now: float) -> None:
        """Stop intake immediately, allowing already accepted jobs to finalize.

        Args:
            now (float): Current monotonic time.

        Raises:
            ValueError: If time is invalid.
        """
        self._validate_time(now)
        if self._state in {RunState.STOPPED, RunState.STOPPING}:
            return
        self._state = RunState.STOPPING
        self._reason = "Finalizing accepted cycles"
        self._deadline = now + self._stop_timeout

    def finish_stop(self, now: float, *, source_quiescent: bool) -> bool:
        """Finalize Stop when workers drain, or explicitly fail jobs at the deadline.

        Args:
            now (float): Current monotonic time.
            source_quiescent (bool): No further source callbacks/events can be admitted.

        Returns:
            bool: Whether the run is stopped, including deadline-failed jobs.

        Raises:
            RuntimeError: If Stop was not requested.
            ValueError: If time is invalid.
        """
        self._validate_time(now)
        if self._state == RunState.STOPPED:
            return True
        if self._state != RunState.STOPPING or self._deadline is None:
            raise RuntimeError("Request Stop before finalizing it")
        expired = now >= self._deadline
        if not expired and (self._pending or not source_quiescent):
            return False
        self._stop_timed_out = expired and (bool(self._pending) or not source_quiescent)
        self._uninspected += len(self._pending)
        self._pending.clear()
        self._state = RunState.STOPPED
        self._reason = "Stopped; finalization deadline exceeded" if self._stop_timed_out else "Stopped"
        return True

    @staticmethod
    def _validate_time(now: float) -> None:
        if isinstance(now, bool) or not isfinite(now) or now < 0:
            raise ValueError("Monotonic time must be finite and nonnegative")
