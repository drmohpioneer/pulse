# Shockable Adult Cardiac Arrest Capability

Version: 1.0
Status: Design Only

---

# Source References

This design is based on the 2025 American Heart Association Adult Advanced Life Support and Adult Basic Life Support guideline material.

Primary references:

- AHA 2025 Part 9: Adult Advanced Life Support
- AHA 2025 Part 7: Adult Basic Life Support
- AHA 2025 Part 11: Post-Cardiac Arrest Care

This document is not production code.

It does not change existing architecture.

Frozen architecture dependencies:

- `ClinicalEvent`
- `ClinicalWorkflowEngine`
- `EventProcessor`
- `RoutingTable`
- `MachineRegistry`
- `RhythmStateMachine`

---

# 1. Clinical Objective

This capability supports deterministic workflow tracking and recommendation generation for adult cardiac arrest when the rhythm is shockable:

- ventricular fibrillation (VF)
- pulseless ventricular tachycardia (pVT)

The capability should help Pulse answer:

> Given the accepted clinical events so far, what is the current shockable arrest phase and what is the next deterministic ACLS-aligned action?

It supports:

- recognition of cardiac arrest events
- CPR state tracking
- rhythm assessment branch selection
- shock delivery tracking
- immediate post-shock CPR resumption
- epinephrine timing after initial defibrillation attempts fail
- antiarrhythmic timing for VF/pVT unresponsive to defibrillation
- repeated CPR/rhythm/shock cycles
- transition to ROSC/post-cardiac arrest care when ROSC is accepted

It must remain deterministic.

It must not use AI.

It must not infer events that are not present as accepted `ClinicalEvent`s.

---

# 2. Scope

## Supported

- Adult cardiac arrest workflow when rhythm is VF or pVT.
- Accepted event replay through deterministic state machines.
- Rhythm pathway classification from accepted rhythm events.
- CPR started, paused, and resumed state tracking.
- Shock count and last shock tracking.
- Medication administration tracking for epinephrine and amiodarone.
- ROSC tracking from accepted ROSC events.
- Deterministic recommendations owned by exactly one state machine.
- Correction-aware replay where superseded events do not affect final state.
- Timeline-driven reconstruction after late corrections.

## Not Supported

- Pediatric cardiac arrest.
- Neonatal resuscitation.
- Nonshockable arrest capability as a complete workflow.
- Tachyarrhythmia with a pulse.
- Bradycardia with a pulse.
- Defibrillator energy selection.
- Device-specific shock waveform selection.
- Advanced airway decision logic.
- H's and T's diagnostic workflow.
- Termination of resuscitation.
- Prognostication.
- Medication contraindication handling.
- Dose calculation beyond recognizing accepted medication events.
- Real-time speech/audio event extraction.
- AI-generated clinical advice.
- UI implementation.
- Persistence/database behavior.
- Authentication/authorization.

---

# 3. Clinical Sequence

## 3.1 Recognition of Arrest

Expected accepted events:

- `CPR_STARTED`
- optionally an initial `RHYTHM_CHECKED`

Clinical meaning:

- A cardiac arrest workflow is active when CPR has started or a pulseless arrest rhythm has been accepted.
- The shockable capability becomes clinically relevant once rhythm state is VF or pVT.

State impact:

- `CPRStateMachine` records CPR running.
- `RhythmStateMachine` remains unknown until an accepted rhythm event arrives.
- Other machines ignore `CPR_STARTED` unless they explicitly own that event.

Recommendation intent:

- If rhythm is not known, recommend rhythm assessment through the machine that owns rhythm-assessment timing.

Open ambiguity:

- Whether `CPR_STARTED` alone should activate a global arrest context is outside the current frozen architecture because there is no global workflow state.

## 3.2 CPR

Expected events:

- `CPR_STARTED`
- `CPR_RESUMED`
- `CPR_PAUSED`

Clinical meaning:

- CPR should run continuously except for rhythm assessment and shock delivery.
- Interruptions should be minimized.

State impact:

- `CPRStateMachine` owns whether CPR is running, paused, or unknown.
- `CPRStateMachine` owns CPR cycle timing and the next rhythm-assessment window.

