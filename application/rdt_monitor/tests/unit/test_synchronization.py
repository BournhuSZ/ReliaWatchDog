from math import inf, nan

import pytest

from rdt_monitor.domain import MarkerState, Observation, SyncConfig, SyncState
from rdt_monitor.synchronization import CycleSynchronizer


@pytest.fixture
def tracker():
    return CycleSynchronizer(SyncConfig(stable_frames=2, max_gap_seconds=0.5, cycle_timeout_seconds=3.0))


def feed(tracker, markers, *, position=None, start=0):
    return [
        tracker.observe(
            Observation(
                index,
                index / 10,
                marker,
                marker == MarkerState.PRESENT if position is None else position,
                position_marker=marker if position is None else None,
            )
        )
        for index, marker in enumerate(markers, start=start)
    ]


def test_present_at_startup_does_not_count(tracker):
    results = feed(tracker, [MarkerState.PRESENT] * 8, position=True)
    assert all(result.observed_cycles == 0 and not result.inspection_ready for result in results)
    assert tracker.state == SyncState.WAITING


def test_bending_cycle_requires_both_marks_to_disappear_and_return(tracker):
    visible = feed(tracker, [MarkerState.PRESENT] * 2, position=True)
    assert visible[-1].observed_cycles == 0
    feed(tracker, [MarkerState.ABSENT] * 2, start=2)
    start_returned = feed(tracker, [MarkerState.PRESENT] * 2, start=4, position=False)
    assert start_returned[-1].observed_cycles == 0
    assert not start_returned[-1].inspection_ready
    both_returned = feed(tracker, [MarkerState.PRESENT] * 6, start=6, position=True)
    assert both_returned[-1].observed_cycles == 1
    assert sum(result.cycle_started for result in both_returned) == 1
    assert sum(result.inspection_ready for result in both_returned) == 1


def test_start_mark_alone_cannot_count_a_bending_cycle(tracker):
    feed(tracker, [MarkerState.PRESENT] * 2, position=True)
    feed(tracker, [MarkerState.ABSENT] * 2, start=2, position=True)
    returned = feed(tracker, [MarkerState.PRESENT] * 2, start=4, position=True)
    assert returned[-1].observed_cycles == 0
    assert not returned[-1].inspection_ready


def test_startup_absence_then_visibility_is_not_an_observed_full_cycle(tracker):
    feed(tracker, [MarkerState.ABSENT] * 2)
    returned = feed(tracker, [MarkerState.PRESENT] * 2, start=2, position=True)
    assert returned[-1].observed_cycles == 0
    assert not returned[-1].inspection_ready


@pytest.mark.parametrize("initial_marker", [MarkerState.PRESENT, MarkerState.ABSENT])
def test_startup_wait_does_not_expire_before_first_validated_cycle(tracker, initial_marker):
    waiting = feed(tracker, [initial_marker] * 100, position=True)
    assert all(result.state == SyncState.WAITING for result in waiting)
    assert all(result.observed_cycles == 0 and not result.inspection_ready for result in waiting)
    feed(tracker, [MarkerState.PRESENT] * 2, start=100)
    feed(tracker, [MarkerState.ABSENT] * 2, start=102)
    started = feed(tracker, [MarkerState.PRESENT] * 2, start=104)
    assert started[-1].cycle_started
    assert started[-1].observed_cycles == 1
    assert started[-1].inspection_ready
    assert started[-1].state == SyncState.TRACKING


def test_persistent_marker_starts_once_and_requires_rearming(tracker):
    absent, present = MarkerState.ABSENT, MarkerState.PRESENT
    results = feed(
        tracker,
        [present, present, absent, absent, present, present, present, present, absent, absent, present, present],
    )
    assert [result.frame_id for result in results if result.cycle_started] == [5, 11]
    assert results[-1].observed_cycles == 2


def test_debounce_rejects_single_frame_changes(tracker):
    absent, present = MarkerState.ABSENT, MarkerState.PRESENT
    results = feed(tracker, [present, present, absent, present, absent, present, absent])
    assert all(not result.cycle_started for result in results)


