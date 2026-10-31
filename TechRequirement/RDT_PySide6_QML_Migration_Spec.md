# RDT Monitor: repository-grounded PySide6 and QML migration plan

Reviewed: 2026-10-30 against repository revision `3c2e393e`.

Status: GUI implementation complete as a release candidate; workstation qualification
and default-launcher cutover remain gated. See the
[acceptance record](QML_Migration_Acceptance.md).
Implementation progress and fresh validation are tracked in the
[migration progress record](QML_Migration_Progress.md).
This supersedes the supplied generic migration sequence for this repository. The source
document in Downloads is unchanged. Its embedded coding-agent prompt was reviewed as
document content. Implementation began only after the user's subsequent instructions
to proceed and continue the revised plan.

## 1. Review conclusions

The original principle is sound: QML owns presentation; Python owns acquisition,
synchronization, inspection, run accounting, recipes, and evidence. Incremental migration
and unchanged model behavior remain essential. However, the proposed work starts too
far upstream of the actual implementation and omits several working operator workflows.

| Original assumption or proposal | Repository finding | Revised decision |
| --- | --- | --- |
| Existing Qt-style Widgets application | Standalone PyQt6 Widgets monitor | Treat PyQt6-to-PySide6 and Widgets-to-QML as separate changes |
| Introduce services and an authoritative state machine | Qt-independent camera, synchronization, lifecycle, inference, collection and recipe modules already exist | Reuse these; extract orchestration from `MainWindow`, rather than rebuild the backend |
| PASS / FAIL / INVALID | Existing normal / needs-review / defect / uninspected outcomes | Preserve all four outcomes and the inclusive review band |
| Anomalib runtime first, OpenVINO later | Live and offline inspection already run exports through OpenVINO | Preserve the exported-model adapter and lightweight monitor environment |
| Separate camera, inference and persistence execution | Capture has its own worker; live inference and evidence share another worker | Retain this bounded design initially; split persistence only if measurements justify it |
| New generic project tree and model directory | Standalone package under `application/rdt_monitor`; detector bundles live beside recipes | Extend the current package; retain bundle-relative paths and schemas |
| Dashboard, settings and diagnostic pages | Working setup/ROI editor, collection/review/training, offline inspection and replay also exist | Include them in parity scope; migrate complex dialogs last |
| Database service | Local evidence files exist; station SQLite/audit/retention remain planned | Preserve file evidence; do not add a database as a QML prerequisite |
| One linear machine-state enumeration | Run, synchronization, camera health and per-cycle activity are distinct | Publish a composed snapshot without replacing domain states |
| Remove Widgets after a new main screen works | Auxiliary workflows are operationally important | Retire only after the complete parity checklist passes |

The [current architecture map](current_architecture.md) records code ownership and coupling.
The [working pipeline](Working%20Pipeline.svg), [operator guide](../application/rdt_monitor/README.md),
and [rollout plan](Rollout%20Plan.md) are required context. Where historical architecture
intent differs from implemented behavior, identify the difference explicitly.

## 2. Scope and boundaries

Change the standalone monitor presentation and its Qt binding. Leave Anomalib core,
Studio, model weights, exports, thresholds, preprocessing, inspection geometry and
evidence formats unchanged. Do not introduce an IPC/controller source in this migration.
The agreed rollout remains vision-marker pilot first, read-only controller integration
later. The application remains advisory; Stop stops monitoring, not the robot.

The current system is development software. Local committed cycle evidence is not a
durable station audit, reboot recovery, retention policy, authenticated administration,
or approval for unattended operation. QML completion must not enable the gated production
run mode. Production-readiness work stays on its existing qualification track.

No new login is required for the existing Advanced recipe editor or recipe saves.
Preserve current access behavior. Protected station administration is separate future work.

## 3. Pipeline invariants

### Acquisition and cycle recognition

1. One `CameraPreview` worker owns device open/read/close using the existing Keyence
   wrapper and its OpenCV backend fallbacks. A generic camera index does not verify hardware identity.
2. `MarkerTrial` recognizes every returned frame before replacement of the latest-preview
   mailbox. Never drive synchronization from a QML timer or the displayed preview.
3. Initial stable visibility establishes a baseline. Both markers must subsequently
   disappear and return stably to complete one visually observed cycle. Uncertainty,
   gaps and overflow retain their existing explicit fault semantics.
4. The exact accepted cycle-return image is submitted to `LiveInspection.capture`.
   Never substitute the latest displayed image. Preview dropping remains allowed;
   losing inspection coverage must remain explicit.
