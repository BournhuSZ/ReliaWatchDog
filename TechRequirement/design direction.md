# RDT Reliability Monitor Design Direction

Status: agreed PyQt6 + Modern Industrial Bauhaus design baseline, 2026-10-14.
This is a UI specification, not a completed implementation. Operational contracts
and qualification gates are defined in [Architecture.md](Architecture.md).

## 1. Philosophy And Operating Context

- Function first: precise geometry, clear hierarchy, high contrast, restrained color.
- A laboratory instrument/CNC HMI, not a decorative dashboard.
- Offline Windows desktop, one camera and station, keyboard/mouse first.
- Advisory monitoring only. Start/Stop controls monitoring, never the bending machine.
- Preserve native window controls and predictable desktop behavior. Touchscreen
  interaction requires a separate sizing/validation pass.
- Use resizable, unframed work areas with restrained dividers. The live view is
  dominant; avoid nested cards, heavy shadows, and competing status lights.

## 2. Dark Palette

| Role | Hex | Usage |
| --- | --- | --- |
| Background Primary | `#0F1115` | Main window |
| Background Secondary | `#171A21` | Supporting work areas |
| Background Tertiary | `#1E222B` | Inputs and tables |
| Border / Divider | `#2A2F3A` | Subtle boundaries |
| Text Primary | `#E8EAED` | Main text |
| Text Secondary | `#9AA0A6` | Supporting labels |
| Accent Blue | `#3B82F6` | Active commands and focus |
| Success / Normal | `#22C55E` | Valid normal assessment |
| Warning | `#EAB308` | Warning and degraded operation |
| Danger / Anomaly | `#EF4444` | Anomaly and critical alerts |
| Heatmap High | `#F97316` to `#EF4444` | Anomaly intensity |

Color is supplementary: always pair state with text and an identifiable symbol.
Unknown, stopped, and unavailable must not look like normal. Validate actual
text/background and focus contrast; do not assume palette selection proves accessibility.

## 3. Typography

- Primary: Inter or IBM Plex Sans.
- Technical values/timestamps: JetBrains Mono or IBM Plex Mono.
- Window/section titles: 18-20 px, medium.
- Compact group titles: 15-16 px, medium.
- Body: 13-14 px, regular; supporting labels: 12 px.
- Key counters: 24-32 px, semibold, used sparingly.
- Stable numeric columns and reserved counter space prevent layout shifts.
- Validate target-PC font availability/distribution rights and Windows DPI scaling.

## 4. Main Window

```text
Native title bar: RDT Reliability Monitor              Min / Max / Close
Run: specimen ID | recipe version | qualification | observed cycles | Setup
----------------------------------------------------------------------
                                      | Monitoring health
                                      | Camera / detector / storage
Live view                             | Synchronization and coverage
Source timestamp + freshness          |-------------------------------
Marker region + inspection ROI        | Specimen assessment
Source-aligned heatmap when valid     | Current result / source cycle
                                      | Latched incident / acknowledge
                                      |-------------------------------
                                      | Start monitoring / Stop monitoring
                                      | Run details / evidence status
----------------------------------------------------------------------
Persistent fault area: cause, impact, recovery action
----------------------------------------------------------------------
Event log: Time | Run/cycle | Condition | Evidence | Acknowledgment | Actions
```

Use resizable panes rather than fixed pixel proportions. Keep faults and primary
controls visible when the event log grows. Support sorting/filtering and paged or
incremental event loading without blocking capture. Verify the eventual minimum
window size and target display/scaling; they are qualification inputs, not assumptions.

## 5. Independent State Displays

| Display | Required behavior |
| --- | --- |
| Monitoring health | Distinguish stopped, warming up, ready, monitoring, degraded, and fault; show unavailable components |
| Synchronization | Waiting for fresh marker transition, tracking cycle, inspection position valid, or lost/ambiguous |
| Specimen | One current assessment: unknown, normal, warning, or anomaly; identify source cycle and age |
| Alarm | Distinguish latched alarm, acknowledgment, observed recovery, and closure |
| Evidence | Pending, complete, incomplete/failed, and protected; incomplete is never styled as successfully saved |

Do not display NORMAL/WARNING/ANOMALY as three competing active lights. A historical
normal result may remain in history but must not imply current health after camera,
model, synchronization, or deadline failure. Clearly label stale/last-valid data.

Critical faults and evidence-save failures remain visible until resolved; do not
rely on transient notifications. Show what failed, what coverage/evidence is affected,
and the available recovery action. Acknowledging an alarm does not clear a fault,
assert specimen recovery, close the incident, or restart inspection.

## 6. Live View And Visible Marker

