# RDT QML migration: software release-candidate acceptance

Date: 2026-10-30. Scope: the standalone `application/rdt_monitor` GUI and its Qt
binding. The source specification in Downloads is unchanged. The user authorized
implementation after reviewing the revised repository-grounded plan.

## Implementation outcome

Every required GUI workflow has a QML implementation. There are no remaining
Widgets-only business dialogs in the QML command's operator flow. Native file
pickers still use Qt Widgets, and the complete Widgets command remains available
as the default/fallback. This is a software release candidate, not a qualified
physical station or an approved default-launcher cutover.

| Workflow | QML implementation | Preserved boundary |
| --- | --- | --- |
| Connection, preview and monitoring | `Main.qml`, `presentation.py` | One controller consumes camera/replay queues; preview replacement cannot replace cycle events |
| Confirmed camera setup | `CameraSetup.qml`, `setup_presentation.py` | Owned frozen pixels, actual serial, marker references, read-only trained ROI, revision and confirmation gates |
| Recipe management | Setup adapter and existing `recipes.py` | Bundle-relative paths/hashes, no overwrite, immutable per-run setup before capture |
| Latest result and evidence | Shared controller and image provider | Four outcomes, inclusive float32 review band, exact frame/crop, evidence-qualified validity and separate defect latch |
| Offline inspection | `OfflineInspection.qml`, `offline_presentation.py` | Existing import/inference workers, independent ROI/threshold, manual snapshots without live evidence claims |
| Recording and synthetic replay | `ReplayDiagnostics.qml`, `replay_settings.py` | Existing sequential source-time pipeline; counting only; explicit Start/Stop and NOT LIVE labeling |
| Candidate collection | `CollectionTraining.qml`, `collection_presentation.py` | Existing bounded writer, exact auto callback, explicit distinct/fresh manual frames, 20-30 target, incomplete coverage on failure |
| Candidate review and training | Collection adapter and unchanged training CLI | Hash-verified display, review of every candidate, explicit undamaged confirmation and separate-process training |
| Model export and binding | Collection adapter and existing recipe writer | Confined export, offline inspection, new family recipe; no automatic live activation |
| Appearance and accessibility states | `palette.py`, QML theme/components, matched QSS | Existing dark/light semantic colors, labels/symbols, focus/disabled/hover states and shared preference key |
| Events, resource limits and exit | Qt models and shared controller | Bounded logs/images/queues; collection drain included in the existing close budget; active training requires explicit wait/cancel |

Model weights, preprocessing, runtime adapters, thresholds, synchronization and
evidence schemas were not redesigned. `ImageImportWorker` gained an optional
candidate hash check; existing callers retain the original behavior. The candidate
writer gained an immutable nonblocking progress-read API; writing/capture semantics
are unchanged. The QML adapter retains the last available persisted progress snapshot
while the writer owns its lock.

## Verification and artifacts

The [progress record](QML_Migration_Progress.md) contains final test counts and
installed-wheel results. All native suites include the original Widgets workflows.
Generated images and contract graphs establish plumbing and identity checks only;
no new fitted defect model or accuracy result is claimed.

Local artifacts are in
`application/rdt_monitor/dist/migration-qml-complete`. They include native screenshots,
the complete pytest transcript, source/wheel builds, installed-wheel checks, runtime
and source fingerprints, hashes and rollback/install instructions. Build artifacts
are ignored by Git; source and documentation remain available for review in the
working tree. No public release, installer deployment or Git commit is implied.

The existing selected training environment was import-checked without fitting:
Anomalib 2.7.1.dev0, PyTorch 2.14.1+cpu, OpenVINO 2026.4.1 and ONNXScript 0.7.2.
Training orchestration tests use a real child process and the existing dataset
verification function with an injected contract export backend. They preserve
explicit review/confirmation and never activate a live detector.

## Measured software smoke

A 15.063-second native 960x640 QML run used a generated threaded 640x360 source and
the actual candidate writer. It produced 349 source frames, saved all 20 candidates
as Awaiting confirmation, kept the inspection verdict Not inspected and did not
start training. Image providers retained one monitor channel and one collection
channel; offline/setup retained none.

After nonblocking progress reads, a requested 16 ms GUI timer measured p50 16 ms,
p95 63 ms, p99 84.740 ms and maximum 250 ms. Process working set ranged from
205,242,368 to 299,220,992 bytes and ended at 211,410,944 bytes. The before/after
measurements are retained; p95 and the maximum did not materially improve. Removing
the progress-lock wait is verified separately by a held-lock regression test.

This is a short hosted-PC software measurement, not input-to-verdict latency,
sensor timing, sustained 30 FPS, memory endurance or target-station acceptance.
No agreed production timing limit has been established or demonstrated. The
remaining jitter must be assessed with representative acquisition, inference,
evidence load and display settings on the target PC.

## Rollout and cutover

Use the complete QML workflow explicitly:

```powershell
uv sync --project application/rdt_monitor --group dev --extra inference --locked
uv run --project application/rdt_monitor --extra inference --locked rdt-monitor-qml
```

The immediate UI rollback is the retained default command, using the same tested
PySide6 environment and backend:

```powershell
uv run --project application/rdt_monitor --extra inference --locked rdt-monitor
```

Close the active session before switching interfaces. Do not open a second camera
owner. Retained checkpoint source/wheels and matching metadata are listed in the
release artifact manifest; use their matching environment for an earlier binding
rollback. Do not mix PyQt6 and PySide6 in one interpreter.

The revised plan's phases 1-5 are implemented. Phase 6 software checks and packaging
are recorded here; the following acceptance work remains external to GUI coding:

- Physical marker/motion/lighting and remount acceptance; sustained camera acquisition,
  disconnect/reconnect, driver behavior and overload/fault recovery on the actual station.
- Representative actual-model comparison and operator approval of defect behavior,
  evidence coverage and performance limits; contract graphs do not establish these.
- Target-PC DPI/font/accessibility acceptance and long-running resource/storage tests.
- Clean offline deployment/installer and dependency/distribution review, plus an
  exercised station rollback with the intended recipes, models and data locations.

Phase 7 default-launcher cutover and removal of the Widgets fallback are deferred
until the workstation trial and the plan's acceptance gates pass. No qualification
or production gate is waived by completing the GUI implementation.
 
The final local bundle is `application/rdt_monitor/dist/migration-qml-complete/rdt-monitor-qml-rc-20261030.zip`.
It includes the wheel/source, locked metadata, validation records, source/runtime
fingerprints, screenshots and install/rollback instructions with SHA-256 checksums.
Final installed-wheel checks passed outside the checkout, both with and without
OpenVINO, using `uv --offline` and the populated local cache. This verifies cached
installation on this host; dependency binaries for a clean offline PC remain separate.