Recommendation intent:

- Resume CPR after shock.
- Continue CPR during active CPR intervals.
- Assess rhythm when the CPR cycle reaches the rhythm-check point.

## 3.3 Rhythm Assessment

Expected event:

- `RHYTHM_CHECKED`

Payload examples:

```json
{"rhythm": "vf"}
```

```json
{"rhythm": "pulseless_vt"}
```

Clinical meaning:

- VF and pVT are shockable arrest rhythms.
- The adult cardiac arrest algorithm branches based on whether the rhythm is shockable.

State impact:

- `RhythmStateMachine` owns rhythm classification.
- It updates current rhythm and pathway only from accepted/corrected rhythm events.
- It does not apply shocks, medication, CPR state, or ROSC effects.

Recommendation intent:

- `RhythmStateMachine` may identify the current pathway.
- The recommendation to deliver shock is owned by `ShockStateMachine`, not `RhythmStateMachine`, for this full capability.

Important implementation note:

- The existing `RhythmStateMachine` currently produces a minimal rhythm-level "Deliver shock." recommendation for the demo vertical slice.
- For this complete shockable arrest capability, recommendation ownership must not be duplicated. The implementation plan must either:
  - treat the existing rhythm recommendation as demo-level only, or
  - route final user-facing "Deliver shock" ownership to `ShockStateMachine` and avoid duplicate display aggregation.

This is a design ambiguity to resolve before implementation.

## 3.4 Shock

Expected event:

- `SHOCK_DELIVERED`

Clinical meaning:

- Defibrillation is the key shockable-rhythm intervention.
- CPR should resume immediately after shock.

State impact:

- `ShockStateMachine` increments shock count.
- `ShockStateMachine` records last shock event and timestamp.
- `CPRStateMachine` does not change state because `ShockStateMachine` tells it to.
- `CPRStateMachine` only changes if a separate accepted `CPR_RESUMED` event arrives.

Recommendation intent:

- Deliver shock when current rhythm is shockable and shock is due.
- Resume CPR after shock is delivered.

## 3.5 Resume CPR

Expected event:

- `CPR_RESUMED`

Clinical meaning:

- After shock, CPR resumes immediately for the next cycle.

State impact:

- `CPRStateMachine` records CPR running and starts/updates the active CPR cycle.
- `ShockStateMachine` ignores `CPR_RESUMED`.

Recommendation intent:

- Once shock is delivered, `CPRStateMachine` owns "Resume CPR" if CPR is not already resumed.
- During CPR, `CPRStateMachine` owns "Continue CPR" and the next rhythm-assessment timing recommendation.

## 3.6 Medication Timing

Expected events:

- `EPINEPHRINE_GIVEN` represented initially as `MEDICATION_GIVEN` with payload identifying epinephrine.
- `AMIODARONE_GIVEN` represented initially as `MEDICATION_GIVEN` with payload identifying amiodarone.

Clinical meaning:

- Epinephrine is recommended for adult cardiac arrest.
- In shockable rhythms, epinephrine timing is after initial defibrillation attempts have failed.
- Operationally, epinephrine every 3 to 5 minutes is guideline-supported.
- Amiodarone or lidocaine may be considered for VF/pVT unresponsive to defibrillation.

State impact:

- `MedicationStateMachine` owns medication history.
- `MedicationStateMachine` owns last epinephrine time and last antiarrhythmic time.
- `MedicationStateMachine` must know enough context from events to decide whether shockable-arrest medication recommendations are due.

Recommendation intent:

- Give epinephrine when indicated by shockable pathway timing.
- Consider amiodarone for VF/pVT unresponsive to defibrillation.

Open ambiguity:

- The exact number of failed shocks before first epinephrine and first amiodarone should be encoded from the accepted algorithm diagram before implementation.
- Current `ClinicalEvent.EventType` has only `MEDICATION_GIVEN`, not medication-specific enum values. Medication-specific behavior must be payload-based unless the event model is changed in a future approved design.

## 3.7 Repeated Cycles

Expected repeating event pattern:

1. CPR running.
2. CPR paused for rhythm check.
3. `RHYTHM_CHECKED` indicates VF/pVT.
4. `SHOCK_DELIVERED`.
5. `CPR_RESUMED`.
6. Medication events occur according to timing/indication.
7. Repeat rhythm assessment after CPR cycle.

State impact:

- Each machine updates only from events it owns.
- Recommendations emerge from each machine's own state and event history.
- The engine orchestrates event routing only.

No machine directly calls another machine.

## 3.8 ROSC

Expected event:

- `ROSC_ACHIEVED`

Clinical meaning:

- Accepted ROSC transitions the workflow out of active cardiac arrest and toward post-cardiac arrest care.

State impact:

- `ROSCStateMachine` records ROSC achieved.
- `RhythmStateMachine` may classify current rhythm category as ROSC from `ROSC_ACHIEVED`.
- `CPRStateMachine` should stop recommending CPR only from accepted events it owns or from explicit ROSC ownership rules.

Recommendation intent:

- Transition to post-cardiac arrest care.

Open ambiguity:

- Whether `ROSCStateMachine` alone owns "Transition to post-cardiac arrest care" or whether `RhythmStateMachine` may retain its existing ROSC recommendation must be resolved before implementation to avoid duplicate recommendations.

---

# 4. State Machines Involved

## RhythmStateMachine

### Responsibilities

- Consume accepted/corrected `RHYTHM_CHECKED` events.
- Consume accepted/corrected `ROSC_ACHIEVED` events if maintaining current rhythm category.
- Classify rhythm as:
  - VF
  - pVT
  - shockable unknown
  - PEA
  - asystole
  - nonshockable unknown
  - organized
  - ROSC
  - unknown
- Classify pathway as:
  - shockable
  - nonshockable
  - organized
  - ROSC
  - unknown
- Provide deterministic explanation for rhythm classification.

### Owned State

- current rhythm
- current rhythm category/pathway
- last rhythm-check event ID
- last rhythm-check timestamp
- rhythm confidence
- applied rhythm event IDs
- last transition reason

### Ignored Responsibilities

- shock count
- shock timing
- medication timing
- CPR running/paused state
- airway status
- H's and T's
- defibrillator energy
- user-facing aggregation of all recommendations
- AI reasoning

## CPRStateMachine

### Responsibilities

- Consume accepted/corrected CPR events:
  - `CPR_STARTED`
  - `CPR_PAUSED`
  - `CPR_RESUMED`
- Track whether CPR is running or paused.
- Track active CPR cycle timing.
- Own recommendations related to CPR continuity and rhythm-assessment timing.
- Own "Resume CPR" after a shock only if represented by its own state/event rules.

### Owned State

- CPR status
- current cycle start timestamp
- last pause timestamp
- last resume timestamp
- last CPR-related event ID
- active cycle number if needed
- CPR interruption status if designed later

### Ignored Responsibilities

- rhythm classification
- shock count
- medication timing
- ROSC confirmation
- AI reasoning
- audio/speech input

## ShockStateMachine

### Responsibilities

- Consume accepted/corrected `SHOCK_DELIVERED` events.
- Track shock count.
- Track last shock timestamp and event ID.
- Own recommendations related to shock delivery for shockable rhythm context.
- Detect possible duplicate shocks during replay according to an accepted duplicate policy.

### Owned State

- total accepted shock count
- last shock event ID
- last shock timestamp
- shock history event IDs
- maybe pending shock recommendation state

### Ignored Responsibilities

- CPR status changes
- rhythm classification updates
- medication administration
- ROSC state
- energy selection unless separately designed
- AI reasoning

## MedicationStateMachine

### Responsibilities

- Consume accepted/corrected `MEDICATION_GIVEN` events.
- Identify medication from payload.
- Track epinephrine administrations.
- Track amiodarone administrations.
- Own medication recommendations:
  - epinephrine when indicated
  - amiodarone/lidocaine consideration for VF/pVT unresponsive to defibrillation
- Track medication timing windows from event timestamps.

### Owned State

- medication administrations
- last epinephrine timestamp/event ID
- last amiodarone timestamp/event ID
- medication-specific counts
- pending or due medication recommendation state

