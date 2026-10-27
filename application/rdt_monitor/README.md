# RDT Reliability Monitor

Standalone PyQt6 application implementing the camera and synchronization workflow of the agreed
[architecture](../../TechRequirement/Architecture.md) and
[design direction](../../TechRequirement/design%20direction.md). Anomalib core and
Studio are unchanged. Offline and cycle-bound live exported-model inspection are
available for unqualified development. Production operation remains gated pending
qualification, authentication, verified hardware binding, retention, and recovery.

The [rollout plan](../../TechRequirement/Rollout%20Plan.md) records the deployment
sequence: vision-marker synchronization for the Phase 1 supervised pilot, followed
by read-only IPC/controller feedback integration in Phase 2. Controller-triggered
inspection is planned, not currently implemented.

## Display Settings

The monitor opens centered within the current screen's usable area, up to 1440 x 900.
It remains freely resizable rather than locking an aspect ratio. Short windows keep
monitoring controls accessible in a scrollable right column; camera images retain
their own proportions. Window sizing uses Qt's logical pixels and excludes the taskbar.
Drag the dividers to adjust camera/control widths and the monitoring/evidence heights.
The latest-inspection ROI remains aligned with the control column when resizing or
dragging the upper divider. Details and the event log remain independently scrollable.

Choose **Settings** at the top right of the desktop window, beside **Details**.
Under **Basic settings**, choose **Dark** or **Light** display mode. The change
applies immediately and is remembered for the next launch, independently of recipes.
Restart an already running monitor to load this new button; stop any active run safely first.

## Simple Recipe And Specimen Setup

Camera setup now opens with **Basic** and **Advanced** tabs. These are UI pages,
not two configuration files that operators must manage.

- **Basic**: load a recipe, identify the actual specimen by serial number, optionally
  enter its name/batch, check the frozen camera image and cyan inspection ROI, and
  adjust Start/Position marker regions if needed. **Use current marker appearance**
  recaptures both references from the frozen frame; it does not relocate the
  inspection crop. Confirm the setup and test-cycle check, then choose **Use for this run**.
  Confirmation is an operator declaration, not an automated test or qualification.
- **Advanced**: open directly without a username or password, name the
  recipe by purpose (for example, "A-type standard bending"), add purpose notes,
  and configure recognition, timing, or detector decision settings. The inspection
  ROI remains the trained crop; changing the model/crop requires the separate
  collection/training workflow and validation. **Save recipe** writes a new revision
  to a new filename; it never overwrites an existing recipe.
- Loading a recipe no longer replaces the current specimen serial with the serial
  of its training specimen. Enter the actual serial before Start; it can also be
  corrected on Basic. Start still opens setup and requires confirmation.
- A named schema-5 recipe contains a stable family identity, unique revision, friendly
  name and notes, one detector binding (or counting-only mode), and default specimen
  setup. Schemas 1-4 remain readable. Display names and serial numbers are metadata,
  not filesystem paths. Existing recipes are not automatically rewritten.
- Marker-only changes for A-002 reuse the exact A-family detector/ROI/thresholds.
  They do not modify A-001's reusable recipe. Technical changes to a loaded recipe require
  saving a new revision before use. A changed mark does not prove that the
  specimen pose or inspected surface is compatible with the model.
- Before accepting camera cycles, every run records a complete
  `run-<id>-setup.json` under `inspection-results`, including specimen details,
  marker references, detector/family identities, setup identity, and source-recipe
  hash. Storage failure blocks starting. Inspection evidence references this snapshot
  and its hash. These files preserve the run configuration, not reboot recovery.
- New revisions of an already bound recipe must be saved beside the original recipe
  so they can reference the same detector bundle without copying/retraining it.
  Keep all referencing JSON files and the bundle together when moving or backing them up.

### Recipe Editing Access

Recipe editing and saving do not require application login, Windows account
authorization, or a setup-admin group. Advanced settings are immediately editable;
training's **Save inspection recipe** also works without a credential prompt.
There is no application admin username or password.

Anyone able to use the desktop application can change and save recipes. Recipe
validation, explicit run confirmation, new-filename revision protection, model
integrity checks, and frozen run snapshots remain in place. Deploy only trusted
recipe/model files and protect workstation access. Station-wide authorization,
tamper-resistant storage, and production approval remain separate qualification work.

## Run

Recipes are kept in the repository's `recipes` folder (`D:\anomalib\recipes`
on this workstation). All recipe Load/Save dialogs open there by default.
Use `coil-01-inspection.json` for the model-bound Coil inspection setup;
`coil-01-counting.json` is the synchronization-only setup. Keep generated
`detector-<id>` folders beside their recipes; they contain the bundled model.

From the repository root with `uv` installed:

```powershell
uv sync --project application/rdt_monitor --group dev
uv run --project application/rdt_monitor rdt-monitor
```

## Implementation Status

The agreed 24/7 operator workflow is being implemented in increments. Run lifecycle,
manual/automatic training collection, and model-bound cycle inspection are available;
this is **not** a qualified production inspection implementation.