- Preserve image aspect ratio and unambiguous mapping between source pixels and ROI.
- Distinguish the cycle-start marker region from the specimen inspection ROI using
  labeled outlines, not color alone.
- Show recognition for both Start and Position marks without implying every frame
  is an inspection. Both move with the bending specimen: both must visibly
  disappear and return to complete one cycle. Holding them visible never adds cycles.
- Establish both marks visible before observing a complete disappearance/return.
  Startup midway through a bend does not count that partial cycle. Uncertain
  transition frames wait without counting; unresolved uncertainty faults.
  Inspection requires the returned qualified visual position, not a fixed delay.
- Label the counter **visually observed cycles** and make coverage gaps discoverable.
- Display only heatmaps aligned to their exact source frame/ROI. If inference trails
  live capture, use a clearly labeled inspection snapshot rather than an old overlay
  on the current frame.
- Show disconnected/frozen/stale video explicitly. Never leave an unlabeled frozen
  image looking live. Preserve access to source and annotated evidence separately.

## 7. Controls, Roles, And Run Workflow

Routine operators identify the specimen, select an eligible qualified recipe, and
start/stop monitoring. Show the active run and locked recipe version throughout.
Enable Start only when qualification and required health checks pass. Critical
faults or restart require an explicit operator-confirmed restart after checks;
preview reconnect does not resume inspection.

Setup requires an authenticated supervisor and service-enforced authorization.
Camera/lighting settings, marker region, ROI, model, thresholds, and timing rules
are setup controls, not live operator adjustments. Remove the unrestricted main-window
sensitivity slider. End the run before changing its recipe; changes require validation
and invalidate the previous setup's qualification. Record who changed what and when.

Use familiar icons with labels for consequential commands and tooltips/accessibility
names for icon-only actions. Use toggles for overlay visibility, numeric controls for
precise setup values, and sliders only where meaningful. Display the calibrated
value and units; do not present an unexplained sensitivity percentage.

Keep training capture, review/confirmation, and training as distinct steps. In the
development collection window, use native Collection, Review, and Training tabs.
Show saved/target and reviewed/total counts, preserve original/ROI image access,
and require explicit undamaged confirmation before enabling Train. Collection
completion must never start fitting automatically. Show incomplete collection,
training progress/cancellation, and unqualified export status without changing
the main window's current specimen assessment or active model.

## 8. Event Review And Evidence

- Group consecutive abnormal observations into one incident rather than rows per frame.
- Show source timestamp, run/specimen/cycle, assessment, thumbnail, evidence state,
  acknowledgment, and review actions in a dense readable table.
- Review original image, annotated image, and pre/post clip with recipe/model, ROI,
  thresholds, scores, timestamps, and coverage metadata.
- Keep pending post-event capture visibly pending. Flag missing footage or failed
  writes and expose the reason; never imply incomplete evidence is complete.
- Separate Acknowledge from incident closure and from evidence protection controls.
  Apply authorized service rules and audit consequential actions.
- Surface quota/storage faults persistently. Do not offer silent overwrite of
  protected evidence; retention removes only expired, unprotected completed incidents.

## 9. Components And Motion

- Global `styles.qss`: palette, typography, controls, focus, table and disabled states.
- Live view: initially a QWidget/QLabel plus painting unless measured requirements
  justify OpenGL. Preserve correct source-to-display transforms.
- Status components: compact, stable dimensions, text plus symbols; subtle emphasis
  may reinforce an active state but never replace its meaning.
- Buttons: flat, 6-8 px corner radius, clear hover/pressed/focus/disabled states.
- Event table: Qt model/view with delegates for thumbnails and condition/evidence
  indicators; load images and query history off the UI thread.
- Status transitions: 200-300 ms; panel transitions: about 250 ms ease-out; heatmaps
  may fade in. Fault meaning and Start eligibility update immediately, not after animation.
- No bounce, decorative animation, flashing alerts, or button scaling that moves
  neighboring content. Allow reduced motion and maintain visible keyboard focus.
- Keep version one in Qt Widgets; QML is not a requirement.

## 10. UI Acceptance

Validate keyboard operation, text/symbol state distinctions, target display/DPI,
resizable layout, long identifiers, growing counters, and readable dense tables.
Exercise stopped/unqualified startup, marker already present, marker loss, delayed
inference, stale video, model failure, disk-full/partial evidence, reconnect,
acknowledgment without recovery, authorization failure, and restart confirmation.

The UI must remain responsive during sustained capture, inspection, recording, and
event review. Target resolution, minimum window size, timing limits, storage capacity,
and recipe acceptance values remain explicit open qualification inputs in the
architecture document. No production readiness is implied by a visual mockup.