### Ignored Responsibilities

- rhythm classification
- shock count changes
- CPR status changes
- ROSC confirmation
- airway state
- AI reasoning

## ROSCStateMachine

### Responsibilities

- Consume accepted/corrected `ROSC_ACHIEVED` events.
- Track whether ROSC has occurred.
- Own transition out of active arrest if assigned as recommendation owner.
- Explain ROSC state from accepted events.

### Owned State

- ROSC status
- ROSC event ID
- ROSC timestamp
- confidence
- supersession-aware ROSC history

### Ignored Responsibilities

- rhythm classification before ROSC
- shock count
- medication timing
- CPR cycle timing unless explicitly encoded by ROSC event policy
- AI reasoning

---

# 5. Event Ownership

| Clinical Event | Responsible Machine | Notes |
|---|---|---|
| `CPR_STARTED` | `CPRStateMachine` | Starts CPR state/cycle. Other machines ignore. |
| `CPR_PAUSED` | `CPRStateMachine` | Records CPR pause, usually for rhythm analysis or shock. |
| `CPR_RESUMED` | `CPRStateMachine` | Records CPR running and active cycle restart/resume. |
| `RHYTHM_CHECKED` | `RhythmStateMachine` | Classifies rhythm/pathway from payload. |
| `SHOCK_DELIVERED` | `ShockStateMachine` | Records defibrillation event and increments accepted shock count. |
| `EPINEPHRINE_GIVEN` | `MedicationStateMachine` | Represented as `MEDICATION_GIVEN` payload until medication-specific event types are approved. |
| `AMIODARONE_GIVEN` | `MedicationStateMachine` | Represented as `MEDICATION_GIVEN` payload until medication-specific event types are approved. |
| `ROSC` | `ROSCStateMachine` | In current event model this is `ROSC_ACHIEVED`. |

Routing note:

- An event may be routed to multiple machines for state awareness only if each machine independently owns a state update for that event.
- Recommendation ownership still remains exactly one owner per recommendation.

---

# 6. Recommendation Ownership

Every recommendation must have exactly one owner.

| Recommendation | Owner | Reason |
|---|---|---|
| Assess rhythm | `CPRStateMachine` | Rhythm checks occur at CPR cycle checkpoints. |
| Deliver shock | `ShockStateMachine` | Shock delivery depends on shockable rhythm context and shock state. |
| Resume CPR | `CPRStateMachine` | CPR state owns running/paused/resumed state. |
| Continue CPR | `CPRStateMachine` | CPR continuity and cycle timing are CPR state concerns. |
| Give epinephrine | `MedicationStateMachine` | Medication timing/history belongs to medication state. |
| Consider amiodarone | `MedicationStateMachine` | Antiarrhythmic recommendation depends on medication and refractory VF/pVT history. |
| Transition to post-cardiac arrest care | `ROSCStateMachine` | ROSC state owns exit from active arrest. |
| Confirm rhythm | `RhythmStateMachine` | Rhythm uncertainty belongs to rhythm classification. |

Important conflict to resolve:

- The current `RhythmStateMachine` demo implementation returns "Deliver shock" for shockable rhythm.
- For the complete shockable cardiac arrest capability, final user-facing recommendation aggregation must avoid duplicate ownership.
- Before implementation, choose one:
  - keep `RhythmStateMachine` recommendation as internal/demo only and do not aggregate it in the shockable capability, or
  - move user-facing "Deliver shock" ownership into `ShockStateMachine` and adjust `RhythmStateMachine` only if an approved clinical requirement permits API/behavior change.

Because the RhythmStateMachine API is frozen, this document does not propose changing it.

---

# 7. Interaction Rules

Machines influence one another only through accepted clinical events and orchestration.

## Allowed

- `EventProcessor` dispatches the same accepted event to every routed machine.
- Each machine independently updates its own state.
- Each machine may derive recommendations from its own state and the event data it has consumed.
- Replay provides all accepted events in deterministic order.
- Corrections create new events that supersede previous events.

## Not Allowed

