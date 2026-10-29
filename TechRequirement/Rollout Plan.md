# RDT Reliability Monitor Rollout Plan

Recorded: 2026-10-19.

Status: agreed phasing decision; implementation and qualification work remain.
This plan records deployment direction, not production approval or a delivery-date commitment.
The [architecture](Architecture.md) and [design direction](design%20direction.md)
remain the baseline. The [operator guide](../application/rdt_monitor/README.md)
describes currently implemented behavior.

## 1. Decision And Operating Context

The target is a Windows industrial PC (IPC) that also controls a reliability
bending robot. Position-sensor feedback already exists in the robot control system.
The exact controller interface, signal semantics, and camera trigger capability
have not yet been confirmed.

- **Phase 1:** deploy the existing vision-marker approach first, minimizing new
  integration work and enabling a controlled on-station pilot.
- **Phase 2:** integrate existing IPC/controller feedback as the primary cycle
  and inspection-position source after Phase 1 is established.
- Do not make IPC integration a prerequisite for the Phase 1 pilot.
- Do not interpret the quickest deployment path as permission to bypass qualification.
  The current application remains an unqualified development inspection system.
- Both phases are advisory monitoring only. The monitor must not command robot
  motion, replace safety interlocks, or interrupt the robot's control loop.

## 2. Scope At A Glance

| Area | Phase 1: vision-marker pilot | Phase 2: controller-feedback integration |
| --- | --- | --- |
| Cycle source | Existing Start/Position marker disappearance and return | Existing controller cycle counter/events, or validated position sequence |
| Inspection position | Stable visual return in the configured setup | Controller position/capture-ready feedback correlated with a camera frame |
| Counter meaning | Visually observed cycles, not guaranteed machine totals | Controller-reported cycles, separately reconciled with inspected cycles |
| Camera | Existing acquisition path, qualified within a measured operating envelope | Hardware triggering if supported; otherwise validated fresh-frame selection |
| Vision role | Cycle synchronization and inspection setup verification | Defect inspection and, where qualified, specimen pose/slip verification |
| IPC control interface | No integration required | Read-only adapter using the installed controller's supported interface |
| Model reuse | Explicit compatibility checks and independent specimen validation | Same model/recipe rules; controller feedback does not establish model compatibility |

## 3. Phase 1: Fast, Controlled Vision-Marker Deployment

### 3.1 Deliverables

1. Install and exercise the existing desktop application on the target IPC.
   Measure contention with robot-control software; keep acquisition, inference,
   and evidence work from blocking control or UI operation.
2. Establish repeatable camera, lighting, fixture, and mark placement. Configure
   the two marker regions and the independent inspection ROI at the intended
   inspection pose.
3. Collect and review genuine normal images, train/export the model, and save a
   model-bound inspection recipe with its matching detector bundle.
4. Run a supervised pilot with explicit specimen/run identification, evidence
   storage, backup procedure, and operator fault/recovery instructions.
5. Record the tested operating envelope and unresolved limitations before
   considering unattended or production use.

### 3.2 Cycle And Inspection Rules

- Establish initial stable visibility before counting. Starting with a visible
  mark, or seeing an initially absent mark for the first time, is not a cycle.
- Each mark must become stably absent and return; their stable joint return
  completes one visually observed cycle. Holding the marks visible adds no cycles.
- Ambiguous recognition is not disappearance, return, or proof of valid position.
- Submit the exact accepted cycle-return frame for inspection, not an unrelated
  preview frame. Preserve run, cycle, frame, recipe, and model identity.
- Camera gaps, marker timeouts, processing overflow, invalid model output, and
  evidence failures must remain visible. They must not become a normal verdict.
- Separate observed cycles, successfully inspected cycles, and uninspected cycles.
  Do not infer machine cycles during an observation gap.

### 3.3 Remounting And Model Reuse

