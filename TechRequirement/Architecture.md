# RDT Reliability Monitor Architecture

Status: agreed design baseline, 2026-10-14. This document specifies intended
behavior, not an implemented or qualified system. Visual requirements are in
[design direction.md](design%20direction.md).

The [rollout plan](Rollout%20Plan.md), recorded 2026-10-19, retains vision markers
for the Phase 1 pilot and defers read-only IPC/controller cycle and position
feedback integration to Phase 2. It does not authorize machine control or waive
the qualification gates below.

## 1. Scope And Authority

- Standalone, offline Windows application for one camera and one test station.
- Monitor the bending-machine PCB/cushion for visible damage or abnormal
  deformation; expected bending motion is normal, not itself an anomaly.
- Advisory only: monitor, alert, and record. No machine control or safety interlock.
- Consume Anomalib through existing APIs without modifying its core library.
- Multi-camera operation, machine control, logger integration, PDF reports, and
  touchscreen-specific interaction are outside version-one scope.

## 2. Technology Decisions And Qualification Gates

| Component | Decision | Constraint |
| --- | --- | --- |
| Language/environment | Python; manage the environment with `uv` | Pin a version supported by selected dependencies and the target PC |
| UI | PyQt6 Widgets, QSS, Qt property animations | Resolve GPL/commercial distribution obligations before release |
| Camera adapter | OpenCV is provisional | Verify NR-CM01 acquisition on the actual driver and target PC |
| Detection | Anomalib PatchCore primary; qualified classical methods where appropriate | No automatic classical fallback that reports normal after model failure |
| Persistence | SQLite plus evidence files | Isolate writes from capture, inference, and rendering |
| Configuration | Validated, versioned recipes and application settings | No unrestricted operator edits or fabricated production defaults |
| Packaging | PyInstaller is provisional | Reproducible offline installation; an application directory is acceptable |
| Acceleration | OpenVINO or ONNX Runtime only if needed and qualified | Validate exported-model results and target-PC performance |

The existing [camera wrapper](../tools/keyence_nr_cm01_api.py) probes generic
OpenCV device indices. It does not prove NR-CM01 USB/UVC compatibility, camera
identity, sustained frame rate, or production reliability.

## 3. Ownership And Data Flow

```text
CameraService: frame identity, timestamps, acquisition continuity
  +--> preview mailbox ------------------------------> UI thread
  +--> bounded synchronization path --> CycleSyncService
  |                                      |
  |                           cycle completion / inspection position
  |                                      v
  +--> bounded inspection candidates --> DetectionService --> result
  +--> bounded recording path ---------> StorageService       |
                                               ^              v
                                               +-------- IncidentManager
                                                            |
RunController / RecipeService / AuthorizationService <-------+
  +--> typed state updates --> UI and audit records
```

Preview, synchronization, inference, and recording have independent bounded
capacity and explicit failure accounting. A preview or inference dropping policy
must not discard the only marker transition before synchronization sees it.

| Owner | Responsibility |
| --- | --- |
| `RunController` | Run lifecycle, start eligibility, critical-fault handling, and confirmed restart |
| `CameraService` | Acquisition, connection health, frame identity, continuity reporting, preview reconnect |
| `CycleSyncService` | Marker recognition, debounce/rearming, observed-cycle count, inspection-position validation |
| `RecipeService` | Validated recipe versions, qualification status, immutable run snapshot |
| `DetectionService` | Qualified position-gated inference, scores, decision validity, source-aligned heatmap |
| `IncidentManager` | Group consecutive abnormal observations, latch alarms, separate acknowledgment/recovery/closure |
| `StorageService` | Pre-event buffer, post-event capture, evidence completion, SQLite queries, retention and reconciliation |
| `AuthorizationService` | Authenticated supervisor authorization at the application boundary and audit attribution |
| UI | Render state and submit commands; never own detection decisions or perform blocking work |

Use typed records rather than unrelated signal arguments or ad hoc dictionaries:
frame identity and timestamps; synchronization events; inspection requests and
results; run and recipe snapshots; incident/evidence state; health and audit events.
Preserve frame/run/cycle/recipe identifiers through all stages. Use monotonic time
for deadlines and durations and wall-clock timestamps for human-readable evidence.
Frame buffers must remain valid and unmodified while another worker consumes them.

## 4. Visible-Marker Cycle Synchronization

Operator clarification, 2026-10-15: Start and Position marks move and bend with
the specimen. Both disappear and return during one physical bending cycle.