5. Host monotonic read-completion time is not a camera exposure timestamp. Preserve
   timing provenance; do not rename it to sensor capture time.

### Inspection and evidence

Reuse `load_exported_model`, `inspect_image`, `RecipeDetector.verdict` and `LiveInspection`.
Load the live predictor once per worker lifecycle. Preserve source-copy ownership,
normalized ROI-to-pixel rounding, BGR-to-RGB conversion, float32 scaling, NCHW layout,
fixed-input linear resize, dynamic-input behavior, output validation and aligned heatmaps.
Both ONNX and IR inputs use the existing OpenVINO adapter; no ONNX Runtime switch is implied.

Preserve float32 threshold comparison exactly:

| Score | Domain outcome | Operator presentation |
| --- | --- | --- |
| `< threshold - review_margin` | `NO_DEFECT` | Normal / no defect detected |
| Inclusive interval between both boundaries | `NEEDS_REVIEW` | Needs review |
| `> threshold + review_margin` | `DEFECT` | Defect |
| Missing/invalid inference or incomplete required evidence | `UNINSPECTED` | Not inspected, with reason |

Even a zero-width review band includes equality. Do not replace this contract with
a binary comparison. Scores are not probabilities. Distinguish no result yet, a pending
cycle and a failed/uninspected cycle through presentation status and reason, without
inventing a competing domain verdict enum. A technical failure cannot become normal.

Model outcome and evidence validity remain distinct. Preserve staged image/JSON writes,
directory commit, model and setup hashes, failure-record fallback, per-run storage budget,
and incomplete-evidence behavior. A metadata-only failure record is not an inspected cycle.
Preserve the separately latched defect alarm, including model-detected defects whose
evidence fails; a later normal result does not clear the latch.

Reuse `CycleTicket` and existing identities, including run, segment, cycle and source
frame. Frame IDs alone are insufficient across new workers/runs. Reject stale worker
completions and unmatched tickets. Keep observed, inspected, uninspected and pending
counts distinct, and test their accounting during stop, overload and coverage loss.

### Recipes and setup

Retain schema-5 writing and schema-1-to-4 reading, new-filename revision protection,
relative detector bundles, model hashes, specimen/family separation and source dimensions.
Loading a recipe preserves the actual specimen serial and does not start a run.
Start requires fresh camera setup and explicit confirmation. Technical/geometry edits
invalidate confirmation; cancellation leaves the previous configuration unchanged.
Marker adjustments do not relocate the trained inspection ROI or imply model compatibility.
Save the complete hashed run-setup snapshot before admitting camera cycles; save failure
blocks Start. Preserve file-picker defaults and existing `QSettings` display preferences.

### Stop, disconnect and close

Reuse the current sequence: bar new observations, drain accepted source events, settle
inspection tickets, then finish asynchronously. Preserve the source-quiescent condition
and explicit finalization timeout. Disconnect releases the device after run finalization.
Stop can leave a healthy camera preview connected; a synchronization fault need not kill it.

The three-second run-finalization budget, stalled-driver behavior and daemon-worker
abandonment are existing development limitations. Keep them visible; do not promise
inference cancellation, forceful driver interruption or production-safe shutdown.
Do not resume or reconnect automatically against an unverified OpenCV index.
Any later process-isolation/shutdown redesign is a separately reviewed reliability change.

## 4. Target architecture with minimal changes

```text
Widgets adapter (during transition) OR QML presentation
                         |
PySide6 view models, event model, image provider, GUI timer
                         |
Qt-independent MonitorController: commands, coordination, snapshots
                         |
Existing modules retained in place:
  camera + replay + synchronization + lifecycle
  recipes + detection + live + collection + training
```

One controller owns each active source/run and consumes each worker queue once. Views
observe its state; Widgets and QML must not independently dequeue events or open cameras.
Parity runs use separate processes or sequential sessions with exclusive camera ownership.

Add only what extraction needs under `application/rdt_monitor/src/rdt_monitor/`:

```text
controller.py                 command/orchestration extraction
presentation/                 PySide6 QObject and Qt model adapters
qml/Main.qml                  initial window and monitor page
qml/components/               preview, verdict, controls, event log, ROI
qml/theme/                    centralized dark/light tokens
```

Keep existing backend filenames, `rdt_monitor.app:main`, recipe paths and build-hook
camera resource behavior initially. Do not move the package into a new project tree.

Extract in small behavior-preserving pieces: queue polling/event reduction and snapshots;
run start/configuration commit; live-result settlement and alarm bookkeeping; stop/drain/
disconnect/close coordination. `ReplaySettings` retains editing widgets while its candidate
validation/commit logic becomes reusable. Collection and training process orchestration
can remain in PySide6 Widgets dialogs until their own migration phase.