- **Load recipe**, **Start**, and **Stop** are directly on the main screen,
  beside the camera connection controls. **Load recipe** opens the JSON picker
  without requiring a camera connection or a pre-entered specimen ID. It selects
  settings without replacing the actual specimen ID or starting a run.
  **Start** opens synchronization setup against the connected camera; source
  dimensions and supported settings are checked before confirmation starts counting.
  These are working development controls, not production defect inspection.
  Existing specimen-linked JSON files remain development recipes, not approved
  reusable inspection recipes. Production inspection still requires verified camera
  binding, approved recipes, qualified inference, durable evidence/retention, and authorization.
- **Collect and train** is available in the Inspection menu, keeping setup work
  out of everyday operation. **Open inspection results** opens the central
  results directory in File Explorer without changing the active run.
  The default operator view shows the camera, short recipe name, latest verdict,
  latched defect count, cycles and Running/Stopped status. **Details** reveals camera
  configuration, frame/timing information, run identity, score/settings, evidence
  status, inspection counts and synchronization. The **Event log** is always visible
  across the bottom; opening Details places technical information beside the log.
  Details are hidden initially, but logging and evidence recording continue unchanged. Actionable
  faults and uninspected inspection-cycle counts remain visible when Details is closed.
  Counting-only runs do not show a missing-inspection alarm for every counted cycle.
  Keyboard navigation follows specimen, connection, recipe, run controls, results,
  and Details. Compact collection windows
  scroll the settings panel rather than forcing the dialog beyond the screen.
- The industrial monitoring layout places the framed camera view on the left,
  a circular **Inspection result** on the right, and **Monitoring control** below it.
  The circle means the latest inspection only: **Normal**, **Needs review**,
  **Defect**, or **Not inspected** (grey). It never means the station is healthy.
  The separate defect alarm remains latched after later normal cycles.
  Start/Stop, Load recipe and results are always visible in the control panel.
  The camera header independently reports connection/freshness; a live preview does
  not imply an inspected or normal specimen. Details remain hidden initially.
- The bottom-right **Latest inspection ROI** shows the exact crop from the latest
  completed cycle, with a red anomaly heatmap marking model-suspected defects
  (stronger red means higher anomaly, not confirmed physical damage). The live
  camera independently outlines the recipe's **Inspection ROI** in cyan.
  The crop stays visible while the next cycle is pending and after Stop, resets on
  a new run or recipe, and shows unavailable when a newer cycle has no model result.
  An evidence failure still makes the verdict grey even if its model heatmap is available.
- The display separates **Visually observed cycles** from **Inspected** and
  **Uninspected**. Counting-only/replay cycles remain uninspected. Model-bound recipes
  inspect exact cycle-return frames; only valid results with committed local evidence
  count as inspected. Preview or marker return alone is not a successful inspection.
- **Stop** bars new observations, drains already accepted cycle events,
  and waits asynchronously for an in-flight capture callback. Disconnect follows
  the same finalization path before requesting device release. The current
  three-second development finalization deadline is not a qualified scenario setting;
  exceeding it is logged explicitly, not reported as a clean stop.
- Run state/counters remain memory-only. The lifecycle supports readiness-gated
  recovery segments, bounded pending inspection tickets, and explicit deadline
  failures for future station services. Automatic hardware recovery is **not**
  enabled against an unverified OpenCV index.

Remaining increments: station-level durable records/retention, Windows-authenticated
station-wide Admin configuration, reusable approved recipes and validation, and verified
automatic recovery/endurance testing. A reboot
does not yet restore an interrupted run. No approval, durable evidence, or 24/7
coverage guarantee is implied by this increment.

For the connected USB camera, leave **Camera index** at **0** and choose
**Connect camera**. Choose **Disconnect camera** to release it. Close other
camera applications first if the device is busy. Camera and replay sessions
cannot run together; switching sources requires stopping the active session.

The live preview reuses the existing
[Keyence wrapper](../../tools/keyence_nr_cm01_api.py), including its backend
fallbacks. The same unmodified wrapper ships inside the standalone package.
Indices are generic OpenCV device indices, not verified manufacturer identities.
Plain camera preview does not classify markers or count cycles. Live marker
recognition requires explicit synchronization setup below. Neither mode
assesses defects or saves evidence.

Under **Diagnostics**, choose **Synthetic fixture**, then **Start replay** for an explicitly labeled,
generated marker sequence. This is not camera footage or anomaly training data.
Alternatively choose a local recording and configure its two normalized regions
under **Replay settings** before starting. Settings are frozen for each replay run.

Live camera setup defaults to **Mark appearance**. Draw each region
tightly around the visible mark and its immediate surround; the frozen image
supplies separate grayscale references for Start and Position. OpenCV normalized
appearance correlation of at least 0.80 means present, at most 0.40 means absent,
and the intervening band is uncertain, never evidence of disappearance. Brief
uncertainty waits for clear visibility; continuous uncertainty beyond the cycle
timeout faults synchronization. Flat references are rejected. Pose matching searches
only within each selected region: up to four pixels in the normalized 32x32 patch,
rotations -15/0/+15 degrees, and scales 0.9/1.0/1.1. Full overlapping patches validate
candidates, and observations below 40% of reference contrast are rejected.
These limits still require physical motion/lighting qualification; they are not
defect scores or probabilities, nor support for arbitrary deformation.

