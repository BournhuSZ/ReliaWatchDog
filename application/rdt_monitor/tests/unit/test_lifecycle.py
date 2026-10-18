# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from rdt_monitor.domain import RunRecord
from rdt_monitor.lifecycle import InspectionOutcome, Readiness, RunLifecycle, RunMode, RunState


@pytest.fixture
def record():
    return RunRecord("run-one", "specimen-one", "recipe-v1", "camera-one", datetime.now(UTC))


@pytest.fixture
def ready():
    return Readiness(True, True, True, True, True)


@pytest.fixture
def run():
    return RunLifecycle(capacity=2, stop_timeout=3)


def test_inspection_start_is_fail_closed(run, record):
    with pytest.raises(RuntimeError, match="Approved recipe"):
        run.start(record, mode=RunMode.INSPECTION, readiness=Readiness())
    assert run.snapshot.state == RunState.STOPPED
    assert run.snapshot.record is None
    assert len(Readiness().blockers) == 5


@pytest.mark.parametrize(
    "field", ["camera_verified", "recipe_approved", "detector_ready", "evidence_ready", "authorization_ready"]
)
def test_each_readiness_check_is_required(run, record, ready, field):
    with pytest.raises(RuntimeError, match="Inspection unavailable"):
        run.start(record, mode=RunMode.INSPECTION, readiness=replace(ready, **{field: False}))
    assert run.snapshot.record is None


def test_development_counts_are_never_inspected(run, record):
    run.start(record, mode=RunMode.DEVELOPMENT, readiness=Readiness())
    assert run.snapshot.state == RunState.READY
    run.observe_cycle(1, 5, 1.0)
    run.observe_cycle(2, 10, 2.0)
    snapshot = run.snapshot
    assert snapshot.state == RunState.RUNNING
    assert (snapshot.observed, snapshot.inspected, snapshot.uninspected, snapshot.pending) == (2, 0, 2, 0)


def test_exact_cycle_identity_and_once_only_settlement(run, record, ready):
    run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    first = run.observe_cycle(1, 10, 1.0)
    assert first.run_id == record.run_id and first.frame_id == 10 and first.timestamp == 1.0
    assert run.snapshot.pending == 1
    with pytest.raises(ValueError, match="not pending"):
        run.settle(replace(first, frame_id=11), InspectionOutcome.NO_DEFECT, evidence_saved=True)
    run.settle(first, InspectionOutcome.DEFECT, evidence_saved=True)
    with pytest.raises(ValueError, match="not pending"):
        run.settle(first, InspectionOutcome.NO_DEFECT, evidence_saved=True)
    second = run.observe_cycle(2, 20, 2.0)
    run.settle(second, InspectionOutcome.NO_DEFECT, evidence_saved=True)
    assert run.snapshot.defects == 1
    assert (run.snapshot.inspected, run.snapshot.pending) == (2, 0)


@pytest.mark.parametrize("outcome", list(InspectionOutcome))
def test_missing_evidence_is_not_a_success(run, record, ready, outcome):
    run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    ticket = run.observe_cycle(1, 10, 1.0)
    run.settle(ticket, outcome, evidence_saved=False)
    assert run.snapshot.inspected == 0
    assert run.snapshot.uninspected == 1
    assert run.snapshot.state == RunState.DEGRADED
    assert run.snapshot.defects == (1 if outcome == InspectionOutcome.DEFECT else 0)


def test_bounded_backlog_counts_overload_as_uninspected(run, record, ready):
    run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    run.observe_cycle(1, 1, 1.0)
    run.observe_cycle(2, 2, 2.0)
    with pytest.raises(RuntimeError, match="capacity exceeded"):
        run.observe_cycle(3, 3, 3.0)
    assert run.snapshot.state == RunState.DEGRADED
    assert (run.snapshot.observed, run.snapshot.pending, run.snapshot.uninspected) == (3, 2, 1)


def test_gap_recovery_keeps_identity_and_counts_only_fresh_cycles(run, record, ready):
    run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    first = run.observe_cycle(1, 10, 1.0)
    run.settle(first, InspectionOutcome.NO_DEFECT, evidence_saved=True)
    run.coverage_lost("Camera disconnected")
    run.coverage_lost("Camera still disconnected")
    assert run.snapshot.gaps == 1
    with pytest.raises(RuntimeError, match="not accepting"):
        run.observe_cycle(2, 11, 2.0)
    with pytest.raises(RuntimeError, match="Recovery unavailable"):
        run.recover(Readiness())
    run.recover(ready)
    assert run.snapshot.state == RunState.RECOVERING
    assert run.snapshot.record == record
    assert run.snapshot.segment == 2
    second = run.observe_cycle(1, 0, 20.0)
    assert second.cycle == 2 and second.segment == 2
    assert run.snapshot.observed == 2
    run.settle(second, InspectionOutcome.NO_DEFECT, evidence_saved=True)
    assert run.snapshot.inspected == 2