- `ShockStateMachine` must not call `CPRStateMachine`.
- `CPRStateMachine` must not call `RhythmStateMachine`.
- `MedicationStateMachine` must not query `ShockStateMachine` directly.
- `ROSCStateMachine` must not mutate CPR, rhythm, medication, or shock state.
- No state machine may call AI, UI, database, audio, or network services.
- No state machine may create clinical events.

## Context Sharing

If a machine needs context from another clinical domain, it must receive that context through events, not direct machine access.

Examples:

- `MedicationStateMachine` may need to know shock count before recommending epinephrine or amiodarone. It must learn this from routed `SHOCK_DELIVERED` events only if that routing is explicitly approved.
- `ShockStateMachine` may need to know the latest rhythm is shockable. It must learn this from routed `RHYTHM_CHECKED` events only if that routing is explicitly approved.
- `CPRStateMachine` may need to know a shock was delivered to recommend resuming CPR. It must learn this from a routed `SHOCK_DELIVERED` event or from an explicit `CPR_RESUMED` event, depending on accepted ownership policy.

Open design question:

- The event ownership table assigns each clinical event a primary responsible machine. Some recommendations require cross-domain context. Before implementation, we must decide which machines may consume non-owned events as read-only context while preserving single recommendation ownership.

---

# 8. Replay Expectations

Replay reconstructs state from a complete ordered list of `ClinicalEvent`s.

Expected replay behavior:

1. Machine resets to initial state.
2. Machine identifies superseded event IDs from correction events.
3. Machine skips superseded events.
4. Machine processes remaining accepted/corrected events in deterministic order.
5. Machine ignores unrelated events.
6. Machine produces the same state and recommendations for the same final event history.

Replay must be:

- deterministic
- side-effect free except for machine state reconstruction
- independent of wall-clock time
- independent of current UI state
- independent of AI/model output

Replay is the only trusted way to reconstruct state after corrections.

---

# 9. Corrections

Events are never deleted.

Corrections create a new `ClinicalEvent` with:

- `status = corrected`
- `supersedes_event_id` pointing to the prior event
- correction history linking the prior event

During replay:

- superseded events do not affect final state
- correction events may affect final state if they are relevant to the machine
- unchanged unrelated events still apply

Examples:

- A `RHYTHM_CHECKED` event says VF.
- A corrected `RHYTHM_CHECKED` event supersedes it and says asystole.
- Replay ignores the original VF event.
- `RhythmStateMachine` final state becomes asystole/nonshockable.
- Any downstream shockable recommendations based on the superseded VF must disappear after replay.

Open ambiguity:

- If a shock was delivered based on a rhythm event later corrected to nonshockable, the shock event itself still occurred and remains in history unless separately superseded. Clinical interpretation of that sequence belongs to audit/review behavior, not deletion.

---

# 10. Invariants

- State machines are deterministic.
- Same final event list plus same order produces identical state.
- Replay produces identical recommendations for identical event history.
- No machine modifies another machine.
- No machine calls another machine.
- No machine calls AI.
- No machine calls UI.
- No machine accesses database/network/audio.
- Events are immutable after creation.
- Events are never deleted.
- Superseded events never affect final state.
- Correction events reference previous events.
- Recommendation ownership is unique.
- Recommendations always include a reason/rationale.
- Machines ignore unrelated events.
- Machines ignore non-accepted/non-corrected events.
- Shock count never decreases during live processing.
- Shock count may be lower after replay if a prior shock event is superseded by correction.
- Medication administration count never decreases during live processing.
- Medication administration count may be lower after replay if a prior medication event is superseded.
- ROSC state is entered only by accepted/corrected ROSC event.
- User-facing recommendations must not duplicate the same action from two machines.

---

# 11. Edge Cases

## Repeated VF

Scenario:

- VF is detected repeatedly across rhythm checks.

Expected behavior:

- `RhythmStateMachine` remains shockable/VF.
- `ShockStateMachine` determines whether another shock is due based on shock and rhythm-check events.
- Recommendations remain deterministic.

## Shock Recorded Twice

Scenario:

- Two `SHOCK_DELIVERED` events are recorded for one actual shock.