The explicit **Brightness (legacy)** mode classifies bright-pixel occupancy: at least 80% above
the brightness cutoff means present, at most 20% means absent, and the intervening
band is ambiguous. These numbers, the fixture's two-frame debounce, 0.2-second
observation-gap limit, and four-second cycle timeout are **unqualified fixture
parameters**, not factory defaults. Arbitrary recordings need explicit setup;
the recognizer is not a general solution for occluded or changing marker shapes.

Install IBM Plex Sans or Inter to use the intended typography. Until available,
Qt uses the installed platform font. Font redistribution must be reviewed before
packaging. The palette and native window behavior are already applied.

## Camera Synchronization

Synchronization establishes observed cycles and positions, not defect inspection.
The appearance recognizer and timing settings still require physical-machine qualification.

1. Connect the camera and wait for a fresh preview. Enter the actual **Specimen ID**.
  Choose **Start** on the main screen to configure synchronization
  manually, or **Load recipe** to select a stored recipe
  (also available before connecting). Loading does not start counting; choose
  **Start** to review and confirm the loaded settings against the live camera frame.
2. Drag on the frozen image to draw the blue **Start marker** rectangle.
  **Position marker** is then selected automatically; drag to draw its yellow
  rectangle. No regions are preselected.
3. Click a rectangle or its role selector to select it. Drag inside to move it;
  drag any of its eight corner/edge handles to resize it. Geometry stays inside
  the image, excluding black margins. The trash icon clears only the selected
  region so it can be redrawn. Partial overlaps are allowed; the selected role
  wins overlap hit-testing. Identical Start/Position rectangles are rejected.
4. Keep **Mark appearance** selected for physical cross/ring marks and
  make sure both marks are visible in the frozen image. Review stable observations for both marks, host-read gap
  limit, and cycle timeout. The initial numbers are experimental values, not
  qualified machine settings. Bright pixel cutoff applies only to legacy mode.
5. Confirm the regions and timing, then **Start synchronization** in the setup dialog. This becomes
  available only when both regions, timing settings, and confirmation are valid.
  Changing geometry or settings clears confirmation; selecting without editing
  does not. Cancel leaves the current session unchanged. Run settings and specimen ID are locked.
6. Begin with both marks visibly present. As the sample bends, both marks must
  become stably absent and then stably return. Their return completes **one cycle**
  and validates the inspection position. They may leave or return at different
  times, but both must return before counting. Holding them visible, moving only
  one, or starting midway through a bend does not count a completed cycle.
  Position observations remain unknown defect assessments, not successful inspections.
7. Choose **Stop** to stop synchronization while keeping a healthy camera preview
  connected. Stopping ends the current run; starting again requires fresh setup and confirmation.
  If synchronization is lost, inspection stays stopped but a healthy camera preview
  continues. Review the stated reason, then choose **Restart** on the main screen and
  confirm setup again. Restart creates a new run and resets its observed-cycle count;
  cancelling retains the original lost state. Recovery never happens automatically.
8. Choose **Disconnect camera** to release the device. Actual camera failures and
  stalled reads require explicit reconnection after the worker releases the camera.

Open **Advanced coordinates** for precise or keyboard-based region adjustments.
X/Y are percentages from the image's top-left; W/H are percentages of its width
and height. Numeric fields and graphical geometry stay synchronized. Invalid
values show a current error, which clears when the values are corrected.

### Save and Load Recipes

In the development synchronization setup dialog:

1. Draw both regions and review settings. To create a reusable recipe, open
  **Advanced**, enter a purpose-based name, and click **Save recipe**. Select a new
  JSON filename and location. Saving keeps setup open and does not start a run.
2. To reuse settings, click **Load recipe** and select the saved JSON file.
  The dialog restores both regions, recognition mode/references, brightness thresholds,
  debounce, and timing limits, but preserves the actual specimen serial.
3. Check placement against the current frozen camera image, confirm the regions
  and inspection surface, exercise test cycles, then choose **Use for this run**.
  Cancelling setup leaves the current run and main-screen ID unchanged.

Recipes persist across application restarts at the selected file location. They
include a schema version and source image dimensions; mismatched dimensions,
invalid files, and unsupported settings are rejected without changing setup.
Reusable recipe saves use new filenames and atomic writes, preserving previous
revisions. Filenames are selected explicitly, not derived from specimen IDs.
Appearance recipes contain two small grayscale reference patches, not full camera
images, inspection evidence, authentication, or qualification approval. Older
brightness-only recipes load explicitly in legacy mode. To migrate one, open
Advanced and select **Mark appearance** while both marks are visible, review the boxes,
and save a new recipe revision. Matching image
dimensions alone does not establish camera identity or correct physical placement.

Startup remains **Waiting for fresh marker transition** without a cycle deadline
while establishing the initial view of both marks and waiting for bending to begin.
Waiting does not establish monitoring coverage or count a cycle. The first
debounced departure starts a deadline for the first return. Thereafter the cycle
timeout limits the interval between completed cycles and resets on each completion.
Frame continuity and the bounded uncertainty deadline remain active during startup.