def test_recovery_requires_pending_work_to_settle(run, record, ready):
    run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    ticket = run.observe_cycle(1, 1, 1.0)
    run.coverage_lost("Inference unavailable")
    with pytest.raises(RuntimeError, match="pending jobs"):
        run.recover(ready)
    run.settle(ticket, InspectionOutcome.UNINSPECTED, evidence_saved=False)
    run.recover(ready)
    assert run.snapshot.segment == 2


def test_stop_rejects_intake_but_drains_accepted_jobs(run, record, ready):
    run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    ticket = run.observe_cycle(1, 1, 1.0)
    run.request_stop(10.0)
    assert run.snapshot.state == RunState.STOPPING
    with pytest.raises(RuntimeError, match="not accepting"):
        run.observe_cycle(2, 2, 2.0)
    with pytest.raises(RuntimeError, match="degraded"):
        run.recover(ready)
    assert not run.finish_stop(10.0, source_quiescent=True)
    run.settle(ticket, InspectionOutcome.DEFECT, evidence_saved=True)
    assert not run.finish_stop(10.1, source_quiescent=False)
    assert run.finish_stop(10.2, source_quiescent=True)
    assert run.snapshot.state == RunState.STOPPED
    assert run.snapshot.inspected == 1 and run.snapshot.defects == 1


def test_stop_deadline_explicitly_fails_unfinished_jobs(run, record, ready):
    run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    ticket = run.observe_cycle(1, 1, 1.0)
    run.request_stop(10)
    run.request_stop(12)
    assert not run.finish_stop(12.999, source_quiescent=False)
    assert run.finish_stop(13, source_quiescent=False)
    assert "deadline exceeded" in run.snapshot.reason
    assert run.snapshot.stop_timed_out
    assert run.snapshot.uninspected == 1 and run.snapshot.pending == 0
    with pytest.raises(ValueError, match="not pending"):
        run.settle(ticket, InspectionOutcome.NO_DEFECT, evidence_saved=True)
    with pytest.raises(RuntimeError, match="degraded"):
        run.recover(ready)


def test_deadline_fails_pending_jobs_even_when_source_is_quiescent(run, record, ready):
    run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    run.observe_cycle(1, 1, 1.0)
    run.request_stop(10)
    assert run.finish_stop(13, source_quiescent=True)
    assert run.snapshot.stop_timed_out
    assert run.snapshot.uninspected == 1


def test_delayed_poll_does_not_report_timeout_for_already_drained_work(run, record):
    run.start(record, mode=RunMode.DEVELOPMENT, readiness=Readiness())
    run.request_stop(10)
    assert run.finish_stop(13, source_quiescent=True)
    assert not run.snapshot.stop_timed_out
    assert run.snapshot.reason == "Stopped"


def test_only_development_source_events_can_drain_after_stop(run, record):
    run.start(record, mode=RunMode.DEVELOPMENT, readiness=Readiness())
    run.request_stop(10)
    run.observe_cycle(1, 1, 1.0, source_draining=True)
    assert run.snapshot.state == RunState.STOPPING
    assert run.snapshot.observed == 1 and run.snapshot.uninspected == 1
    assert run.finish_stop(10, source_quiescent=True)
    with pytest.raises(RuntimeError, match="not accepting"):
        run.observe_cycle(2, 2, 2.0, source_draining=True)


def test_inspection_cannot_bypass_stop_with_development_drain(run, record, ready):
    run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    run.request_stop(10)
    with pytest.raises(RuntimeError, match="not accepting"):
        run.observe_cycle(1, 1, 1.0, source_draining=True)


def test_new_start_resets_counters_and_requires_new_identity(run, record, ready):
    run.start(record, mode=RunMode.DEVELOPMENT, readiness=Readiness())
    run.observe_cycle(1, 1, 1.0)
    with pytest.raises(RuntimeError, match="Stop and finalize"):
        run.start(replace(record, run_id="other"), mode=RunMode.INSPECTION, readiness=ready)
    run.request_stop(10)
    assert run.finish_stop(10, source_quiescent=True)
    with pytest.raises(ValueError, match="new run ID"):
        run.start(record, mode=RunMode.INSPECTION, readiness=ready)
    run.start(replace(record, run_id="new-run"), mode=RunMode.INSPECTION, readiness=ready)
    assert (run.snapshot.observed, run.snapshot.inspected, run.snapshot.uninspected) == (0, 0, 0)


@pytest.mark.parametrize("cycle,frame,time", [(1, -1, 1), (2, 1, 1), (True, 1, 1), (1, 1, float("nan"))])
def test_invalid_cycle_does_not_change_accounting(run, record, cycle, frame, time):
    run.start(record, mode=RunMode.DEVELOPMENT, readiness=Readiness())
    before = run.snapshot
    with pytest.raises(ValueError, match="identity"):
        run.observe_cycle(cycle, frame, time)
    assert run.snapshot == before


@pytest.mark.parametrize("capacity,timeout", [(0, 3), (True, 3), (1, 0), (1, float("inf")), (1, True)])
def test_invalid_runtime_limits(capacity, timeout):
    with pytest.raises(ValueError):
        RunLifecycle(capacity=capacity, stop_timeout=timeout)