def test_inspection_waits_for_visual_position_and_emits_once(tracker):
    feed(tracker, [MarkerState.PRESENT] * 2 + [MarkerState.ABSENT] * 2)
    results = feed(tracker, [MarkerState.PRESENT] * 2, start=4, position=False)
    assert not results[-1].cycle_started and not results[-1].inspection_ready
    assert not tracker.observe(Observation(6, 0.6, MarkerState.PRESENT, True)).inspection_ready
    assert tracker.observe(Observation(7, 0.7, MarkerState.PRESENT, True)).inspection_ready
    assert not tracker.observe(Observation(8, 0.8, MarkerState.PRESENT, True)).inspection_ready


@pytest.mark.parametrize(
    "observation, reason",
    [
        (Observation(3, 0.2, MarkerState.PRESENT, True), "coverage gap"),
        (Observation(2, 1.0, MarkerState.PRESENT, True), "timing gap"),
        (Observation(2, 0.1, MarkerState.PRESENT, True), "nonmonotonic"),
        (Observation(2, nan, MarkerState.PRESENT, True), "Invalid"),
    ],
)
def test_coverage_fault_latches_until_new_run(tracker, observation, reason):
    feed(tracker, [MarkerState.ABSENT] * 2)
    result = tracker.observe(observation)
    assert result.state == SyncState.LOST
    assert reason in result.reason
    assert not result.inspection_ready and not result.cycle_started
    assert tracker.observe(Observation(4, 1.1, MarkerState.ABSENT, True)).state == SyncState.LOST


def test_overdue_marker_faults(tracker):
    started = feed(tracker, [MarkerState.PRESENT] * 2 + [MarkerState.ABSENT] * 2 + [MarkerState.PRESENT] * 2)
    assert started[-1].cycle_started
    results = feed(tracker, [MarkerState.ABSENT] * 32, start=6)
    assert results[-1].state == SyncState.LOST
    assert "overdue" in results[-1].reason


@pytest.mark.parametrize("uncertain_role", [0, 1])
def test_transition_uncertainty_neither_faults_nor_counts_as_absence(tracker, uncertain_role):
    feed(tracker, [MarkerState.PRESENT] * 2)
    markers = [MarkerState.PRESENT, MarkerState.PRESENT]
    markers[uncertain_role] = MarkerState.AMBIGUOUS
    for index in range(2, 5):
        result = tracker.observe(
            Observation(index, index / 10, markers[0], markers[1] == MarkerState.PRESENT, markers[1])
        )
        assert result.state == SyncState.WAITING
        assert not result.cycle_started and not result.inspection_ready
    returned = feed(tracker, [MarkerState.PRESENT] * 2, start=5)
    assert returned[-1].observed_cycles == 0
    feed(tracker, [MarkerState.ABSENT] * 2, start=7)
    completed = feed(tracker, [MarkerState.PRESENT] * 2, start=9)
    assert completed[-1].observed_cycles == 1


@pytest.mark.parametrize("uncertain_role", [0, 1])
def test_unresolved_uncertainty_faults_within_cycle_deadline(tracker, uncertain_role):
    markers = [MarkerState.PRESENT, MarkerState.PRESENT]
    markers[uncertain_role] = MarkerState.AMBIGUOUS
    for index in range(33):
        result = tracker.observe(
            Observation(index, index / 10, markers[0], markers[1] == MarkerState.PRESENT, markers[1])
        )
    assert result.state == SyncState.LOST
    assert "ambiguous" in result.reason
    assert result.observed_cycles == 0
    assert not result.inspection_ready


def test_missing_first_return_faults_without_counting(tracker):
    feed(tracker, [MarkerState.PRESENT] * 2 + [MarkerState.ABSENT] * 2)
    waiting = feed(tracker, [MarkerState.PRESENT] * 32, start=4, position=False)
    assert waiting[-1].state == SyncState.LOST
    assert "overdue" in waiting[-1].reason
    assert waiting[-1].observed_cycles == 0


def test_each_completed_cycle_resets_the_deadline(tracker):
    feed(tracker, [MarkerState.PRESENT] * 2)
    for cycle in range(10):
        completed = feed(tracker, [MarkerState.ABSENT] * 2 + [MarkerState.PRESENT] * 2, start=2 + cycle * 4)
        assert completed[-1].observed_cycles == cycle + 1
        assert completed[-1].state == SyncState.TRACKING


@pytest.mark.parametrize("stable,gap,timeout", [(0, 0.5, 3), (True, 0.5, 3), (2, 0, 3), (2, inf, 3), (2, 1, 1)])
def test_invalid_configuration_is_rejected(stable, gap, timeout):
    with pytest.raises(ValueError):
        SyncConfig(stable, gap, timeout)
