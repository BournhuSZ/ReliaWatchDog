# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Controller regressions independent of Qt, hardware and trained-model quality."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from rdt_monitor.controller import MonitorController
from rdt_monitor.domain import RunRecord, SyncResult, SyncState
from rdt_monitor.lifecycle import InspectionOutcome, Readiness, RunMode, RunState
from rdt_monitor.live import CycleDetection
from rdt_monitor.recipes import RecipeDetector
from rdt_monitor.replay import Region


def record(name="run-one"):
    return RunRecord(name, "specimen", "recipe-v1", "camera", datetime.now(UTC))


def cycle(number, *, frame_id=None, timestamp=None):
    return SyncResult(
        frame_id if frame_id is not None else number * 10,
        timestamp if timestamp is not None else float(number),
        SyncState.TRACKING,
        number,
        True,
        True,
        "Cycle complete",
    )


@pytest.fixture
def worker():
    binding = RecipeDetector(f"detector-{'c' * 32}/model.xml", "a" * 64, "b" * 64, Region(0, 0, 1, 1), 0.5)
    calls = []
    return SimpleNamespace(binding=binding, finished=Event(), stop=lambda **kwargs: calls.append(kwargs), calls=calls)


@pytest.fixture
def controller(worker):
    instance = MonitorController(capacity=4, stop_timeout=3)
    instance.start(record(), mode=RunMode.EXPERIMENTAL_INSPECTION, readiness=Readiness(), worker=worker)
    return instance


def detection(event, score=0.1, *, evidence=Path("committed/result.json"), reason=""):
    return CycleDetection(event, SimpleNamespace(score=score) if score is not None else None, evidence, reason)


@pytest.mark.parametrize(
    ("score", "outcome"),
    [
        (0.1, InspectionOutcome.NO_DEFECT),
        (0.45, InspectionOutcome.NEEDS_REVIEW),
        (0.55, InspectionOutcome.NEEDS_REVIEW),
        (0.9, InspectionOutcome.DEFECT),
    ],
)
def test_settlement_uses_existing_review_band(controller, worker, score, outcome):
    event = cycle(1)
    history = controller.observe(event)
    assert history.run_id == "run-one"
    assert "pending" in history.reason
    update = controller.settle(worker, detection(event, score))
    assert update.ticket.frame_id == event.frame_id
    assert update.outcome == update.verdict == outcome
    assert update.evidence_saved and update.is_latest
    assert controller.lifecycle.snapshot.inspected == 1
    assert controller.tickets == {}


@pytest.mark.parametrize("evidence, reason", [(None, ""), (Path("grey/result.json"), "Evidence incomplete")])
def test_evidence_failure_preserves_defect_latch_but_never_valid_verdict(controller, worker, evidence, reason):
    event = cycle(1)
    controller.observe(event)
    update = controller.settle(worker, detection(event, 0.9, evidence=evidence, reason=reason))
    assert update.outcome == InspectionOutcome.DEFECT
    assert update.verdict == InspectionOutcome.UNINSPECTED
    assert not update.evidence_saved
    assert controller.defects == controller.lifecycle.snapshot.defects == 1
    snapshot = controller.lifecycle.snapshot
    assert (snapshot.inspected, snapshot.uninspected, snapshot.pending) == (0, 1, 0)
    assert snapshot.state == RunState.DEGRADED
    normal = cycle(2)
    controller.observe(normal)
    controller.settle(worker, detection(normal))
    assert controller.defects == 1


def test_overtaking_completion_does_not_regress_latest_cycle(controller, worker):
    events = [cycle(number) for number in range(1, 4)]
    for event in events:
        controller.observe(event)
    assert controller.settle(worker, detection(events[2])).is_latest
    assert not controller.settle(worker, detection(events[0], 0.9)).is_latest
    assert not controller.settle(worker, detection(events[1], 0.51)).is_latest
    assert controller.latest_cycle == 3
    assert controller.defects == 1
    assert controller.lifecycle.snapshot.inspected == 3


@pytest.mark.parametrize("change", [{"timestamp": 9.0}, {"observed_cycles": 9}, {"frame_id": 11}])
def test_mismatched_completion_does_not_consume_valid_ticket(controller, worker, change):
    event = cycle(1)
    controller.observe(event)
    with pytest.raises(ValueError, match="no matching cycle ticket"):
        controller.settle(worker, detection(replace(event, **change)))
    assert controller.lifecycle.snapshot.pending == 1
    controller.settle(worker, detection(event))
    with pytest.raises(ValueError, match="no matching cycle ticket"):
        controller.settle(worker, detection(event))