Implementation update, 2026-10-19: the desktop now provides Basic/Advanced setup
tabs, named family recipe metadata, specimen-specific marker adjustments, frozen
run setup records. As of 2026-10-21, recipe editing and saving no longer require
Windows-account authorization. See the [operator guide](../application/rdt_monitor/README.md#simple-recipe-and-specimen-setup).
This is not completed station qualification or Phase 2 controller integration.

- Operators select recipes by purpose-based names and identify the current specimen.
  Basic handles marker adjustment and setup confirmation; Advanced handles reusable
  recipe revisions and technical settings without a credential prompt.
- Family/model identity and specimen setup are logically separate within the existing
  JSON workflow. Routine remounting records a run setup, not a new serial-number recipe.
  New reusable revisions preserve previous files and reuse their existing detector bundle.
- A model trained with A-001 may be a candidate for another compatible A-type
  specimen, but identifier similarity alone is not evidence of compatibility.
- Validate on independent normal and defective specimens. Repeated images of
  A-001 do not establish coverage of between-specimen variation.
- Treat B-001 as a separate compatibility decision. A different geometry,
  material, finish, or inspection surface normally requires its own validated
  model/recipe unless a shared model has been demonstrated to work.
- At every remount, verify specimen identity, camera geometry, lighting, marker
  locations, and the inspected surface within the saved ROI.
- Marker recognition is not geometric registration of the inspection crop.
  The current monitor does not automatically align a displaced specimen.
- Restore the validated setup when mounting differs. If a setup change is needed,
  create and validate a new recipe rather than silently changing a model-bound one.
  Changing only marker settings does not automatically require model retraining,
  but the model's input surface and appearance must remain compatible.
- Loading a recipe preserves the current specimen ID. Verify the actual specimen
  identity; never run A-002 under A-001's identity merely to reuse settings.
- No automatic learning from incoming specimens and no automatic model replacement.

### 3.4 Pilot Acceptance And Release Gates

Before the pilot, define and record numeric limits and test durations appropriate
to the actual robot. Values are not prescribed here because cycle speed, dwell
time, defect requirements, and target-IPC performance are not yet measured.

| Check | Required evidence |
| --- | --- |
| Cycle correctness | Compare observed counts with an independent reference over recorded test runs; include startup, pauses, staggered marker transitions, and repeated visible holds |
| Recognition envelope | Test expected motion, lighting, remounting, and occlusion; record supported bounds and fault behavior outside them |
| Frame coverage | Measure capture rate, synchronization latency, gaps, and overflow at the fastest intended cycle; show transitions are not silently lost |
| Inspection validity | Independent specimen-level normal/defect evaluation with agreed false-alarm and detection criteria |
| Traceability | Verify specimen/run identity, recipe/model identity, source frame, verdict, and evidence association |
| Failure handling | Exercise camera loss, marker ambiguity, queue overload, storage failure, stop, and application restart; verify explicit unavailable/uninspected states |
| IPC coexistence | Measure sustained resource use and robot-control timing with monitoring active; obtain acceptance from the control-system owner |
| Endurance and storage | Run an agreed-duration station trial; measure evidence growth, available space, and operator recovery/backup procedures |

A supervised development pilot is not approval for unattended 24/7 operation.
Durable run recovery, retention, verified hardware binding, authorization, and
other open architecture gates remain required for the intended production scope.
Do not claim those capabilities from the existing local evidence files alone.

## 4. Phase 2: Read-Only IPC/Controller Feedback

### 4.1 Interface Discovery

With the control-system owner, identify:

- Controller/runtime and supported read interface: for example ADS, OPC UA,
  Modbus TCP, a local IPC channel, or a vendor SDK. No protocol is selected yet.
- Whether the controller already owns an authoritative completed-cycle counter.
- Meanings, polarity, debounce, update rate, and timing of position and cycle signals.
- Session/reset behavior, counter rollover, event latching/acknowledgment, timestamps,
  heartbeat, and communication-fault reporting.
- Camera support for hardware triggering, frame identifiers, and exposure timestamps.
- Read permissions, connection limits, and acceptable CPU/network load without
  changing robot motion or safety behavior.

Prefer an existing supported control interface rather than duplicating sensor wiring.
Any required acknowledgment must acknowledge data delivery only, not actuate the robot.

### 4.2 Event Contract And Adapter

Introduce a typed, source-neutral cycle event and a bounded, non-blocking adapter.
Keep controller-specific details outside model inference and UI decision logic.
Reuse the existing run lifecycle, inference, and evidence mechanisms where their
contracts fit; do not disguise controller events as visual marker observations.

Prefer controller-provided run/session identity, completed-cycle number,
inspection-position/capture-ready state, running/paused/fault status, and heartbeat.
Retain source timestamps or sequence numbers where available, together with host
receipt times; do not assume different device clocks are synchronized.

- Prefer a counter or latched event over polling a short pulse that may be missed.
- If only endpoint signals exist, agree and validate a full sequence such as
  home -> bend endpoint -> home stable. A home bit alone is not a completed cycle.
- Detect duplicate, out-of-order, missed, reset, and rollover events explicitly.
  Preserve controller cycle IDs separately from the monitor's local sequence.
- A counter jump can reveal missed coverage but cannot recreate missing images.
  Record affected cycles as uninspected or a coverage gap, not inspected.
- On disconnect/reconnect, establish a verified session baseline and record gaps.
  Do not silently reset counts or resume against an unknown controller session.

### 4.3 Frame Correlation

```text
Existing robot control and sensors
                |
Read-only controller adapter
                |
Cycle identity + inspection-position event
                |
Camera acquisition / validated frame correlation
                |
Optional qualified pose check -> inspection ROI -> model
                |
Verdict + evidence + coverage accounting
```

Use hardware-triggered acquisition when the existing controller and camera support
it and the timing is qualified. Otherwise, validate a free-running-camera method
against robot dwell time, settling, exposure, buffering, frame age, and deadlines.
The latest preview frame is not automatically the correct trigger frame.

No suitable frame by the deadline means not inspected. Controller-reported cycles,
admitted inspections, completed inspections, and missing coverage must be distinct.
Controller position feedback does not prove that a specimen has not slipped in
its fixture; retain visual checks where they address that risk.

### 4.4 Migration And Acceptance

1. Build deterministic adapter tests and recorded/simulated signal replay before
   connecting to live robot feedback.
2. Add explicit recipe/config selection for synchronization source. Preserve the
   existing vision mode; do not silently switch sources during a run.
3. Run controller feedback in shadow mode alongside vision. Compare event identity,
   counts, and timing without double-counting or submitting duplicate inspections.
4. Validate pulses/counters, duplicates, gaps, faults, resets, reconnects, camera
   delays, backpressure, stop/drain, and evidence correlation on the actual IPC.
5. Qualify controller-driven operation against agreed numeric timing/count criteria.
   Switch the primary source only after acceptance by station and control owners.
6. Keep rollback to a separately validated vision configuration explicit and
   operator-confirmed. Do not silently fall back after controller communication loss.

## 5. Work Sequence And Open Decisions

| Order | Work package | Completion condition |
| --- | --- | --- |
| 1 | Phase 1 station setup and pilot preparation | Camera/fixture/recipe baseline and numeric acceptance criteria recorded |
| 2 | Phase 1 supervised validation | Results and limitations reviewed; deployment scope explicitly accepted |
| 3 | Phase 1 production-readiness work, if required | Relevant architecture gates closed before claiming production or 24/7 readiness |
| 4 | Phase 2 controller/camera interface discovery | Signal contract and timing approach agreed with control owner |
| 5 | Phase 2 implementation and shadow validation | Adapter, frame correlation, fault handling, and comparison tests pass |
| 6 | Phase 2 controlled cutover | Qualification accepted; source selection and rollback documented |

No delivery dates or hardware purchases are committed by this plan.
Open inputs include controller type/protocol, camera trigger capability, cycle rate
and inspection dwell, allowed mounting variation, defect acceptance criteria,
retention/endurance requirements, and responsibility for station qualification.

## 6. Boundaries

- No IPC/controller integration is implemented by this planning document.
- Automatic pose registration, approved recipe qualification, and multi-specimen
  training workflows are not implied to exist; assess them separately when needed.
- No automatic robot stop, motion command, or safety function is included in either phase.
- Implementation status remains in the operator guide; future work must update that
  guide and relevant tests rather than treating this plan as proof of implementation.