Expected behavior:

- Without correction, `ShockStateMachine` treats both accepted events as real.
- With correction, the duplicate event is superseded and ignored during replay.

Open ambiguity:

- Automatic duplicate detection threshold is not defined. Do not implement without separate design.

## Shock Before CPR

Scenario:

- `SHOCK_DELIVERED` arrives before `CPR_STARTED`.

Expected behavior:

- `ShockStateMachine` records the shock if accepted.
- `CPRStateMachine` remains unknown/not started unless it owns and consumes shock context.
- System should not invent CPR started.

Open ambiguity:

- Whether to surface an audit warning belongs to a future validation/audit capability.

## Medication Before Rhythm

Scenario:

- `MEDICATION_GIVEN` epinephrine occurs before any rhythm check.

Expected behavior:

- `MedicationStateMachine` records the medication.
- It should not infer shockable or nonshockable pathway.
- Recommendations that require rhythm context should be withheld or marked as requiring rhythm assessment.

## ROSC Then Recurrent VF

Scenario:

- `ROSC_ACHIEVED` occurs.
- Later `RHYTHM_CHECKED` indicates VF/pVT.

Expected behavior:

- Machines process events in order.
- `ROSCStateMachine` records prior ROSC.
- `RhythmStateMachine` may update current rhythm to VF if a later rhythm event is accepted.
- A recurrent-arrest capability may be required for full clinical behavior.

Open ambiguity:

- Whether recurrent VF after ROSC starts a new arrest episode is not defined in current architecture. Do not implement episode partitioning without separate design.

## Duplicate Rhythm Checks

Scenario:

- Multiple `RHYTHM_CHECKED` events occur close together with same rhythm.

Expected behavior:

- `RhythmStateMachine` applies each accepted event in order.
- Final rhythm state equals the latest non-superseded rhythm event.

Open ambiguity:

- Automatic collapsing of duplicate rhythm checks is not defined.

## Late Event Arrival

Scenario:

- An event arrives late with an earlier timestamp.

Expected behavior:

- Live processing applies in arrival order unless the event store/replay layer supplies a corrected chronological history.
- Replay should use the canonical accepted event order defined by the event store.

Open ambiguity:

- Canonical ordering policy for same timestamp or late-arriving events is not yet defined.

## Rhythm Corrected After Shock

Scenario:

- VF event leads to shock.
- VF event is corrected to PEA.

Expected behavior:

- Rhythm final state becomes PEA after replay.
- Shock event remains if not superseded.
- Shock count remains unless shock event itself is corrected.
- Audit should show that shock occurred under a later-corrected rhythm context.

## ROSC Event Superseded

Scenario:

- ROSC is accepted, then corrected as false.

Expected behavior:

- `ROSCStateMachine` replay ignores superseded ROSC.
- Active arrest recommendations may resume based on remaining event history.

---

# 12. Acceptance Tests

This capability is complete only when the following scenarios pass as deterministic tests.

## Scenario 1: Initial VF Shock Cycle

Events:

1. `CPR_STARTED`
2. `RHYTHM_CHECKED {"rhythm": "vf"}`

Expected:

- Rhythm state is VF/shockable.
- Shock recommendation owner emits "Deliver shock."
- No medication recommendation is emitted before initial defibrillation attempt policy is satisfied.

## Scenario 2: Shock Delivered Then Resume CPR

Events:

1. `CPR_STARTED`
2. `RHYTHM_CHECKED {"rhythm": "vf"}`
3. `SHOCK_DELIVERED`

Expected:

- Shock count is 1.
- Shock history contains the shock event ID.
- CPR recommendation owner emits "Resume CPR" if CPR is not running.
- Shock machine does not mutate CPR state.

## Scenario 3: CPR Resumed After Shock

Events:

1. `CPR_STARTED`
2. `RHYTHM_CHECKED {"rhythm": "vf"}`
3. `SHOCK_DELIVERED`
4. `CPR_RESUMED`

Expected:

- CPR state is running.
- Shock count remains 1.
- Rhythm remains VF/shockable until next rhythm event.
- CPR owner controls next rhythm-assessment timing.