def test_old_worker_with_reused_frame_identity_cannot_settle_new_run(controller, worker):
    event = cycle(1)
    controller.observe(event)
    controller.settle(worker, detection(event))
    controller.lifecycle.request_stop(1)
    worker.finished.set()
    controller.finish_stop(1, source_quiescent=True)
    new_worker = SimpleNamespace(binding=worker.binding, finished=Event(), stop=lambda **kwargs: None)
    controller.start(record("run-two"), mode=RunMode.EXPERIMENTAL_INSPECTION, readiness=Readiness(), worker=new_worker)
    controller.observe(event)
    with pytest.raises(ValueError, match="no matching cycle ticket"):
        controller.settle(worker, detection(event))
    assert controller.lifecycle.snapshot.pending == 1
    assert controller.defects == 0
    assert controller.settle(new_worker, detection(event)).ticket.run_id == "run-two"


def test_overflow_settles_pending_once_and_rejects_late_results(controller, worker):
    for number in range(1, 4):
        controller.observe(cycle(number))
    latest = controller.fail_pending("Inspection result backlog overflow; coverage unavailable")
    assert latest.cycle == controller.latest_cycle == 3
    assert controller.lifecycle.snapshot.uninspected == 3
    assert controller.lifecycle.snapshot.pending == 0
    assert controller.fail_pending(controller.fault) is None
    with pytest.raises(ValueError, match="no matching cycle ticket"):
        controller.settle(worker, detection(cycle(1)))


def test_stop_drains_accepted_event_and_preserves_result(controller, worker):
    controller.lifecycle.request_stop(1)
    event = cycle(1)
    controller.observe(event, source_draining=True)
    assert controller.finish_stop(2, source_quiescent=False) is None
    update = controller.settle(worker, detection(event))
    worker.finished.set()
    stopped = controller.finish_stop(2, source_quiescent=True)
    assert stopped.snapshot.state == RunState.STOPPED
    assert stopped.snapshot.inspected == 1
    assert stopped.timed_out_ticket is None
    assert update.verdict == InspectionOutcome.NO_DEFECT
    assert controller.worker is None
    assert worker.calls == [{"cancel": True}]


def test_deadline_invalidates_pending_and_gates_restart_until_worker_exits(controller, worker):
    controller.observe(cycle(1))
    controller.lifecycle.request_stop(1)
    assert controller.finish_stop(2, source_quiescent=False) is None
    stopped = controller.finish_stop(4, source_quiescent=False)
    assert stopped.snapshot.stop_timed_out
    assert stopped.snapshot.uninspected == 1
    assert stopped.timed_out_ticket.cycle == 1
    assert controller.retired_worker is worker
    assert controller.worker is None
    with pytest.raises(RuntimeError, match="Previous detector"):
        controller.start(record("run-two"), mode=RunMode.DEVELOPMENT, readiness=Readiness())
    with pytest.raises(ValueError, match="no matching cycle ticket"):
        controller.settle(worker, detection(cycle(1)))
    worker.finished.set()
    controller.start(record("run-two"), mode=RunMode.DEVELOPMENT, readiness=Readiness())
    assert controller.tickets == {} and controller.inspections == []
    assert controller.defects == controller.latest_cycle == 0
    assert controller.fault == ""


def test_recovery_segment_preserves_source_event_and_cumulative_ticket(controller, worker):
    first = cycle(1)
    controller.observe(first)
    controller.settle(worker, detection(first))
    controller.lifecycle.coverage_lost("Observation gap")
    controller.lifecycle.recover(Readiness(True, True, True, True, True))
    returned = cycle(1, frame_id=20, timestamp=2)
    controller.observe(returned)
    update = controller.settle(worker, detection(returned))
    assert update.ticket.cycle == 2
    assert update.ticket.segment == 2
    assert update.detection.event.observed_cycles == 1


def test_counting_history_is_bounded_and_never_inspected():
    instance = MonitorController()
    instance.start(record(), mode=RunMode.DEVELOPMENT, readiness=Readiness())
    for number in range(1, 503):
        instance.observe(cycle(number))
    assert len(instance.inspections) == 500
    assert instance.inspections[0].cycle == 3
    assert "counting-only" in instance.inspections[-1].reason
    snapshot = instance.lifecycle.snapshot
    assert (snapshot.observed, snapshot.inspected, snapshot.uninspected) == (502, 0, 502)


def test_failed_production_start_cannot_replace_existing_controller_state():
    instance = MonitorController()
    with pytest.raises(RuntimeError, match="Inspection unavailable"):
        instance.start(record(), mode=RunMode.INSPECTION, readiness=Readiness())
    assert instance.run_record is None
    assert instance.lifecycle.snapshot.state == RunState.STOPPED
