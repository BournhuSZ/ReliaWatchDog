# QML migration progress

Started: 2026-10-30. Scope follows the
[revised migration plan](RDT_PySide6_QML_Migration_Spec.md).

## Baseline

The initial native Windows run at source revision `3c2e393e` completed with
392 passed, 10 failed and 2 skipped. The ten failures were eight parameterizations
of evidence-column resize/drag alignment and two camera-badge metadata checks.
These were present before extraction. The alignment fix immediately reallocates
the bottom splitter after updating evidence width. The badge fix initializes and
updates its connection property independently from inspection validity.

Dependency versions and lockfile hashes are recorded in
[QML_Migration_Baseline.json](QML_Migration_Baseline.json). This records the development
workstation software environment, not a qualified station hardware/performance baseline.
No model bundles, recipes, images or thresholds were rewritten for migration.

## First controller extraction

`application/rdt_monitor/src/rdt_monitor/controller.py` now owns run identity,
the existing `RunLifecycle`, cycle-ticket admission, bounded inspection-position
history, result settlement, latest-cycle ordering, model-defect latching,
pending-result overflow handling and asynchronous stop finalization. Widgets
render typed completion/finalization updates. The existing collection dialog reads
run/detector state from this controller.

Completion validation checks worker ownership and the original synchronization
event as well as the ticket's run identity before settlement. A rejected completion
does not consume a valid ticket. This also preserves cumulative lifecycle cycle
numbers across recovery segments whose source counters restart.

Seventeen new controller cases cover the existing review band, evidence-invalid
defects, out-of-order completions, identity mismatch/duplicates, retired workers
with reused frame IDs, overflow, stop drain/deadline, restart gating, recovery,
bounded counting history and production readiness. Existing Widgets assertions
now inspect the controller's state; their behavioral assertions are retained.

## Validation of the first extraction

Native Windows Qt validation completed with **419 passed, 2 skipped** in 52.80 seconds.
The skip sites are Windows symlink privilege checks; they do not establish symlink
behavior on this workstation. Ruff check and format check passed for all 27 monitor
Python files. Source distribution and wheel builds succeeded under
`application/rdt_monitor/dist/migration-phase1`.

The built wheel was installed through `uv` in an isolated environment outside the
checkout with the same NumPy, OpenCV and PyQt6/Qt/SIP versions as the baseline.
Installed imports, controller initialization, both stylesheet resources and the
packaged Keyence wrapper were verified without the optional inference extra. Qt
runtime was 6.11.2 (binding build version 6.11.0). This is an import/resource smoke
check, not installer, hardware or visual acceptance of a new UI.

No dependency or lockfile update was needed. Existing inference/recipe/camera-worker
modules were unchanged. The migration is still in the PyQt6 extraction phase.

## Source ownership and configuration extraction

The controller now owns the camera/replay workers, latest frame, source selection
state, run setup commit, command eligibility, bounded source/result polling,
disconnect-after-drain and the existing close budget. `MonitorSnapshot` is immutable;
`SourcePoll` carries already-admitted observations and already-settled results.
Widgets render these updates without consuming the source queues themselves.

Source events are admitted before inference completions, independently of preview
replacement. Frozen detector settings travel with result updates so rendering does
not depend on keeping a retired worker alive. Failed camera attachment cancels and
retains any unfinished model worker, preventing an immediate overlapping restart.
Repeated close requests do not extend the existing three-second abandonment budget.

The existing 419 tests passed under PyQt6 after source extraction. Twelve additional
source-controller cases passed, including source exclusivity, setup-before-capture,
setup storage failure, source recipe changes, lossless event admission without a
preview, explicit accounting errors, disconnect barriers, replay completion,
immutable snapshots, close timing, failed attachment and admission-before-settlement.

The PyQt6 source checkpoint was built to
`application/rdt_monitor/dist/migration-source-controller`, alongside the original
project metadata and lockfile. These are local development rollback artifacts;
the baseline source remains repository revision `3c2e393e`.

## PySide6 binding port

The application and native integration tests now import PySide6. Custom signals
use `Signal`; frozen-image marker capture reads the binding's memoryview while
preserving row-stride handling. Display images retain their owned `QImage.copy()`.
Two added native cases check odd/even image widths and source-buffer ownership.