Each returned camera frame is evaluated in capture order before preview
replacement. Frame/time discontinuity, unresolved mark uncertainty, overdue cycles,
camera geometry changes, recognition errors, or event-backlog overflow latch a
synchronization fault without releasing a healthy camera. Marker processing stops
until explicit restart. The UI logs the fault once and reports synchronization
loss separately from camera health. Pending events are bounded to 128; the session log remains memory-only.
Host read-completion timing does **not** establish sensor timing or prove that
the driver delivered every sensor frame. Real marker geometry, visibility duration,
position validity, sustained throughput, and recovery still require qualification.

## Collect, Review, Then Train

Training is never triggered by collecting enough images or completing a cycle.
Open **Inspection > Collect and train** for the explicit development workflow:

1. Connect the camera and enter the actual specimen ID. In the **Collection** tab,
  select **Cycle cutoff: Auto** or **Manual**. Choose **Use camera frame** and draw
  the independent **Training ROI** around the specimen surface. Select 20-30 images
  and an output directory, then **Collect candidates**.
2. Capture according to the selected mode:
  - **Auto** (default): configure/start synchronization as above. One original PNG
    and its exact ROI crop are saved per completed two-mark bending cycle. Capture
    takes pixels directly from the synchronization worker, not the replaced preview.
  - **Manual**: synchronization is not required. Complete a physical cycle, hold the
    specimen at the intended inspection/return position, then click **Capture cycle**.
    Each click declares one completed cycle and saves the current camera frame plus
    its ROI crop. The collection view shows the live camera during capture. Wait
    for the previous image to save before the next click. The same frame cannot be
    saved twice, and frames older than one second are rejected (a development
    freshness safeguard, not a qualified machine timing). There is no automatic
    cycle timeout in manual mode; marker faults do not block manual captures.
    These counts are explicitly **operator-declared**, not visually detected cycles,
    and do not alter the main window's observed-cycle or inspection counts.
  The mode, settings and collection identity are locked until collection finishes.
  The collection metadata records `cycle_mode`; older sets default to `auto`.
  The writer stops accepting candidates at the target; it does not label them
  normal, change the live assessment, or train anything.
3. In **Review**, open every candidate using the numbered selector or arrows.
  Toggle **ROI crop** to inspect the actual training input. Only after every
  image has been opened can you check **Specimen remained undamaged**. Leave
  this unchecked if damage occurred; collect a new verified set instead.
4. Open **Training** and explicitly click **Train PatchCore**. This is the only
  GUI action that requests fitting. The separate process verifies metadata and
  source/crop hashes, records the explicit confirmation, snapshots the ROI crops
  into a new versioned job, then fits and exports a CPU OpenVINO model.
  Anomalib checkpointing remains enabled; Lightning checkpoints are stored under
  that job's `fit` output directory, separate from the exported OpenVINO model.
  Export uses ONNX opset 18 with a fixed single-image RGB input (`1 x 3 x 256 x 256`)
  to match the monitor detector. A real OpenVINO inference check must pass before
  the training job reports success.
5. Inspect process progress/failure in the persistent log. **Cancel training**
  stops only this application's training child. **Inspect export** selects a
  successful export in the offline inspection window; it never activates a live
  detector or qualifies a production recipe.
6. To use the trained model for cycle inspection, choose **Save inspection recipe**
  in the Training tab, as described below. This is explicit; completing training
  does not silently change an active run.

The collection window displays **Visually observed cycles** separately from
**Saved candidates**, plus the current synchronization state or capture blocker.
In Auto mode, use **Configure synchronization** when no trial exists, or **Restart synchronization**
after a latched fault. Restart requires explicit setup confirmation and starts a new
run; it does not silently resume an interrupted collection or reset its eligibility.

Each generated `collection-<id>` directory contains `collection.json`, ordered
source/crop PNG pairs, specimen/run/recipe identity, ROI/source dimensions, cycle
and frame numbers, host read-completion times, host UTC collection times, and
integrity hashes. Specimen IDs never determine filesystem paths. Use **Load
collection** to resume review later or after an application restart. Reloading
clears the GUI's review/confirmation state and requires another explicit review
before requesting training. Completed candidates remain **Awaiting confirmation**
until an explicitly requested training job verifies and records the confirmation.

Stopping early, losing camera coverage (or synchronization in Auto mode) before the target, writer
backlog overflow, or storage failures leave an incomplete/unavailable set that
cannot train. Partial files may remain for diagnosis; they are not successful
collection or production evidence. Closing the collection window does not stop
its background work. Application exit requests collection shutdown; a blocked
writer is subject to the existing bounded development shutdown, not a durable
evidence guarantee. Active training requires completion or explicit cancellation
before exiting the main window.

**Training Python** must point to a trusted interpreter with this repository's
Anomalib, Torch, OpenVINO and ONNX export dependencies, separate from the desktop
environment. The repository interpreter is selected when available. To prepare
that existing environment without installing Torch into the monitor:

```powershell
uv pip install --python .venv/Scripts/python.exe "openvino>=2025,<2027" onnxscript
```