1. Establish a debounced initial view of both marks. No external machine trigger
  is required for version one. Startup visibility or an initially absent mark's
  first appearance does not establish a completed cycle.
2. Each mark must become stably absent and then return. Their debounced joint
  visibility completes exactly one observed cycle. Departures and returns may be
  staggered; one mark moving alone cannot complete a cycle.
3. Holding both marks visible never adds cycles. After completion, both must leave
  and return again to complete the next cycle.
4. The returned Position mark validates the inspection position for that completed
  cycle. Inference requires the qualified visual position, never a fixed delay.
5. Both marker regions, recognition thresholds, debounce, pose/deformation tolerance,
   and expected-cycle timeout belong to the qualified recipe.
6. Brief uncertainty during bending waits for clear visibility; it is neither
  disappearance nor return and cannot produce a count or inspection event.
  Unresolved uncertainty, an overdue first return, or an overdue later cycle
  produces a synchronization fault and an unknown affected inspection, not normal.
7. Any acquisition or synchronization-processing gap that could hide a transition
   invalidates synchronization. Record the coverage gap and require recovery checks.
8. Label the counter **visually observed cycles**. Without an independent machine
   counter it cannot guarantee the machine's total physical cycle count.

The synchronization path must meet measured coverage and latency requirements.
Its bounded-buffer overflow must be observable; it may not silently skip marker
observations to preserve a misleading cycle count.

## 5. Recipes, Runs, And Detection

Each specimen type and camera/lighting/ROI configuration needs a versioned,
qualified inspection recipe. Validate using normal cycles and representative
defects, including expected motion and lighting variation. Define the detectable
defect classes and minimum sizes before selecting thresholds or score fusion.

A recipe records camera settings, marker/inspection regions, position criteria,
model identity/version, preprocessing, thresholds, any qualified classical logic
and fusion rules, timing limits, and qualification evidence. Raw classical and
PatchCore scores are not assumed interchangeable or calibrated.

Candidate training capture is separate from monitoring and fitting. For a new
specimen, collect an explicitly selected 20-30 source/ROI image pairs at distinct
completed-cycle positions, preserving exact frame, cycle, run, ROI and timestamp
identity. Reaching the image target stops collection, never confirms normality or
starts training. Require image review and explicit user confirmation that the
specimen remained undamaged, followed by an explicit Train action. Incomplete
collection or failed integrity checks cannot become training input. Train from a
frozen verified dataset snapshot into a new model version, never adapt a live
model to later damage. Such a normal-only baseline still needs independent
normal/defect validation and recipe qualification before production use.

Every monitoring session is an explicit test run with specimen ID, recipe version,
start/end timestamps, visually observed cycles, inspection outcomes, and coverage
gaps. Snapshot and lock the recipe for the run. Changing camera settings, lighting,
ROI, model, or thresholds requires ending the run and validating the new setup;
changes invalidate the prior setup's qualification.

An inspection is valid only when the required image, synchronization, position,
recipe, model, and deadline checks pass. Missed required inspections, model
failure, or stale results are unknown/unavailable, never normal. Associate heatmaps
with their exact source frame and ROI transform; do not overlay an old result on
an unrelated live frame.

## 6. State And Incident Contracts

Maintain independent state dimensions rather than one green/yellow/red flag:

| Dimension | Required distinctions |
| --- | --- |
| Monitoring health | Stopped, warming up, ready, monitoring, degraded, fault; expose camera and detector availability |
| Synchronization | Waiting for fresh transition, tracking, inspection position valid, lost/ambiguous |
| Specimen assessment | Unknown, normal, warning, anomaly; include source cycle and result age |
| Alarm | Active/latched, acknowledged, recovery observed, closed |
| Evidence | Pending, complete, incomplete/failed, protected |

Consecutive abnormal observations form one incident rather than one event per
frame. Alarm state stays latched until acknowledged. Acknowledgment records
operator awareness, not specimen recovery; recovery and incident closure are
separate transitions. Record all transitions and their associated run/cycle.
Recipe qualification must define warning/anomaly thresholds, grouping/recovery
criteria, and incident closure eligibility.

## 7. Evidence And Persistence

Every incident retains:

- Unannotated source image and a separate annotated image.
- Configured pre-event and post-event video, preserving available cycle context.
- Frame identifiers, timestamps, run/specimen/cycle identifiers, recipe/model
  versions, ROI, thresholds, scores, and relevant health/coverage state.
- Acknowledgment, recovery, closure, protection, and evidence-save status.

