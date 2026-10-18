"""Deterministic, fail-closed synchronization driven by visible marker transitions."""

from math import isfinite

from rdt_monitor.domain import MarkerState, Observation, SyncConfig, SyncResult, SyncState


class CycleSynchronizer:
    """Count complete two-mark bending cycles and latch coverage faults.

    Both marks must first be visibly present. Each must then become stably absent
    before their stable joint return completes one cycle. Transitional uncertainty
    never establishes absence or return. A fresh instance is required after a fault.

    Args:
        config (SyncConfig): Explicit debounce, continuity, and timeout limits.
    """

    def __init__(self, config: SyncConfig) -> None:
        self.config = config
        self.state = SyncState.WAITING
        self.observed_cycles = 0
        self._last: Observation | None = None
        self._cycle_reference: float | None = None
        self._motion_started_at: float | None = None
        self._baseline_seen = False
        self._candidate: list[MarkerState | None] = [None, None]
        self._candidate_count = [0, 0]
        self._departed = [False, False]
        self._uncertain_since: list[float | None] = [None, None]
        self._fault = ""

    def observe(self, observation: Observation) -> SyncResult:
        """Consume a source observation without permitting silent resynchronization.

        Args:
            observation (Observation): Next frame in source order.

        Returns:
            SyncResult: Cycle/position events or a latched unknown-coverage fault.
        """
        if self.state == SyncState.LOST:
            return self._result(observation, reason=self._fault)
        if not isfinite(observation.timestamp) or observation.timestamp < 0 or observation.frame_id < 0:
            return self.invalidate(observation, "Invalid frame identity or timestamp")
        if self._last is not None:
            elapsed = observation.timestamp - self._last.timestamp
            if observation.frame_id != self._last.frame_id + 1:
                return self.invalidate(observation, "Frame coverage gap; a marker transition may be missing")
            if elapsed <= 0 or elapsed > self.config.max_gap_seconds:
                return self.invalidate(observation, "Observation timing gap or nonmonotonic source time")
        self._last = observation
        deadline_reference = self._cycle_reference if self._cycle_reference is not None else self._motion_started_at
        if (
            deadline_reference is not None
            and observation.timestamp - deadline_reference > self.config.cycle_timeout_seconds
        ):
            return self.invalidate(observation, "Bending cycle overdue")
        position = observation.position_marker
        if position is None:
            position = MarkerState.PRESENT if observation.position_valid else MarkerState.ABSENT
        markers = (observation.marker, position)
        for index, marker in enumerate(markers):
            if marker == MarkerState.AMBIGUOUS:
                self._candidate[index] = None
                self._candidate_count[index] = 0
                uncertain_since = self._uncertain_since[index]
                if uncertain_since is None:
                    uncertain_since = observation.timestamp
                    self._uncertain_since[index] = uncertain_since
                if observation.timestamp - uncertain_since > self.config.cycle_timeout_seconds:
                    return self.invalidate(observation, "Marker remains ambiguous or obscured; transition unresolved")
                continue
            self._uncertain_since[index] = None
            if marker == self._candidate[index]:
                self._candidate_count[index] += 1
            else:
                self._candidate[index] = marker
                self._candidate_count[index] = 1
            if self._baseline_seen and marker == MarkerState.ABSENT:
                if self._candidate_count[index] >= self.config.stable_frames:
                    self._departed[index] = True
                    if self._motion_started_at is None:
                        self._motion_started_at = observation.timestamp
        both_visible = all(
            marker == MarkerState.PRESENT and count >= self.config.stable_frames
            for marker, count in zip(markers, self._candidate_count, strict=True)
        )
        if not self._baseline_seen:
            if both_visible:
                self._baseline_seen = True
            return self._result(
                observation, reason="Waiting for both marks visible, then a full disappearance and return"
            )
        if both_visible and all(self._departed):
            self._departed = [False, False]
            self._motion_started_at = None
            self._cycle_reference = observation.timestamp
            self.observed_cycles += 1
            self.state = SyncState.TRACKING
            return self._result(
                observation, True, True, "Cycle complete / both marks returned; defect inspection unavailable"
            )
        if MarkerState.AMBIGUOUS in markers:
            self.state = SyncState.WAITING
            reason = "Mark transition uncertain / waiting for clear visibility; no cycle counted"
        elif self._motion_started_at is not None:
            self.state = SyncState.WAITING
            reason = "Bending / waiting for both marks to disappear and return"
        else:
            reason = "Both marks visible / waiting for bending"
        return self._result(observation, reason=reason)

    def invalidate(self, observation: Observation, reason: str) -> SyncResult:
        """Latch a fault until an operator explicitly begins a new run.

        Args:
            observation (Observation): Source identity associated with the fault.
            reason (str): Coverage or processing failure explanation.

        Returns:
            SyncResult: Lost synchronization with no valid inspection event.
        """
        self.state = SyncState.LOST
        self._fault = reason
        return self._result(observation, reason=reason)

    def _result(
        self,
        observation: Observation,
        cycle_started: bool = False,
        inspection_ready: bool = False,
        reason: str = "",
    ) -> SyncResult:
        return SyncResult(
            observation.frame_id,
            observation.timestamp,
            self.state,
            self.observed_cycles,
            cycle_started,
            inspection_ready,
            reason,
        )