## 5. State and presentation contract

`RunLifecycle` remains authoritative for `RunState`, `RunMode`, tickets and counters.
`CycleSynchronizer` remains authoritative for `SyncState`. Camera status remains with
`CameraPreview`. Capturing, inferencing and recording may overlap and must not replace
run health with a single linear sequence of states.

Expose one coherent immutable controller snapshot after each bounded poll batch:

| Area | Required fields |
| --- | --- |
| Context | source type, development/production mode, specimen, recipe/revision, run/segment |
| Health | camera connection/freshness, synchronization state, run state, fault reason |
| Counts | observed, inspected, uninspected, pending, latched defects, coverage gaps |
| Latest inspection | cycle/frame identity, outcome, pending/validity reason, score, evidence status |
| Images | preview identity/URL separately from completed inspection ROI/overlay identity/URL |
| Commands | `canConnect`, `canDisconnect`, `canLoadRecipe`, `canStart`, `canStop`, disabled reasons |

Python computes command eligibility and revalidates every command, including repeated
Start or stale UI actions. QML does not determine verdicts, counters or readiness.
Do not derive business state by reading widget labels during extraction.

Use PySide6 `QObject`, `Property`, `Signal`, decorated `Slot`, and a bounded
`QAbstractListModel` for operator events. Notify only changed properties, and update
Qt objects/models on the GUI thread. Keep strong ownership of controller/view models
through engine shutdown; stop timer/worker callbacks before destroying their adapters.
Keep exception details in diagnostic logs and actionable error reasons in the UI.

Initially preserve the existing Python threads, bounded queues and approximately 33 ms
main polling cadence. Polling moves into the adapter; it need not become per-frame signals
or a new QThread/asyncio framework. Bound work per tick so a backlog cannot monopolize
the GUI; critical events cannot be silently discarded. Benchmark cadence rather than
declaring it a new inspection deadline.

## 6. Binding migration before visual migration

First port the existing Widgets app and its Qt tests to PySide6 with unchanged layout.
Change imports/signals and validate enum use, event signatures, dialog behavior,
`QProcess`, QtTest behavior, object lifetimes and NumPy-backed `QImage` ownership.
Replace the monitor's PyQt6 dependency with a compatible locked PySide6 version using
`uv`; preserve other dependencies unless compatibility requires a separately explained change.
Do not import both bindings in one application process or pass Qt objects between them.
Retain the original PyQt6 release/environment as the rollback baseline.

The extraction and binding-port commits must each pass regression independently before
QML styling starts. A temporary pure-Python controller must not import either Qt binding.

Use `QApplication` while auxiliary Widgets dialogs remain. Keep a PySide6 Widgets launch
option when adding the QML shell; both options use the same controller. Prototype a
top-level `QQmlApplicationEngine` window, then validate native dialog parenting, modality,
focus, screen placement and shutdown. Choose embedding only if the prototype demonstrates
a concrete need; do not retain two presentation technologies without an exit plan.

## 7. Image transport and UI requirements

Prototype a `QQuickImageProvider` serving owned `QImage` snapshots. Never pass a borrowed
NumPy buffer whose lifetime can end before rendering. The provider returns existing
image data; it never acquires frames, runs inference or writes evidence.