For offline backbone loading, place the official
[`timm/wide_resnet50_2.racm_in1k` weights](https://huggingface.co/timm/wide_resnet50_2.racm_in1k/resolve/main/model.safetensors)
at `models/backbones/wide_resnet50_2.racm_in1k/model.safetensors` under the repository
root. Training prefers this local file, logs its path, and does not request Hugging
Face weights in this mode. An invalid local file fails explicitly rather than
falling back to random weights or a network download. When the file is absent,
the existing pretrained HTTPS/cache loading remains available. Restart a training
job after placing the file; an already running download does not switch sources.

Normal-only fitting uses a reproducible internal normal-image split
for preprocessing/postprocessing statistics; it does not measure defect recall
or false alarms on independent specimens. Twenty to thirty early-cycle images
are an initial candidate set, not proof of an undamaged specimen or adequate
diversity. Normal confirmation is an operator declaration, not authenticated
supervisor approval. The model remains unqualified until separate specimen-level
normal/defect validation. No online learning or automatic model replacement occurs.

## Cycle-Bound Live Inspection (Unqualified)

1. In **Collect and train > Training**, after a successful export, review the
   **Unqualified threshold** (default 0.5) and **Review margin** (default 0.05).
   These are operator settings, not validated defect sensitivity or confidence.
   The inclusive needs-review band is threshold minus/plus margin; bands extending
   outside [0, 1] are rejected instead of clipped.
   The live band preview updates as you edit and flags invalid settings before saving.
   To reuse a model after restarting, **Load collection**, then **Load trained model**
   and select that collection's training-job `weights/openvino/model.xml`.
2. Click **Save inspection recipe**. Select the saved synchronization recipe for
   the same camera geometry and specimen pose, then choose the new recipe filename.
   The app copies the XML/BIN into a generated `detector-<id>` directory beside it.
   Schema 4 stores relative model references, model/weights SHA-256 identities,
   the exact training ROI, threshold, and review margin. Existing schema-3 inspection
   recipes load with margin 0.05; their files are not automatically rewritten.
   This does not approve the model.
   Keep the JSON and its detector directory together when moving the recipe.
3. Stop any active run. On the main screen, **Load recipe** selects this new JSON
   without starting work. Connect the camera, verify the specimen ID and pose, then
   **Start**. Confirm the marker setup and choose **Start unqualified inspection**.
   Basic marker-only adjustments are saved with the run while retaining the model
   binding; technical changes to a loaded recipe require saving a new recipe revision in Advanced.
   Legacy recipes without a detector still count only.
   The main screen and setup show **Unqualified inspection** for a model-bound recipe,
   or **Counting only - no detector** for a synchronization-only recipe. The selected
   filename/mode remains visible after connecting and starting. Hover the recipe
   summary to check Start/Position regions, recognition mode, model path/hashes,
   inspection ROI, threshold, and review margin. Missing bundled XML/BIN files reject loading rather
   than silently falling back to counting. Model integrity is checked by the worker.
4. Each completed full two-marker disappearance/return cycle submits its exact
   stable-return frame to a separate CPU OpenVINO worker. The worker loads the
   bundled model once and verifies its hashes. The bounded queue accepts four waiting
   frames; overload and inference/output errors are explicit **Uninspected** outcomes.
   Candidate collection cannot replace the live inspection capture callback.
5. The prominent **Latest cycle verdict** indicator shows the latest completed
   cycle, without changing color while the next cycle is pending:
   - **Green**: no defect detected, score below threshold minus margin.
   - **Yellow**: needs review, score inside the inclusive review band. With threshold
     0.50 and margin 0.05, both 0.45 and 0.55 are yellow. Comparisons use the same
     float32 precision as model outputs.
   - **Red**: defect detected, score above threshold plus margin.
   - **Grey**: no inspection yet, counting-only, failed inspection, or incomplete evidence.
   Yellow is never a waiting/error indicator. Review results with complete evidence
   count as inspected, but only red model verdicts latch the separate **DEFECT ALARM**.
   A later green/yellow verdict does not clear that alarm. A red model verdict still
   latches it if evidence saving fails and the latest cycle becomes grey.
   **Stop** retains the latest result and alarm; a new run resets them.
   The screen also reports score, pending/inspected/uninspected counts, and latched
   defect count. Inspection failures retain a visible fault; cycle counting can
   continue in a degraded unqualified run.
   **Inspection position** logs only that inference is pending; **Cycle inspection**
   records its eventual outcome. Counting-only positions explicitly report that no
   detector is bound.
6. New inspection results are stored separately from recipes under the repository's
   `inspection-results` directory (`D:\anomalib\inspection-results` on this workstation):

   ```text
   inspection-results/
     run-<id>/
       green-normal/
         cycle-00000001/
           source.png
           roi.png
           map.png
           result.json
       yellow-review/
         cycle-00000002/...
       red-defect/
         cycle-00000003/...
       grey-uninspected/
         cycle-00000004/...
   ```

   Cycle numbers are zero-padded for sorting within each verdict group. Images
   are the original source, exact ROI crop, and normalized anomaly map (not a
   confidence visualization). JSON records include run/cycle/frame/time, recipe/model
   identity and hashes, ROI, threshold, margin, score, final outcome, original model
   verdict, errors, and explicitly missing images. Inference failures save available
   source/ROI images; overloaded cycles may have metadata only. Counting-only runs
   generate no inspection evidence folders. Files are staged and the cycle directory
   is moved into its final group only after its result record is written.
   Existing `live-runs` evidence is left untouched.

   Evidence failure cannot count as a successful inspection. A grey failure record
   is attempted, including after the image budget is exhausted when metadata still
   fits. If even the record cannot be saved, the UI explicitly reports this; it
   never claims success. The experimental per-run evidence ceiling is 512 MiB,
   including failure records. No files are automatically deleted.
   There is no station-wide retention policy, disk reservation,
   station-level durable failure audit, or interrupted-run restoration yet.
7. **Stop** bars further observations and drains accepted inference within the
   existing three-second development deadline. Unfinished cycles are explicitly
   uninspected on timeout. Preview continues; disconnect follows finalization.
   Late results never update a later run. An uninterruptible inference thread can
   finish after the deadline; it attempts grey deadline-failure records without
   publishing late UI results. A new Start remains blocked until it exits.

Install the monitor's optional inference runtime if needed:

```powershell
uv sync --project application\rdt_monitor --group dev --extra inference
```

This is a commissioning tool, not qualified 24/7 operation. The default threshold
and normal-only training are not evidence of defect recall. Validate independent
normal and defective specimens before trusting the model.

## Offline Defect Inspection

No bending machine or connected camera is needed. Install the optional CPU runtime
and launch the standalone application from the repository root:

```powershell
uv sync --project application/rdt_monitor --extra inference --group dev
uv run --no-sync --project application/rdt_monitor rdt-monitor
```

Close the running monitor before synchronizing its environment on Windows. The
inference extra installs OpenVINO, not Torch or the full Anomalib training stack.

1. Open **Inspection > Offline images** and select **Open image**. Image reading
  and decoding run in a background job; encoded files above 128 MiB are rejected.
2. Select **Load model** and choose a trusted Anomalib OpenVINO `.xml` export
  with its matching `.bin`, or an `.onnx` export. Checkpoints and pickle files
  are not accepted. No detector or fabricated score is supplied at startup.
3. Draw the **Inspection ROI** around the specimen surface, independently of
  synchronization marks. Move/resize it with the existing graphical handles,
  edit percentage coordinates, or select **Full image ROI**. Match the cropping,
  camera position, lighting, and image orientation used for training.
4. Set the explicit, unqualified **Threshold**, then select **Inspect image**.
  The model loads and executes on CPU in a background job. Controls lock until
  completion. Scores are normalized anomaly scores, **not probabilities**;
  the initial 0.500 threshold is not a validated production setting.
5. Toggle **Heatmap** to compare the result against its exact source snapshot.
  The map is placed only inside the inspected ROI. Changing the image, model,
  ROI, or threshold invalidates the previous result and disables saving.
6. **Save image** writes the currently displayed source/heatmap composite to an
  explicitly selected PNG. This manual snapshot is not authenticated inspection
  evidence and does not include an audit record or model provenance.

The export must accept one RGB float image in NCHW layout, with embedded Anomalib
preprocessing/postprocessing and named `pred_score` and `anomaly_map` outputs.
The adapter converts BGR pixels to RGB, scales to [0, 1], and resizes the crop
using OpenCV linear interpolation only when the export fixes its input dimensions.
Dynamic spatial inputs retain the crop dimensions. Maps are resized back to the
exact inspected source region. Rejecting incomplete, nonfinite or unnormalized
outputs leaves the assessment **Unknown**, never normal.

Import and inference are single-use daemon jobs with bounded result mailboxes;
there is no inference cancellation or qualified execution deadline. Closing the
offline window does not cancel its current job. Exiting the application may
abandon that job. These are offline development semantics, not live monitoring
or production shutdown guarantees.

### Prepare a Specimen Model

Training is separate from the lightweight monitor environment. Use this
repository's Anomalib environment with its `openvino` extra and the standard
`Folder`, `Patchcore`, and `Engine` APIs. Train on genuine normal specimen ROI
images. Include representative defects and independent normal images for
calibration/evaluation; separate physical specimens/capture sessions rather than
splitting neighboring frames from one recording. Do not use the mark-recognition
fixtures as anomaly training data.

For a prepared folder containing `train/good`, `test/good`, and `test/defect`,
the following API sequence fits, evaluates, and exports the model:

```python
from anomalib.data import Folder
from anomalib.engine import Engine
from anomalib.models import Patchcore

data = Folder(
    name="rdt-specimen",
    root="datasets/rdt-roi",
    normal_dir="train/good",
    normal_test_dir="test/good",
    abnormal_dir="test/defect",
    train_batch_size=4,
    eval_batch_size=4,
    num_workers=0,
)
model = Patchcore()
engine = Engine(accelerator="cpu", devices=1, default_root_dir="results/rdt", logger=False)
engine.fit(model=model, datamodule=data)
engine.test(model=model, datamodule=data)
engine.export(model=model, export_type="openvino", export_root="results/rdt-export", input_size=(256, 256))
```

Run training in a guarded Python entry point (`if __name__ == "__main__":`) on
Windows. The Folder datamodule manages its validation/test split; review those
splits and retain a separate specimen-level acceptance set before selecting a
threshold. Initial backbone weights may require a download. No training or
accuracy result is claimed here: a suitable dataset and trained specimen-specific
export have not yet been provided. Inference tests execute a tiny contract graph
through real OpenVINO; that graph checks plumbing, **not defect detection quality**.

Offline results never update the main window's specimen condition, live cycle
history, or evidence status. Production monitoring stays disabled.

## Implemented

- Typed source-frame, synchronization, inspection-placeholder, and run records.
- Camera preview with explicit index selection, connection, and disconnection;
  one worker owns all camera operations and a one-frame mailbox bounds preview
  memory. Read-completion times use the host monotonic clock, not a sensor clock.
- Live/stale labeling and latched opening/read failures. Opening and read stalls
  default to 10-second and two-second faults respectively. There is no automatic
  reconnect. A blocked driver cannot be forcibly interrupted; reconnect stays
  unavailable until its worker exits.
- Initial two-mark visibility, independent disappearance/return debounce, exactly
  one count and position event per completed bending cycle, and latched coverage/timeout faults.
- Sequential worker processing: replay and explicitly enabled live synchronization recognize
  every returned frame before preview replacement. The preview mailbox holds one
  frame; pending events are bounded.
  Overflow is a visible fault, not silently lost cycle history.
- Native desktop window with separate health, synchronization, observed cycles,
  explicit unknown/uninspected outcomes, model-bound cycle results, and a bounded session log.
- Camera-first operator controls with replay tools under Diagnostics, required specimen
  identity, and explicit synchronization recovery independent of camera acquisition.
- Portable specimen-linked JSON recipes with explicit save/load, source-dimension
  validation, and mandatory confirmation before activating loaded settings.
- Explicit new-run/restart commands, locked run settings, nonblocking stop and
  asynchronous close. A blocked source gets up to three seconds to close; a
  daemon source worker may be abandoned on process exit. This is acceptable only
  for unqualified development, not a production shutdown contract.
- Deterministic replay/camera lifecycle tests and native Qt rendering checks.
- Independent offline image/model selection, single specimen ROI, unqualified
  score/threshold assessment, source-aligned heatmap, and explicit PNG snapshots.
  Image import and CPU model inference run outside Qt; unavailable or stale
  outputs cannot become normal assessments.

The current worker uses Python threading with bounded queues and a Qt UI poll
timer, not per-frame Qt signals. Synchronization executes directly in the owning
replay/capture worker, before any preview drop. It is independent of UI transport;
slow recognition can still affect acquisition and must be measured. Replay pacing uses
source frame index / advertised FPS, not verified hardware acquisition timestamps.
EOF/decode boundaries do not certify recording completeness. Counters describe
only visually observed cycles.

## Deliberately Unavailable

Production monitoring remains disabled. There is no qualified live synchronization or
defect inference, protected/versioned qualification store, supervisor authentication, incident/acknowledgment
workflow, station-level SQLite/audit persistence, clip recording, retention, or installer yet.
Unqualified cycle inference and local successful-cycle evidence are available only with
an explicitly loaded model-bound recipe; reaching a position alone is not a successful
inspection. Run state and the session log remain memory-only and disappear on exit.
No simulated normal/anomaly scores or heatmaps are presented as real results.

The session table and inspection list retain at most 500 entries, intentionally
not a complete durable audit. The editable synchronization and diagnostic settings
are not a protected production setup mode. License
review, including PyQt6 GPL/commercial obligations, is required before distribution.

## Validate

Collection/review/training workflow validation on 2026-10-15: 226 standalone
tests passed; one Windows symlink-creation test was skipped for missing privileges.
Checks cover exact synchronization-to-saved-frame identity, source/ROI pairing,
target stopping without automatic training, persisted reload/review, explicit
undamaged/manual-Train gates, metadata/image tamper rejection, storage failures,
and failed child-process startup. Native Review/Training captures fit their actual
1024x768 and 1440x900 client sizes. Ruff, editor diagnostics, and source-to-wheel
build passed. The separate training interpreter imports Anomalib, OpenVINO
2026.4.1 and ONNXScript 0.7.2. No physical images were collected and no model was
fitted; backend-orchestration tests use an injected contract backend, not fake
accuracy results. The independent finish review could not complete because of a
network error; native/manual checks are not represented as reviewer approval.

Offline-inspection validation on 2026-10-15: all 201 standalone monitor tests
passed, including actual OpenVINO CPU execution for dynamic/fixed-size contracts,
incompatible input/output rejection, asynchronous import/inference, stale-result
invalidation, snapshot alignment, and native Windows layouts at 900x650 and
1280x800. Ruff and editor diagnostics passed; the offline source-to-wheel build
succeeded. An independent fallback reviewer confirmed the background-import fix;
the named finish-review/documentation agents were unavailable, so the existing
design was preserved and this feature boundary documented directly. Contract
graphs test execution plumbing only; no trained-model accuracy is established.

Physical-camera acceptance is **not complete**. A live-camera report on
2026-10-15 showed zero observed cycles and a latched ambiguous-marker fault with
thin pink Start/Position marks. Brightness occupancy did not recognize their
appearance. The replacement captures the actual selected marks. A bounded live
check processed 540 frames with both reference matches around 0.96 and no fault,
but observed no disappearance/reappearance and therefore zero cycles. This is
live presence evidence, not successful physical-cycle acceptance.
Automated test success below establishes
software regression coverage only, not successful cycle counting with these marks.
Do not treat this milestone or its completed implementation checklist as proof
that the monitor works on the physical station.

Real-sample regressions use separate reference/observation frames captured from
the actual camera. They cover cross/ring discrimination, unmarked surface
rejection, controlled two-mark bending sequences, bounded return-pose changes, startup, recipe
reference persistence, and native setup capture/restore. The constructed sequence
does not substitute for measured physical movement through the position limits.

Paired-cycle update on 2026-10-15: all 163 monitor tests passed on native Windows
Qt, including two-mark completion, incomplete startup, delayed/staggered returns,
transient versus unresolved uncertainty, pose changes, and counter/event integration.
A live-camera attempt was interrupted before a complete result was obtained;
physical bending-cycle acceptance remains open.

Recipe milestone validation on 2026-10-15: all 136 monitor tests passed on
native Windows Qt. The synchronization core, bounded replay/camera workers,
desktop controls, graphical region editor, explicit fault recovery, and
specimen-linked recipe save/load are implemented and covered. Recipe tests
include fresh-dialog restoration, confirmation and cancellation, malformed files,
source-dimension mismatch, atomic replacement failure, and specimen/run identity.
These automated checks do not qualify physical marker recognition, sustained
hardware acquisition, defect inference, authentication, or durable evidence.

Milestone validation on 2026-10-14: all 53 tests passed on native Windows Qt,
including replay synchronization, camera lifecycle/timeouts, widget/run integration,
font coverage, and camera/replay rendering at 1024x768 and 1440x900. Native
screenshots were inspected for readable text and layout; a minimum-size sidebar
clipping issue now has a containment regression check. Source-to-wheel builds
passed and the packaged Keyence API matched the original file byte for byte.

Live-trial validation on 2026-10-15: all 68 monitor tests passed on native Windows
Qt, including all-frame processing despite preview replacement, explicit region
confirmation, settings locking, synchronization faults, and reconnect behavior.
Trial and setup renders cover 1024x768 and 1440x900. These trial checks use
deterministic synthetic pixels, not a qualified physical-machine marker sequence.

Graphical-editor validation on 2026-10-15: all 87 monitor tests passed. Coverage
adds native mouse-driven drawing, moving,
all eight resize handles, overlap priority, selected-region clearing, letterbox
clamping, coordinate synchronization, confirmation invalidation, and dialog
resizing. Collapsed and expanded coordinate panels are rendered at both sizes.

Operator-recovery validation: all 98 monitor tests passed on native Windows Qt.
Regressions cover healthy capture surviving timeout, ambiguity, geometry, recognition,
and backlog faults; required/locked specimen identity; exactly-once fault logging;
explicit restart and cancellation; Diagnostics replay; and recovery layouts at both sizes.
These checks use deterministic synthetic pixels, not qualified physical marker sequences.

Use the native Windows platform for visual acceptance. The offscreen plugin on
this installation produced glyph boxes despite passing a basic font-coverage
assertion; a nonblank image alone does not prove readable text. Headless visual
checks require a working font setup and actual screenshot inspection.

```powershell
$env:QT_QPA_PLATFORM = "windows"
uv run --project application/rdt_monitor --group dev python -m pytest -c application/rdt_monitor/pyproject.toml --confcutdir=application/rdt_monitor/tests application/rdt_monitor/tests -q
Remove-Item Env:QT_QPA_PLATFORM
uv run --project application/rdt_monitor --group dev ruff check application/rdt_monitor
uv run --project application/rdt_monitor --group dev ruff format --check application/rdt_monitor
```

## Initial USB Acquisition Check

Windows enumerated an NR-CM01 camera with status OK. The existing standalone
[camera wrapper](../../tools/keyence_nr_cm01_api.py) read generic OpenCV index 0
using DirectShow at 640x480. A 30-frame host-read sample had a mean interval of
about 0.0339 seconds and a maximum of about 0.047 seconds. The reported FPS
property was 0 and must not be treated as a valid frame-rate measurement.

This is an initial acquisition smoke check, not sustained-throughput, disconnect
recovery, sensor-timestamp, or inspection qualification. Generic OpenCV indices
do not establish manufacturer/model identity. The GUI connection path also
streamed index 0 through DirectShow at 640x480, rendered preview pixels, and
released the device on disconnect. The retained image was correctly marked
**NOT LIVE**. Reported FPS remained unavailable. Connecting USB does not enable
production monitoring.

Next steps: qualify marker/position
recognition, test sustained acquisition and recovery, then integrate qualified
inference and durable evidence.
Production start must remain disabled until authentication, recipe qualification,
health/evidence guarantees, and target-PC acceptance gates are implemented.