The monitor depends on **PySide6 Essentials 6.11.3** with Shiboken 6.11.3. Essentials
includes Widgets, QML, Qt Quick, Quick Controls and QtTest without the unused add-on
bundle. See the [official package description](https://github.com/qtproject/pyside-pyside-setup/blob/dev/README.pyside6_essentials.md).
The Qt runtime changes from 6.11.2 to 6.11.3 with this binding port. Other locked
packages, including OpenVINO, OpenCV and NumPy, retain their existing versions.
PyQt6 is removed from the monitor dependency graph; the bindings are not mixed.

Before QML work, the complete native Windows suite passed with **433 passed,
2 skipped** in 62.60 seconds. Ruff and source-to-wheel builds passed. The new
[runtime fingerprint](QML_Migration_PySide6.json) records the binding, Qt runtime,
lockfile hash and unchanged inference/runtime versions.

## Optional QML vertical slice

`rdt-monitor-qml` launches a separate preview shell through `QQmlApplicationEngine`.
The default `rdt-monitor` command retains the complete Widgets workflow. The
prototype covers camera preview/connect/disconnect, synthetic counting diagnostics,
run/source health and counts, session events, light/dark preference and nonblocking
close. It deliberately exposes no unconfirmed camera-inspection Start command.

`MonitorViewModel` polls only through the shared controller and emits changed
presentation properties. `EventLogModel` retains 500 structured rows. `FrameProvider`
keeps an owned image per preview/ROI channel and generation-specific URLs with QML
caching disabled. An obsolete URL returns no image rather than a newer acquisition.
Read-only inspection ROI transport, validity and the defect latch are available to
the adapter; the prototype has no recipe activation or live inference UI yet.

Eight native QML cases cover image ownership/stale URL rejection, bounded model roles,
coalesced status notifications, missing specimen labels, fixture rendering at 960x640
and 1440x900, preview-versus-inspection validity, exact inspected-frame ROI delivery,
evidence-invalid verdicts with a preserved defect latch, and asynchronous close. The shell was
also rendered in both themes for visual review; placeholder contrast and text sizing
were corrected. The working pipeline diagram now identifies the PySide interface
and controller-owned updates.

Final combined validation completed with **441 passed, 2 skipped** in 64.34 seconds.
The skips remain Windows symlink privilege checks. During combined testing, replacing
a native `QWidget.screen()` method with a Python test double coincided with native
heap corruption. The geometry helper now accepts a rectangle explicitly, preserving the same test
assertions without overriding the native method. The combined suite subsequently
passed twice (439 passed before the final two ROI cases, then 441 passed).

Ruff and formatting passed for all 31 Python files. Final source/archive builds are
under `application/rdt_monitor/dist/migration-qml-preview`. The wheel was installed
in an isolated Python 3.11 environment outside the checkout with the tested runtime
versions. Its QML window rendered successfully, the optional launcher was registered,
QML/theme resources were present, and the packaged Keyence wrapper matched the original
byte for byte. The smoke environment did not install the inference extra.

## Confirmed QML operation and GUI color portfolio

The optional interface now loads recipes without starting or replacing the current
specimen serial. Start reuses the existing frozen Basic/Advanced native setup dialog,
validates the fresh source and recipe, commits setup, then attaches the shared run
pipeline. Cancellation has no run side effects. Repeated Start, stale camera input,
changed recipe files and setup storage failures have explicit regression coverage.
Stop drains asynchronously; synchronization faults require explicit reconfirmation.

The camera and latest inspection remain distinct. Results carry exact cycle/frame
identity, raw ROI and the existing anomaly heatmap. Toggling the map never reruns
inference. Failed evidence cannot produce a normal verdict; the defect latch remains
independent of the latest result. A healthy camera preview survives monitoring Stop.

`palette.py` supplies semantic colors to QML and the Widgets verdict painter. The
existing QSS action colors are retained with targeted light-mode contrast/specificity
and disabled-control fixes. Both themes preserve green normal, amber review, red
defect, grey uninspected and blue focus; words/symbols accompany color. Marker
overlays retain blue Start, amber Position and cyan Inspection ROI on dark label
backings. No palette token determines a domain outcome.

Native visual checks cover both themes at 960x640 and 1440x900, including a camera
viewport containment regression. Local screenshots and a four-verdict color gallery
are under `application/rdt_monitor/dist/migration-qml-operation`. Their imagery and
gallery outcomes are synthetic visual references, not camera or model acceptance.
The native setup dialog is transient to and centered within the QML window's screen.

New regressions exercise actual asynchronous inspection/evidence plumbing with an
injected contract predictor, cancellation, restart, evidence failure and field
locking. Forty-four contrast cases check named text/background pairs, including
hover, pressed and disabled controls. These checks do not establish full accessibility
or trained-model accuracy.

Combined native Windows validation passed with **498 passed, 2 skipped** in 68.85
seconds; both skips concern Windows symlink privileges. Ruff and formatting passed
for all 35 source/test Python files. Source and wheel builds succeeded. The wheel
was installed outside the checkout in isolated Python 3.11 without the inference
extra and rendered successfully. Packaged QML components, palette/dialog modules,
launcher metadata and the byte-identical original Keyence wrapper were verified.

## Offline image inspection in QML

The next auxiliary workflow now has an independent QML screen behind the Offline
button. `OfflineInspectionModel` reuses `ImageImportWorker`, `InspectionWorker`,
`DetectionSettings`, normalized `Region` validation and the existing heatmap renderer.
Model decoding, preprocessing, score decisions and export formats are unchanged.
There is no controller dependency in the offline adapter and no live evidence write.
The existing offline binary normal/anomaly threshold remains separate from live
recipe review-band outcomes.

The shared-palette cyan ROI editor supports drawing, moving, eight resize handles,
clear/full-image selection and numeric percentages with letterbox-aware coordinates.
Inspection and snapshot actions stay pinned below scrollable inputs. All result
labels say unqualified/offline. PNG snapshots contain the exact displayed source or
overlay, with an explicit manual-snapshot disclaimer. Changed inputs invalidate
assessment and export eligibility. Closing clears retained display data and rejects
late jobs; an outstanding job prevents overlapping restart until it settles.

Nineteen native regressions cover exact RGB crop/source alignment, heatmap toggling,
manual snapshot contents, invalid inputs and storage failure, picker cancellation,
background detector failure, late completion, worker-publication timing, keyboard
threshold editing, mouse-driven ROI geometry and control containment in both themes
at 960x640 and 1440x900. One case executes an actual OpenVINO contract graph; injected
predictors and that graph establish plumbing only, not trained-model accuracy.
Synthetic visual QA captures are under
`application/rdt_monitor/dist/migration-qml-offline`.

The combined native Windows suite passed with **517 passed, 2 skipped** in 109.01
seconds. Skips remain Windows symlink privilege checks. Ruff and formatting passed
for 37 source/test Python files. The default Widgets workflows remain covered.
Source and wheel builds passed. An isolated Python 3.11 installation outside the
checkout rendered the packaged offline screen/ROI editor without the inference
extra. Attempting inspection there produced explicit dependency-install guidance,
a neutral result and no snapshot eligibility; it did not change the live verdict.
The original camera wrapper remained byte-identical. This smoke check is separate
from the actual OpenVINO contract execution in the full development suite.

## Local recording replay and replay-only configuration in QML

Diagnostics now offers a local recording picker, explicit replay label and Start,
asynchronous Stop, and the existing synthetic fixture. Picking a file does not open
its decoder or start a run; cancellation preserves selection and native modal
dialogs block conflicting commands and window close. Replay-only numeric settings
reuse `Region`, `SyncConfig` and `ReplayRecipe` validation through `replay_settings.py`.
All fields commit atomically with a settings revision. Cancel/close discards drafts;
edits never mutate a live recipe, detector bundle or actual specimen serial.

`MonitorController.start_replay` remains the sole pipeline/run owner. The existing
`VideoSource` decoder and `ReplayPipeline` process the recording with source-time
semantics. Replay stays counting-only and cannot produce a defect verdict, model
ROI or inspection evidence. Preview headings explicitly mark recording pixels NOT
LIVE; the badge shows replay terminal state. Start/Position outlines come from the
actual replay recipe, and selected live detector ROI overlays are excluded. The
synthetic fixture always uses its known settings regardless of recording edits.

The existing source-ownership and drain barriers gate both interfaces. A failed
replay thread start now releases the lifecycle's run admission instead of leaving
the controller permanently busy; Widgets also reports that failure explicitly.
Decoder failures and missing/deleted sources remain visible and never normal.

Twenty new regressions cover actual OpenCV video encode/decode of generated pixels,
exact cycle accounting and neutral verdicts, cancellation/native-modal barriers,
atomic settings validation, camera/run exclusivity, repeated Start, Stop, EOF,
decoder failure, startup failure, live configuration isolation and native keyboard
Apply/Cancel interactions. Both themes are checked at 960x640 and 1440x900; settings
actions/errors and replay commands stay pinned below scrollable inputs. Synthetic
visual QA captures are under `application/rdt_monitor/dist/migration-qml-replay`.
These checks do not establish physical recording completeness or marker qualification.

Combined native Windows validation passed with **537 passed, 2 skipped** in 107.07
seconds. Both skips remain Windows symlink privilege checks. Ruff and formatting
passed for all 39 source/test Python files. Existing camera, setup, collection,
training, offline inspection and QML operation workflows remain covered.
Source/archive builds and an isolated Python 3.11 wheel installation passed. The
installed replay form rendered outside the checkout, new resources matched source,
and the original Keyence wrapper remained byte-identical. With no inference extra,
the default-paced video decoder counted one generated cycle, reached Ended, retained
NOT LIVE labeling and produced a neutral verdict with no inspection evidence.

## Frozen camera setup editor in QML

QML Start now opens `CameraSetup.qml` through `QmlMonitorDialogs` and `SetupModel`,
without constructing a Widgets setup dialog. Native file pickers keep transient
ownership and existing workstation colors. `SetupChoice` is a presentation-neutral
record shared by the legacy fallback and new editor. The controller still revalidates
the selected recipe, saves the immutable run setup and attaches the same camera and
inspection workers only after explicit confirmation.

The adapter owns a frozen image copy and a separate bounded `setup` image provider.
Live preview cannot replace setup pixels. Basic/Advanced views expose actual serial,
name/batch, recipe load, appearance-reference refresh, graphical/numeric marker edits,
recognition/timing, purpose metadata and detector decisions. Blue Start and amber
Position retain draw/move/eight-handle editing and overlap priority. The trained cyan
inspection ROI is shown read-only and cannot be changed by marker edits.

Existing `Region`, `MarkerAppearance`, `StoredRecipe`, `specimen_setup` and managed
recipe writers remain authoritative for geometry, reference capture, run-only remount
adjustments, technical-change revision requirements, model integrity and no-overwrite
saves. Loaded references are reused at unchanged geometry until explicit recapture.
Each actual edit invalidates confirmation; focusing an unchanged numeric display
does not round or modify the original geometry. Named revisions preserve family
identity, existing files, detector hashes, trained ROI and inclusive review semantics.

The modal Qt event loop preserves the prior confirmation boundary while camera timers
continue; repeated Start and main-window close are blocked during setup. Cancel starts
no run. Explicitly saved reusable revisions remain saved even if the later run draft
is canceled. The existing Widgets editor/fallback remains available and tested.

Thirty-three new regressions cover frozen ownership, serial preservation, distinct
pixel-bearing marker geometry, confirmation invalidation, invalid settings, reference
reuse/recapture, failed loads, metadata/timing/decision revision gates, model-ROI
integrity, no-overwrite/family identity, nested-session guards, and actual QML modal
accept/cancel/source-change/storage-failure behavior. Native Basic/Advanced layouts
and marker interaction cover both themes at 960x640 and 1440x900. Synthetic screenshots
are under `application/rdt_monitor/dist/migration-qml-setup`. No physical camera was
opened and no trained-model accuracy or station qualification is implied.

Combined native Windows validation passed with **570 passed, 2 skipped** in 112.12
seconds. Skips remain Windows symlink privilege checks. Ruff and formatting passed
for 42 source/test Python files. The static-camera fixture uses explicit acquisition
time so engine-load latency cannot turn the intended fresh test frame into a stale
one; the application's freshness gates remain unchanged.
Source and wheel builds passed. In isolated Python 3.11 outside the checkout, the
installed QML setup/resources matched source and the real modal confirmation returned
the actual serial with unchanged detector, trained ROI and marker references.
The smoke environment had no inference extra and opened no camera or monitoring run.
The original Keyence wrapper remained byte-identical. Visual QA waits for the native
window to be exposed before opening setup, matching the interactive launch sequence.

## Collection, review and training in QML: GUI parity complete

`CollectionTraining.qml` and `CollectionModel` now provide the last GUI workflow.
They reuse the original bounded `CandidateCollection` writer, source/crop manifests,
training CLI, export checks and recipe binding. QML never fits a model, classifies
a candidate as normal or replaces the active live detector.

Auto collection attaches the writer to the existing counting-only trial's exact-cycle
callback. Manual capture requires an explicit command, a fresh distinct frame and
the prior persisted-count barrier. Source/specimen/configuration changes are barred
during collection; camera/synchronization loss and explicit interruption produce an
incomplete set. Callback detachment and the existing close budget cover capture drain.
Active training preserves the original requirement to wait or explicitly cancel
before closing the app. Closing the panel leaves jobs running, with a main-screen badge.

Review decodes each selected source/crop in the existing worker with an optional
SHA-256 check. A complete set and review of every image are required before the
undamaged declaration and separate Train action. The existing child-process function
again verifies metadata, hashes and exact source/crop identity. Logs are bounded,
cancel/startup/failure paths stay explicit, and model exports are confined to the
selected collection. Offline export inspection and new family-recipe binding require
explicit commands; saving never activates a live model.

Recovery checks include failed manifest loads clearing stale frame/ROI drafts,
preserved picker defaults/cancellation, source/crop view changes resetting confirmation,
and invalid decisions or changed metadata never leaving a new export eligible.
During the final timing review, the writer gained `try_document()`: QML keeps the
last immutable progress snapshot rather than waiting on its metadata-persistence lock.
The writer's admission, persistence, file formats and existing blocking accessor
are unchanged. A held-lock regression verifies that the GUI progress poll returns.

All three stages have dark/light native checks at 960x640 and 1440x900, with pinned
capture/training/cancel/export actions. Generated-data tests include actual manual and
marker-triggered capture, 20 exact source/crop pairs, review/tamper guards, actual
separate-process contract export, offline handoff, explicit recipe binding and failure
paths. Synthetic visual references and software timing data are under
`application/rdt_monitor/dist/migration-qml-complete`.

The existing separate training interpreter still imports Anomalib 2.7.1.dev0,
PyTorch 2.14.1+cpu, OpenVINO 2026.4.1 and ONNXScript 0.7.2. No actual PatchCore fit
or trained-model accuracy claim was added. Final combined test/build/package results
are recorded below and in the [acceptance record](QML_Migration_Acceptance.md).

Final validation: **594 passed, 2 skipped**, covering every one of the 596 collected
test IDs exactly once. Widgets (163 passed), QML (117 passed) and backend (314 passed,
2 skipped) run in separate processes, consistent with interface-isolated parity
testing. Both skips remain Windows symlink privilege checks. JUnit records, the full
console transcript and exact coverage accounting are retained with the release candidate.
All 44 source/test Python files pass Ruff and formatting checks.

The final test audit bounded the replay admission/EOF fixture to its one required
complete cycle and allowed external interpreter/OpenVINO startup an appropriate
training-process allowance. These changes preserve exact assertions and do not relax
application freshness, capture, inspection or stop deadlines. Earlier diagnostic
single-process attempts remain in local development artifacts; the final records
identify the successful isolated validation groups explicitly.

Installed-wheel checks outside the checkout passed both without and with OpenVINO.
Each reviewed 20 persisted generated candidates, used a real child process and the
existing verifier with a contract export backend, bound a new recipe explicitly,
handed the export to offline inspection and completed QML modal setup. Missing
inference remains explicit/neutral; with the runtime, the contract graph executes.
The active live recipe/worker was unchanged, no physical camera opened and no actual
PatchCore fit or accuracy claim was made. Runtime versions, source/test/lockfile and
original wrapper hashes are recorded; the source and wheel build successfully.

## Remaining gates

### GUI refinement after the release candidate

2026-10-31 gui1.0 layout: the user-supplied arrangement now places branding,
Specimen & recipe, Camera source and Station session in one wide header, wrapping
branding above the setup row at narrower widths. The user chose to retain the
compact actual-serial field. Saved part name/batch and model type/hash/threshold/
review margin are read-only, with complete metadata on hover; missing values and
counting-only replay are explicit. The lock switch is inside the session header,
independent of the disabled workspace. Warnings and the latched alarm are above the
latest inspection in the right-hand status scroller; Start/Stop stay pinned.
The bottom row is Event log → Run summary → ROI. All 179 QML/contrast cases pass;
the manual native review is recorded in `QML_Planned_Layout_Audit.json`.
Twelve native captures cover both
themes, three widths and generated recipe/preview fixtures without camera access.
Artifacts: `application/rdt_monitor/dist/migration-qml-complete/gui-planned-layout`.

2026-10-31 setup/summary pass: Camera source and Specimen & recipe now use equal
card heights, aligned command baselines and consistent specimen/recipe value
columns. The new read-only run summary shows Pass/Fail/Review and a semantic stacked
chart. Yield is Pass / (Pass + Fail + Review), using only completed evidence-backed
results; pending/uninspected/evidence failures remain outside the denominator.
The existing latched alarm is unchanged. New cumulative lifecycle counters survive
same-run Stop/recovery and reset on a new Start. Dense alarm-bearing windows keep
the summary contained and leave Start/Stop pinned. All 620 regression cases ran in
isolated groups: 618 passed, 2 Windows symlink-privilege skips. Twenty native captures
cover both themes and six ledger states. The manual Impeccable-native audit record
is `QML_Setup_Summary_Audit.json`; artifacts are in
`application/rdt_monitor/dist/migration-qml-complete/gui-run-summary`. Generated
visual-QA data is explicitly labeled; no camera or actual-model qualification is claimed.

2026-10-31: Offline inspection, Open results and Settings now live in Tools, File
and View respectively, with their front-panel buttons removed. The header uses
a persistent Lock GUI switch (on = locked, off = unlocked), synchronized with the
Settings switch. It remains usable while the workspace/menus are locked. Existing
camera/specimen/recipe and monitoring controls remain on the operating screen.
All 60 focused QML/menu/lock/clock and palette checks passed, including menu command
dispatch in both themes and continued lock protection for shortcuts/window closing.
Eight native theme/size/lock captures and source/wheel builds passed. Artifacts:
`application/rdt_monitor/dist/migration-qml-complete/gui-menu-switch`.

The station-header refinement adds a semantic monitoring-status indicator, live
workstation-local time with time zone, and the OS session user (as selected by the
user, without separate app credentials). An explicit Unavailable fallback handles
identity lookup failure. Settings is accessible from the front panel and View
menu, with live appearance/map controls and GUI locking. The header follows the
provided RDT branding/subtitle reference. Timestamp and status updates continue
while locked. All 166 checks passed (122 QML integration plus 44 palette), including
settings, user fallback and locked clock updates. Native captures cover both themes
at three sizes; source/wheel artifacts are in
`application/rdt_monitor/dist/migration-qml-complete/gui-session`.

The following camera/lock refinement adds one state-aware Connect camera /
Disconnect camera action shared by the front button and Edit menu. Its label
tracks acquisition ownership even while setup temporarily disables commands.
Lock GUI disables the workspace, menus, shortcut actions, auxiliary dialogs and
window closing; only Unlock GUI remains active. Start/Stop are included in the
lock. Acquisition, processing, preview and logging continue. This session-local
guard is not authentication. All 164 QML/contrast checks passed (120 integration,
44 palette), including native command/lock/close/shortcut and display-update checks;
both themes were rendered at three sizes. Updated artifacts are in
`application/rdt_monitor/dist/migration-qml-complete/gui-lock`.

The subsequent Impeccable critique-polish-audit pass restores frequent Offline
inspection and Open results buttons alongside the camera/specimen/recipe and
Start/Stop controls; secondary commands remain in menus. Button/menu entries share
QML Actions. Monitoring controls are pinned outside result scrolling at minimum
height. Final validation passed 117 QML integration tests plus 44 semantic contrast
cases (161 total), with both-theme native interaction/size checks. The manual native
review record is `QML_GUI_Critique_Polish_Audit.json`; updated build/screenshots are
in `application/rdt_monitor/dist/migration-qml-complete/gui-balanced`. Windows
large-text/DPI and Narrator acceptance remain external checks. The context engine
was unavailable in the sandbox, so this pass used the skill's direct-context fallback.

Following clarification, the latest interface uses a conventional File / Edit /
View / Tools / Help menu bar instead of the Review & tools box described below.
File groups recipe loading/results/Exit, Edit groups specimen focus/camera actions,
View groups appearance/details, Tools groups offline/collection/training/replay,
and Help provides About. Camera and specimen/recipe controls remain directly
accessible. Dialog sizing accounts for the menu bar, and native pointer checks use
window coordinates. Updated artifacts are in `dist/migration-qml-complete/gui-menu`.
Final menu-bar validation passed all 117 QML integration tests (76 dialog tests
and 41 transport/operation/replay tests). Six native theme/size captures passed;
the Tools menu opened by mouse and launched offline inspection by keyboard in both
themes. Source/wheel builds passed, with packaged Main.qml verified against source.

The main QML window now groups camera selection/connection, specimen/recipe setup,
and review actions into three labeled toolboxes. Start/Stop remain alongside the
latest inspection, with the result and monitoring panels adjacent in one scrollable
column. The compact layout retains access to the same actions. Empty-source guidance,
header hierarchy and spacing were refined using the existing dark/light colors.
This UI refinement is subsequent to the original release-candidate bundle above;
the source checkout contains the updated interface.
All 117 QML integration tests passed after this refinement. Six native captures
checked both themes at 960 x 640, 1280 x 800 and 1440 x 974, including toolbox bounds,
button text fit and action visibility. The updated wheel/source and screenshots
are in `application/rdt_monitor/dist/migration-qml-complete/gui-polish`; the wheel's
packaged `Main.qml` was verified against the current source.

There are no remaining GUI workflow ports. Phases 1-5 of the revised migration plan
are implemented. The complete Widgets interface remains available as the default
fallback while phase 6 workstation/deployment acceptance and phase 7 cutover are gated.

Short synthetic software measurements and local release/rollback artifacts are retained;
they do not satisfy target-station timing or deployment acceptance. Representative
actual-model comparison, physical fault/endurance trials, DPI/operator acceptance,
clean offline deployment and an exercised station rollback remain open.
No production or 24/7 acceptance is implied.

The final release candidate is `application/rdt_monitor/dist/migration-qml-complete/rdt-monitor-qml-rc-20261030.zip`.
It retains exact test accounting, JUnit records, installed-wheel checks, source/runtime
fingerprints, GUI references and checksummed install/rollback artifacts. Both final
installed-wheel checks also passed with cached offline dependency resolution on this
host; clean-PC offline deployment remains an acceptance gate.

### Compact industrial header — 2026-10-31

Refined the operator-approved header without changing command or inspection logic.
Setup cards now use 120 px height (previously 144), 8 px internal padding and
restrained 3 px corners. The wrapped header is 172 px (previously 198), preserving
all recipe metadata, actual-serial entry, connection action and session lock.
This returns 24 px at wide sizes / 26 px at narrow sizes to the main work area.
Panel padding remains 12 px by default elsewhere.

Validation: 26 QML integration checks and 44 palette checks passed. Twelve native
captures cover empty/generated-metadata states, both themes and 960x640, 1280x800,
1440x997 sizes; no QML warnings. Captures are under ignored
`application/rdt_monitor/dist/migration-qml-complete/gui-industrial-header/`.
No physical camera, detector fitting or production qualification was involved.