Only mark an incident successfully recorded when its database entry and all
required evidence have been saved. Until post-event capture and writes finish,
show pending. Missing footage or failed writes produce incomplete/failed evidence
with an explicit reason; never label a partial clip complete.

SQLite and filesystem writes are not a single atomic transaction. Track evidence
completion explicitly, finalize files before marking them available, and reconcile
unfinished records/files after restart. If storage itself is unavailable, display
the unsaved incident/fault without implying durable persistence.

Use a configurable quota and retention period. Automatically remove only expired,
unprotected, completed incidents. Never silently overwrite protected evidence.
If capacity cannot be reclaimed, raise a persistent storage fault and apply the
critical-fault/restart contract. Validate file destinations under the configured
storage root; specimen identifiers must not become unchecked filesystem paths.

## 8. Threading, Backpressure, And Recovery

- UI thread: widgets, rendering, and lightweight command/state handling only.
- Worker-owned services: acquisition, synchronization, inference, recording,
  persistence, and expensive queries; use `QObject` workers with appropriate
  `QThread` ownership and queued signals/slots at UI boundaries.
- Bound both payload buffers and notification traffic. Queued Qt signals alone
  do not provide backpressure; do not enqueue unbounded per-frame UI callbacks.
- Preview/inference may discard stale work, but a missed required inspection
  must be recorded as unknown. Never accumulate an increasing processing delay.
- Keep recording independent of inference throughput. Recording overflow or
  missing pre/post coverage creates a visible evidence fault.
- Camera preview may reconnect automatically. Camera loss, application restart,
  synchronization coverage loss, or another critical processing fault must not
  silently resume inspection. Require health checks and operator-confirmed restart.
- Shutdown must stop accepting new inspections, finish or explicitly mark pending
  evidence incomplete, close resources, and preserve unfinished run/incident state.
  Never block the UI indefinitely waiting for hardware or a worker.

## 9. Permissions And Audit

Routine operators can start/stop eligible runs, review evidence, and acknowledge
alarms. Supervisor-authenticated setup controls model selection, camera/lighting
settings, marker/inspection regions, ROI, thresholds, and recipe qualification.
Enforce authorization in services, not just by hiding widgets. Run without Windows
administrator privileges. Record who changed what and when, including old/new
recipe versions and qualification status. A hidden toggle is not authentication.

## 10. Proposed Application Boundaries

Keep the monitor in its own application package, separate from Anomalib core.
The exact package location and interfaces will be chosen during implementation;
this document does not create a project or prescribe unimplemented public APIs.

```text
rdt_monitor/
  ui/              Main window, live view, state panels, event table, setup
  core/            Run, recipe, synchronization, incident, authorization services
  acquisition/     Camera adapter and continuity tracking
  detection/       Anomalib integration and qualified classical methods
  storage/         SQLite, evidence files, recording, retention, audit persistence
  tests/           Deterministic replay, service/state tests, hardware acceptance
```

## 11. Qualification Inputs And Release Gates

The following are deliberately unset. Missing values or acceptance evidence keep
the affected recipe/system unqualified; example numbers are not production defaults.

| Required input or gate | Evidence needed |
| --- | --- |
| Defects and performance | Detectable classes/minimum sizes, measured missed-defect and false-alarm limits on representative data |
| Marker and inspection position | Marker geometry/region, transition rules, minimum observable duration, debounce, position tolerance, cycle timeout |
| Timing and throughput | Decision deadline, image resolution, capture rate, bounded-buffer capacities, result freshness, sustained target-PC measurements |
| Incident rules | Warning/anomaly calibration, grouping, recovery, and closure criteria |
| Evidence and retention | Pre/post durations, required image/clip quality, run length, event volume, quota, retention, disk-full behavior |
| Authentication | Offline identity provisioning, supervisor credential protection/recovery, audit access and retention |
| Display | Target resolution, Windows scaling, minimum usable window size, keyboard/mouse usability |
| Camera compatibility | Actual NR-CM01/driver acquisition, identity, frame timing, disconnect/reconnect and sustained recording tests |
| Deployment | Pinned environment, dependency/license review including PyQt6, clean offline Windows installation, packaged end-to-end acceptance |

Acceptance tests must cover marker persistence/startup/rearming, ambiguous/missed
markers and coverage gaps, position-gated inference, overload, stale heatmaps,
model failure without fallback, recipe locking/authorization, incident lifecycle,
partial evidence and disk-full handling, retention protection, restart/shutdown,
and UI responsiveness. Use deterministic recorded-cycle replay for repeatable
logic tests, followed by real hardware and packaged target-PC tests.
