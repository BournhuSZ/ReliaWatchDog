"""Typed observations and results for visual cycle synchronization."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite


class MarkerState(StrEnum):
    """Visual marker classification, including explicitly uncertain observations."""

    ABSENT = "absent"
    PRESENT = "present"
    AMBIGUOUS = "ambiguous"


class SyncState(StrEnum):
    """Synchronization health, independent of specimen condition."""

    WAITING = "Waiting for fresh marker transition"
    TRACKING = "Tracking"
    LOST = "Synchronization lost"


@dataclass(frozen=True)
class SyncConfig:
    """Explicit experimental parameters; these do not qualify a production recipe.

    Args:
        stable_frames (int): Consecutive observations required to accept a marker state.
        max_gap_seconds (float): Maximum permitted interval between observations.
        cycle_timeout_seconds (float): Maximum interval between completed cycles,
            for the first observed bend to return, or for continuous uncertainty to resolve.

    Raises:
        ValueError: If a limit is nonfinite, nonpositive, or inconsistent.
    """

    stable_frames: int
    max_gap_seconds: float
    cycle_timeout_seconds: float

    def __post_init__(self) -> None:
        if isinstance(self.stable_frames, bool) or not isinstance(self.stable_frames, int) or self.stable_frames < 1:
            raise ValueError("stable_frames must be a positive integer")
        limits = (self.max_gap_seconds, self.cycle_timeout_seconds)
        if not all(isfinite(value) and value > 0 for value in limits):
            raise ValueError("Synchronization time limits must be finite and positive")
        if self.cycle_timeout_seconds <= self.max_gap_seconds:
            raise ValueError("Cycle timeout must exceed the observation gap limit")


@dataclass(frozen=True)
class Observation:
    """One ordered visual observation of both marks on the bending specimen.

    Args:
        frame_id (int): Sequential source-frame identity starting at zero.
        timestamp (float): Monotonic replay-source or host-read time in seconds.
        marker (MarkerState): Start-mark classification.
        position_valid (bool): Whether the position mark is positively visible.
        position_marker (MarkerState | None): Full position-mark classification.
            When omitted, position_valid represents definite presence or absence.
    """

    frame_id: int
    timestamp: float
    marker: MarkerState
    position_valid: bool
    position_marker: MarkerState | None = None


@dataclass(frozen=True)
class SyncResult:
    """Synchronization result for one source frame, not a defect assessment.

    Args:
        frame_id (int): Source-frame identity.
        timestamp (float): Replay-source or host-read time in seconds.
        state (SyncState): Current synchronization health.
        observed_cycles (int): Number of complete two-mark disappearance/return cycles.
        cycle_started (bool): Compatibility event flag emitted when a full cycle completes.
        inspection_ready (bool): Whether both marks have returned for this completed cycle.
        reason (str): Operator-readable explanation, including a fault cause if present.
    """

    frame_id: int
    timestamp: float
    state: SyncState
    observed_cycles: int
    cycle_started: bool = False
    inspection_ready: bool = False
    reason: str = ""


@dataclass(frozen=True)
class RunRecord:
    """Immutable development-run identity; it does not authorize production monitoring.

    Args:
        run_id (str): Unique run identifier.
        specimen_id (str): Operator-entered replay label or explicit camera-trial label.
        recipe_version (str): Frozen unqualified settings revision.
        source (str): Recording path, synthetic fixture label, or generic camera index label.
        started_at (datetime): UTC wall-clock start time.
    """

    run_id: str
    specimen_id: str
    recipe_version: str
    source: str
    started_at: datetime


@dataclass(frozen=True)
class InspectionRecord:
    """Unknown assessment associated with an observed inspection position.

    Args:
        run_id (str): Owning replay or camera-trial run.
        cycle (int): Visually observed cycle identity within the run.
        frame_id (int): Source frame at the observed position.
        timestamp (float): Replay-source or host-read time in seconds.
        reason (str): Why no valid specimen assessment exists.
    """

    run_id: str
    cycle: int
    frame_id: int
    timestamp: float
    reason: str = "Unknown: defect detector unavailable; evidence not recorded"