## Scenario 4: Repeated Shockable Rhythm

Events:

1. `CPR_STARTED`
2. `RHYTHM_CHECKED {"rhythm": "vf"}`
3. `SHOCK_DELIVERED`
4. `CPR_RESUMED`
5. `CPR_PAUSED`
6. `RHYTHM_CHECKED {"rhythm": "pulseless_vt"}`

Expected:

- Rhythm state is pVT/shockable.
- Shock count remains 1 until next shock event.
- Shock owner emits another shock recommendation if policy says shock is due.

## Scenario 5: Epinephrine Timing in Shockable Pathway

Events:

1. `CPR_STARTED`
2. `RHYTHM_CHECKED {"rhythm": "vf"}`
3. `SHOCK_DELIVERED`
4. `CPR_RESUMED`
5. repeated shockable cycle events according to accepted timing policy

Expected:

- Medication owner emits epinephrine only after initial defibrillation attempts have failed.
- Epinephrine recommendation includes rationale and due/overdue state if designed.

Open required detail:

- Exact failed-defibrillation threshold must be specified from accepted algorithm diagram before implementation.

## Scenario 6: Epinephrine Given

Events:

1. shockable cycle context
2. `MEDICATION_GIVEN {"medication": "epinephrine"}`

Expected:

- Medication state records epinephrine event ID and timestamp.
- Next epinephrine recommendation is suppressed until the 3 to 5 minute interval policy is due.

## Scenario 7: Amiodarone for Refractory VF/pVT

Events:

1. repeated VF/pVT with shock events sufficient to meet refractory policy

Expected:

- Medication owner emits amiodarone/lidocaine consideration only when VF/pVT is unresponsive to defibrillation under accepted policy.

Open required detail:

- Exact refractory threshold and whether to represent lidocaine as alternative must be specified before implementation.

## Scenario 8: ROSC Transition

Events:

1. active shockable arrest history
2. `ROSC_ACHIEVED`

Expected:

- ROSC state is achieved.
- ROSC owner emits transition to post-cardiac arrest care if assigned ownership.
- Active shock/CPR/medication recommendations are suppressed by aggregation policy without direct machine mutation.

Open required detail:

- Recommendation aggregation/suppression policy is not yet implemented and must be designed separately.

## Scenario 9: Correct VF to Asystole

Events:

1. `CPR_STARTED`
2. `RHYTHM_CHECKED {"rhythm": "vf"}` original
3. corrected `RHYTHM_CHECKED {"rhythm": "asystole"}` supersedes original

Expected after replay:

- Rhythm state is asystole/nonshockable.
- Original VF event ID is not in applied rhythm event IDs.
- Shockable recommendations are absent from final state.

## Scenario 10: Duplicate Shock Corrected

Events:

1. VF rhythm
2. shock event A
3. shock event B duplicate
4. correction supersedes shock event B

Expected after replay:

- Shock count is 1.
- Shock history contains shock event A only.
- Superseded shock event B does not affect final state.

## Scenario 11: Shock Before CPR

Events:

1. `SHOCK_DELIVERED`
2. `CPR_STARTED`

Expected:

- Shock count is 1.
- CPR state changes only after `CPR_STARTED`.
- No machine invents missing prior CPR.

## Scenario 12: Late Event Replay

Events:

1. Live arrival order differs from clinical timestamp order.
2. Event store provides canonical replay order.

Expected:

- Replay from canonical order produces deterministic final state.
- Same canonical order always produces same recommendations.

Open required detail:

- Canonical ordering rules must be defined before implementation.

---

# Implementation Readiness Checklist

Before implementation begins:

- Confirm recommendation ownership conflicts with existing `RhythmStateMachine` demo recommendation.
- Confirm medication payload schema for epinephrine/amiodarone.
- Confirm exact failed-shock thresholds for epinephrine and antiarrhythmic recommendations from the accepted 2025 algorithm diagram.
- Confirm whether machines may consume non-owned events as context.
- Confirm canonical event ordering policy for replay.
- Confirm how user-facing recommendation aggregation/suppression will work without duplicating recommendation ownership.

