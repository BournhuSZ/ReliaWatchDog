# RDT monitor architecture at the migration baseline

Inspected: 2026-10-30, revision `3c2e393e`. Source review only; no fresh runtime or
hardware qualification is claimed. This map supports the
[QML migration plan](RDT_PySide6_QML_Migration_Spec.md).
For subsequent implemented changes and fresh test results, see the
[migration progress record](QML_Migration_Progress.md).

## Scope and ownership

The relevant application is the standalone `application/rdt_monitor` package, not
Anomalib Studio. Its `rdt-monitor` entry point calls `rdt_monitor.app:main`, creates
a PyQt6 `QApplication` and `MainWindow`, and loads package QSS. The project requires
Python `>=3.11,<3.15`, NumPy, OpenCV headless and PyQt6. OpenVINO is an optional
inference extra. Training uses a separately selected interpreter.

Paths below are relative to `application/rdt_monitor/src/rdt_monitor`.

| Responsibility | Current owner | Migration disposition |
| --- | --- | --- |
| Main UI, coordination and bootstrap | `app.py`: `MainWindow`, `main` | Extract coordination; port binding; replace presentation incrementally |
| Camera open/read/close and preview | `camera.py`: `CameraDevice`, `create_camera`, `CameraPreview`, `CameraFrame`, `CameraStatus` | Keep worker and wrapper contract |
| Camera-specific implementation | Repository `tools/keyence_nr_cm01_api.py`, included by monitor `hatch_build.py` | Keep backend fallbacks and packaged copy |
| Marker processing on every camera frame | `camera.py`: `MarkerTrial` | Keep before preview replacement |
| Marker regions/appearance and replay | `replay.py`: `Region`, `MarkerAppearance`, `ReplayRecipe`, `ReplayPipeline` | Keep recognition and source behavior |
| Two-marker cycle state | `synchronization.py`: `CycleSynchronizer`; `domain.py` records/enums | Keep transitions and debounce/coverage semantics |
| Run mode/state and cycle accounting | `lifecycle.py`: `RunLifecycle`, `CycleTicket`, `RunSnapshot`, `Readiness` | Keep authoritative; expose snapshots |
| Recipe schema, bundles, threshold and setup snapshots | `recipes.py`: `StoredRecipe`, `RecipeDetector`, save/load helpers | Keep file formats, path confinement, hashes and verdict logic |
| Runtime inference and offline jobs | `detection.py`: `Predictor`, `_OpenVINOModel`, `inspect_image`, `InspectionWorker`, `ImageImportWorker` | Keep preprocessing and output contract |
| Live inspection and per-cycle file evidence | `live.py`: `LiveInspection`, `CycleDetection` | Keep bounded worker and staged commit |
| Candidate collection and manifest validation | `collection.py`: `CandidateCollection`, records and file helpers | Keep exact-frame/crop integrity |
| Model fit/export in separate process | `training.py`, launched with `QProcess` from `app.py` | Keep explicit training workflow and separate environment |
| Setup / ROI geometry editor | `app.py`: `ReplaySettings`, `RegionEditor`, `LiveView` | Extract reusable validation; replace visual editor late |
| Offline inspection / collection-review-training UI | `app.py`: `OfflineInspectionDialog`, `TrainingCollectionDialog` | Preserve workflow while migrating each view |
| Log, latest verdict, defect latch, ROI rendering | `MainWindow` poll/event/result callbacks | Move reduction/bookkeeping to controller; expose Qt models/images |
| Display mode | `QSettings("Anomalib", "RDT Reliability Monitor")`, QSS | Preserve preference keys; replace QML theme independently |

## Implemented paths

```text
Load recipe -> preserve current specimen -> frozen-camera setup -> confirm
  -> save hashed run setup -> start run / attach MarkerTrial

CameraPreview worker owns camera reads
  -> MarkerTrial: classify both regions -> CycleSynchronizer -> bounded SyncResult events
  -> exact accepted cycle image callback -> LiveInspection job queue
  -> latest-frame preview mailbox (capacity one)

LiveInspection worker: load/verify export -> inspect exact ROI -> write evidence
  -> bounded CycleDetection results

MainWindow GUI timer (~33 ms): drain source events -> admit lifecycle tickets
  -> settle matching inspection results -> render counters/verdict/log/ROI
```

Inspection callback intake happens in the source worker; lifecycle ticket admission and
settlement happen in the GUI coordinator. Preserve the order of source-event consumption
and result settlement when extracting it. There must be one consumer of each queue.

Counting-only recipes produce observed/uninspected counts without a detector. Replay
uses its own source worker and does not create live inspection evidence. Camera and
replay ownership are mutually exclusive. Offline inspection is a separate background
job and cannot update the live run. Collection saves candidates and hashes; explicit
review/undamaged confirmation and Train launch the separate Anomalib process. Export
does not automatically activate a recipe.

## Existing state and validity

`RunState`: Stopped, Ready, Running, Degraded, Recovering, Stopping. `RunMode` separates
Development, Unqualified live inspection and readiness-gated Inspection. Production
readiness checks must remain enforced.

`InspectionOutcome`: No defect detected, Needs review, Defect detected, Uninspected.
`RecipeDetector.verdict` compares float32 scores against an inclusive threshold/review
band. Evidence success affects whether a cycle counts as inspected and whether its
displayed verdict is valid. Model-detected defects also feed a separate latched alarm.
Camera health, synchronization and run health are independent of the latest verdict.

## UI coupling to extract

`MainWindow._configure_marker_trial` validates setup, saves run configuration and
creates workers/run state. `_event` admits tickets and appends position history.
`_poll_live_inspection` matches results, settles tickets, updates latch and evidence
status, and renders the ROI. `_finalize_marker_stop` drains source/result queues and
coordinates deadline/cancellation. `_poll_camera`, `poll`, `disconnect_camera` and
`closeEvent` coordinate lifecycle and resource release. These are the main controller
extraction seams; they currently mix orchestration with widget mutation.

`ReplaySettings` mixes candidate configuration, recipe I/O and confirmation with
Widgets. `TrainingCollectionDialog` mixes workflow state, validation, polling and
`QProcess` orchestration with controls. These require dedicated later extraction,
rather than replacing them with incomplete QML placeholders.

## Persistence and limitations

Recipes and detector bundles are file-based. Run setup snapshots link model, recipe,
specimen and marker setup. Live evidence stages source/ROI/map PNGs and `result.json`,
then renames the directory into a verdict group. Failure records, model/setup hashes
and a per-run evidence budget exist. Collection manifests and image hashes are separate.
The event/inspection histories are bounded to 500 entries and memory-only.

There is no station SQLite incident/audit store, reboot run recovery, retention service,
authenticated supervisor workflow or implemented controller trigger. Advanced recipe
editing currently requires no login. Stop/drain is asynchronous with a three-second
development deadline. Blocked camera drivers cannot be forcibly interrupted; daemon
jobs may be abandoned at exit. Automatic hardware recovery is not enabled for generic
camera indices. Existing physical-camera checks do not establish production acceptance.

## Verification anchors

The monitor has its own unit suite and native Qt integration tests under
`application/rdt_monitor/tests`. Important areas include all-frame recognition before
preview replacement, exact cycle pixels, review boundaries, evidence failure, path
confinement, stop deadlines, late-result rejection, recipe compatibility and specimen
setup, collection tampering, and Widgets workflows. See the
[operator guide](../application/rdt_monitor/README.md) for historical validation reports
and the [rollout plan](Rollout%20Plan.md) for station qualification still required.