Use explicit generation/frame URLs with image caching disabled for changing streams.
Define provider locking and a bounded ownership scheme: old URLs must not silently
return a newer frame under the old identity. Permit dropping superseded preview requests;
retain the latest inspection image separately. Keep locks short and avoid blocking on
worker completion. If asynchronous loading is enabled, verify provider thread safety and
late-request behavior. Qt documents provider caching and asynchronous request behavior in
the [QQuickImageProvider reference](https://doc.qt.io/qt-6/qquickimageprovider.html).

Avoid base64 and full-resolution Python-to-QML properties. Resize only preview copies;
never change inference input. Measure preview conversion cost and memory before selecting
a more complex texture/video path.

Preserve the present information hierarchy: camera connection/freshness, latest verdict,
latched defect alarm, monitoring controls/counters, event log and latest inspection ROI.
Keep faults and missing inspection coverage visible with Details closed. Counting-only
runs must not generate a missing-inspection alarm for each counted cycle.

Keep the latest completed ROI while the next cycle is pending and after Stop; clear on
a new run/recipe and show unavailable for a newer cycle without a result. Evidence failure
still makes the displayed verdict uninspected, even if a model heatmap can be shown.

Centralize restrained dark/light theme tokens, accessible contrast and text labels.
Preserve the effective existing GUI color portfolio rather than introducing a new
visual identity. Use semantic normal/review/defect/uninspected tokens, independent
camera/run/latch indicators, blue keyboard focus and stable marker/inspection ROI
colors. Light mode needs darker verdict foregrounds; explicitly test enabled,
hover, pressed, disabled and focus states. Keep Widgets QSS and QML controls aligned
through the transition. The applied role table and measured contrast scope are in
[design direction](design%20direction.md#implementation-update-2026-10-30).
Preserve keyboard navigation, resizable windows, screen-aware dialogs and readable layouts
at the current supported sizes and Windows display scaling. Text, geometry and overlays
must remain correct with letterboxing; colors alone must not carry verdict meaning.

## 8. Required workflow parity

| Workflow | Behavior to retain before removing its Widgets implementation |
| --- | --- |
| Everyday operation | Load before camera/specimen, connect/freshness, actual serial, confirmed Start, Stop/restart, separate health/verdict, defect latch, open results |
| Basic/Advanced setup | Frozen image, draw/move/eight-handle resize/clear, overlap hit-testing, letterbox clamping, numeric coordinates, marker reference recapture, validation and cancellation |
| Recipe management | Old schema reads, schema-5 new revisions, no overwrite, bundle confinement, hashes, current serial preservation, immutable run setup |
| Collection | Manual and automatic exact-frame/ROI capture, bounded writer, explicit interruption, target stops collection without starting training |
| Review/training | Manifest reload, every-candidate review, undamaged confirmation, hash/source-crop validation, explicit Train, separate interpreter/process, errors/cancel/logs |
| Model use | Export does not activate automatically; save binding, then explicit load/setup/confirm before a later run |
| Offline inspection | Background import/inference, stale-result invalidation on edits, exact heatmap/source pairing, explicit PNG snapshot, no live-run mutation |
| Replay | Fixture clearly labeled synthetic, recording/settings, separate source ownership, counting-only semantics, no production evidence claims |
| Preferences/details | Remembered light/dark mode, diagnostic access, bounded log, faults visible when Details closed |

Collection/training remains a separate workflow reachable from the monitor. It is an
existing requirement, so the original instruction to keep training out of the normal
operator UI must not remove it.

## 9. Delivery phases and exit gates

Each phase is a reviewable change with its own evidence. No delivery dates are implied.

| Phase | Work | Exit gate / rollback |
| --- | --- | --- |
| 0: baseline | Record architecture, locked environment, representative recipes/exports/inputs, screenshots, test results and target-PC performance | Reproducible PyQt6 baseline; archive release and matching environment |
| 1: controller extraction | Move orchestration incrementally; keep Widgets functional and backend contracts unchanged | Existing suite plus meaningful controller parity tests pass; revert individual extraction if needed |
| 2: binding port | PySide6 Widgets app, updated tests and lockfile; retain other runtime versions | Behavior/layout/process/settings parity; packaged resources work; rollback to PyQt6 release |
| 3: QML vertical slice | Explicit launch option; status, bounded events, preview and read-only ROI from shared controller | Source/frame identity, image lifetime/cache, rendering and shutdown prototype pass on target Windows PC |
| 4: daily operation | Start/Stop/recipe/specimen flow and verdict/evidence/counters; reuse PySide6 setup dialogs initially | Deterministic end-to-end normal and fault scenarios match Widgets; operator workflow complete |
| 5: auxiliary UI | Setup editor, collection/review/training, offline inspection, replay and settings migrated one workflow at a time | Full parity table passes; no placeholder replaces a working workflow |
| 6: release candidate | Installed-wheel and deployment smoke tests, recorded-input comparison, hardware fault trials and measured endurance | Numerical/identity/evidence parity and agreed performance limits accepted; retain Widgets fallback |
| 7: cutover | Default to QML; remove fallback after accepted workstation trial and complete parity | Release rollback artifact retained; update operator guide, architecture technology decision and pipeline diagram |

If only the main screen is migrated, call it an intermediate QML release. Do not mark
the whole migration done while required dialogs still depend on Widgets. Prefer the
top-level engine approach above; document any remaining hybrid scope explicitly.

## 10. Regression and acceptance evidence

Preserve the current tests rather than rebuild their intent. Core anchors include
`test_camera.py`, `test_synchronization.py`, `test_lifecycle.py`, `test_live.py`,
`test_detection.py`, `test_recipes.py`, `test_collection.py` and `test_replay.py`.
Port Widgets integration assertions in `test_app.py` and `test_recipe_workflow.py`;
retain backend assertions and replace view-specific assertions when QML owns that view.

Compare deterministic recorded/synthetic source sequences through each presentation
path sequentially. Do not compare independent live-camera runs as if their pixels were
identical. Use actual qualified model/data artifacts when available; tiny contract graphs
and synthetic marker fixtures establish plumbing only, not defect-detection accuracy.

Compare accepted frame pixels/IDs, synchronization events/order, ROI bytes, settings,
scores/maps, float32 review-band endpoints, outcome, evidence content/hashes and counters.
Normalize intentionally variable run IDs, output directories and wall-clock timestamps
when comparing records; still validate their internal linkage. Unchanged runtime/input
should not acquire a new broad numerical tolerance. Investigate differences before cutover.

Required failure and lifecycle cases:

- Missing/busy camera, open/read stalls, disconnect, empty/invalid frame, stale preview.
- Ambiguous marks, startup mid-cycle, staggered returns, observation gaps and event overflow.
- Missing/tampered model, malformed/nonfinite outputs, inference exception and slow inference.
- Queue overload, disk-full/budget exhaustion, partial image writes, failed result record
  and failed setup snapshot; no inspected/normal success from incomplete required evidence.
- Repeated Start, Stop during capture/inference/write, disconnect during drain, close while
  processing, finalization timeout, late completion from retired workers and restart isolation.
- Counting-only versus model-bound mode, defect latch after a subsequent normal cycle,
  valid heatmap with failed evidence, canceled setup and stale recipe actions.
- Training interpreter startup failure, cancellation/export failure, collection tampering,
  offline edit invalidation and source switching without concurrent camera ownership.

Record target-PC acquisition/recognition gaps, preview age, UI command latency, inference
and evidence latency, backlog peaks, CPU/GPU/RAM use and long-run memory growth. Define
numeric limits and trial duration from the existing baseline and station requirements
before cutover. QML adds graphics load on the robot IPC; measure coexistence. Test the
chosen graphics backend, Windows scaling, fonts and driver behavior on that PC. Do not
assert a new frame rate or responsiveness guarantee without measurements.

Use native Windows visual acceptance; this repository records offscreen font limitations.
Check keyboard/focus/modality, resize, full event log and faults with Details closed.
Check readable rendered screenshots, not merely successful startup.

Baseline commands, from the repository root:

```powershell
uv sync --project application/rdt_monitor --group dev --extra inference --locked
uv run --project application/rdt_monitor --group dev --extra inference --locked python -m pytest -c application/rdt_monitor/pyproject.toml --confcutdir=application/rdt_monitor/tests application/rdt_monitor/tests -q
uv run --project application/rdt_monitor --group dev --locked ruff check application/rdt_monitor
uv run --project application/rdt_monitor --group dev --locked ruff format --check application/rdt_monitor
```

During the binding-port phase, deliberately update the lockfile before resuming locked
commands. Build wheel and source archive, install the built wheel into a clean environment
and launch outside the checkout. Verify QML modules/assets, styles needed by remaining
dialogs, camera wrapper, optional inference imports and external recipe bundles. Review
the eventual installer separately; a wheel build is not an offline deployment acceptance.

## 11. Dependencies and completion criteria

Use `uv` throughout. Capture the actual lockfile, interpreter, Qt binding/runtime,
OpenVINO, NumPy/OpenCV, training interpreter, camera driver and graphics backend baseline.
Do not install Anomalib/PyTorch training dependencies into the monitor merely for QML.
Select a PySide6 version compatible with that baseline and the target PC; this plan does
not invent a tested version. Review distribution obligations and bundled assets for the
selected Qt modules; changing bindings alone does not establish packaging compliance.

Qt implementation references:

- [PySide6 signals, slots and thread affinity](https://doc.qt.io/qtforpython-6/tutorials/basictutorial/signals_and_slots.html)
- [QQuickImageProvider caching and asynchronous loading](https://doc.qt.io/qt-6/qquickimageprovider.html)

Completion requires preserved pipeline semantics and all required workflows, a responsive
QML interface, isolated ownership, bounded transport, successful installed-artifact checks,
accepted workstation evidence, updated documentation and a tested rollback route.
Station qualification, authentication, retention, durable recovery and controller feedback
remain separate deliverables governed by the rollout plan.

The initial plan review inspected source, tests, requirements and existing validation notes. It did
not execute application tests, operate hardware, measure performance or qualify models.
Historical test counts in the operator guide are not fresh acceptance results.
