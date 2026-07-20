# Workflow Coordinator Design

Version: 1.0
Status: Design Only

---

# Purpose

The Workflow Coordinator is the deterministic presentation-selection layer between independent state machines and the user-facing dashboard.

It consumes immutable state snapshots and deterministic recommendations from state machines.

It does not mutate machine state.

It does not call state-machine `apply_event()`.

It does not create clinical events.

It does not call AI.

It does not replace `ClinicalWorkflowEngine`.

It answers:

- What is the current overall workflow phase?
- What is the highest-priority action?
- What secondary actions should be visible?
- What rationale should be presented to the user?
- Which otherwise-valid recommendations should be suppressed because a more important state makes them inappropriate?

This design exists so remaining state-machine implementations can stay simple, deterministic, and local.

---

# Architectural Position

Current event flow:

```text
ClinicalEvent
  -> ClinicalWorkflowEngine
  -> EventProcessor
  -> State Machines
  -> Machine States + Machine Recommendations
```

Coordinator flow:

```text
Machine States + Machine Recommendations
  -> Workflow Coordinator
  -> Workflow Presentation Decision
  -> Dashboard
```

The coordinator is not a state machine.

It is a deterministic reducer over already-computed state and recommendations.

---

# Non-Goals

The Workflow Coordinator does not:

- listen to audio
- perform evidence fusion
- call AI
- call external services
- write to a database
- mutate machine state
- own CPR/rhythm/shock/medication/ROSC state
- create or correct events
- infer missing clinical events
- replace state-machine recommendations
- implement hidden ACLS state transitions
- decide whether an event happened

---

# Inputs

The coordinator receives a single immutable input object.

Conceptual shape:

```text
WorkflowCoordinatorInput
  machine_states
  machine_recommendations
  active_confirmation_requests
  accepted_event_timeline
  replay_metadata
```

## machine_states

Read-only snapshot keyed by machine name.

Expected initial keys:

- `rhythm`
- `cpr`
- `shocks`
- `medications`
- `rosc`
- later: `airway`, `reversible_causes`, `timers`

Examples:

- `rhythm.current_category`
- `cpr.status`
- `shocks.shock_count`
- `medications.last_epinephrine_at`
- `rosc.status`

The coordinator must tolerate missing machine states during incremental implementation.

Missing state should degrade to `unknown`, not crash the system, unless strict validation mode is enabled in tests.

## machine_recommendations

Read-only recommendations keyed by owner machine.

Each recommendation must include:

- stable ID
- priority
- message
- rationale
- referenced state fields
- owner machine
- optional confirmation flag

Current `Recommendation` does not include `owner_machine`.

Design requirement:

- The coordinator input wrapper must associate each recommendation with its owner machine without changing the frozen `Recommendation` model.

## active_confirmation_requests

Read-only confirmation items from evidence/event validation.

The coordinator may elevate confirmation requests when they block safe workflow progression.

It must not resolve them.

## accepted_event_timeline

Read-only list of accepted/corrected clinical events or timeline entries.

Used for rationale and phase determination only.

The coordinator must not mutate the timeline.

## replay_metadata

Optional metadata:

- canonical event order ID
- replay run ID
- replay generated timestamp
- whether replay included corrections
- whether any machines failed replay

The coordinator uses this only for traceability.

---

# Outputs

The coordinator emits a single immutable decision object.

Conceptual shape:

```text
WorkflowPresentationDecision
  phase
  primary_action
  secondary_actions
  suppressed_actions
  rationale
  visible_state_summary
  safety_flags
  source_recommendation_ids
  source_state_fields
```

## phase

The current overall workflow phase.

Initial phase set:

- `unknown`
- `arrest_recognized`
- `awaiting_rhythm_assessment`
- `shockable_arrest`
- `post_shock_cpr`
- `nonshockable_arrest`
- `rosc`
- `post_cardiac_arrest_care`
- `conflict_or_confirmation_needed`

## primary_action

The single highest-priority user-facing action.

There must be at most one primary action.

If no safe action is available, primary action should be:

- `Confirm clinical state`
- or `Assess rhythm`
- depending on available state.

## secondary_actions

Other actions that are relevant but lower priority.

Examples:

- prepare medication
- consider reversible causes
- continue CPR timer awareness
- confirm low-confidence rhythm

Secondary actions must not visually compete with the primary action.

## suppressed_actions

Recommendations intentionally hidden or demoted.

Each suppressed action must include:

- source recommendation ID
- owner machine
- suppression reason

Suppression must be explainable and deterministic.

## rationale

Clinician-facing explanation for the selected primary action.

The rationale is assembled from:

- selected recommendation rationale
- relevant machine state fields
- suppression rules
- accepted event timeline references

No LLM text generation.

## visible_state_summary

Small read model for the dashboard:

- rhythm
- pathway
- CPR status
- shock count
- last medication summary
- ROSC status
- confirmation status

The coordinator does not compute underlying state. It only selects and formats already-owned state for presentation.

## safety_flags

Deterministic flags such as:

- confirmation required
- conflicting state
- replay incomplete
- stale rhythm
- ROSC suppressing active-arrest actions

These flags are not AI warnings.

---

# Phase Determination Rules

Phase is derived from machine states in strict order.

The first matching rule wins.

## 1. conflict_or_confirmation_needed

Use when:

- active confirmation blocks a high-impact event
- required machine state is missing for a critical decision
- replay metadata indicates a machine failed replay
- mutually incompatible states are present

Examples:

- rhythm says ROSC but ROSC machine says no ROSC
- shockable rhythm recommendation exists but rhythm confidence requires confirmation

## 2. rosc

Use when:

- ROSC state is achieved
- or rhythm state category is ROSC and ROSC state is not available

ROSC phase suppresses active-arrest recommendations unless recurrent arrest is explicitly represented by later accepted events and a future episode policy.

## 3. post_cardiac_arrest_care

Use when:

- ROSC has been achieved and post-ROSC care recommendation is primary

This may initially collapse with `rosc` until post-cardiac-arrest state is designed.

## 4. shockable_arrest

Use when:

- rhythm category is shockable
- ROSC is not achieved
- shock has not yet been delivered for the current rhythm-check cycle

## 5. post_shock_cpr

Use when:

- latest relevant shock has been delivered
- ROSC is not achieved
- CPR is not confirmed running after that shock

## 6. nonshockable_arrest

Use when:

- rhythm category is nonshockable
- ROSC is not achieved

This phase is recognized by the coordinator, but full nonshockable workflow remains out of scope for the shockable capability.

## 7. awaiting_rhythm_assessment

Use when:

- CPR/arrest is active
- rhythm is unknown
- ROSC is not achieved

## 8. arrest_recognized

Use when:

- CPR has started
- but rhythm status is unavailable

This may collapse into `awaiting_rhythm_assessment` for UI purposes.

## 9. unknown

Use when:

- no enough accepted state exists to determine arrest phase

---

# Recommendation Ownership Rules

The coordinator must respect unique recommendation ownership.

It may select, suppress, or order recommendations.

It must not duplicate recommendation responsibility.

Initial ownership rules:

| Recommendation | Owner |
|---|---|
| Assess rhythm | `CPRStateMachine` |
| Deliver shock | `ShockStateMachine` |
| Resume CPR | `CPRStateMachine` |
| Continue CPR | `CPRStateMachine` |
| Give epinephrine | `MedicationStateMachine` |
| Consider amiodarone | `MedicationStateMachine` |
| Transition to post-cardiac arrest care | `ROSCStateMachine` |
| Confirm rhythm | `RhythmStateMachine` |

Important current conflict:

- `RhythmStateMachine` currently emits "Deliver shock." for the demo.
- The coordinator must avoid duplicate user-facing display once `ShockStateMachine` owns "Deliver shock."
- Until `ShockStateMachine` exists, demo-level rhythm recommendation may be shown.
- Once `ShockStateMachine` exists, the coordinator should suppress `rhythm.shockable.deliver_shock` with reason:
  - `superseded_by_owner_machine: shocks`

This avoids changing the frozen `RhythmStateMachine` API.

---

# Prioritization Rules

The coordinator orders candidate recommendations deterministically.

Ordering keys:

1. suppression eligibility
2. phase-specific priority bucket
3. recommendation priority enum
4. owner machine priority order
5. recommendation ID lexical order

## Priority Enum Order

From highest to lowest:

1. `critical`
2. `high`
3. `medium`
4. `low`

## Owner Machine Tie-Break Order

Initial deterministic tie-break order:

1. `rosc`
2. `cpr`
3. `shocks`
4. `rhythm`
5. `medications`
6. `airway`
7. `reversible_causes`
8. `timers`

Rationale:

- ROSC can suppress active arrest.
- CPR continuity is broadly time-critical.
- Shock is decisive in shockable arrest but should not override confirmed ROSC.
- Rhythm determines pathway but should not duplicate action ownership.
- Medication timing matters but usually does not interrupt shock/CPR actions.

## Phase-Specific Primary Action Rules

### conflict_or_confirmation_needed

Primary action:

- highest-impact confirmation or "Confirm clinical state"

Suppress:

- any action requiring the uncertain state unless safe to display as secondary.

### rosc / post_cardiac_arrest_care

Primary action:

- transition to post-cardiac arrest care

Suppress:

- deliver shock
- continue CPR
- resume CPR
- give epinephrine
- consider amiodarone

Exception:

- If recurrent arrest episode policy is later designed and later accepted events establish recurrent arrest, active-arrest actions may return.

### shockable_arrest

Primary action:

- deliver shock if shock due

Secondary actions may include:

- prepare to resume CPR
- medication timing if due but not interrupting shock

Suppress:

- nonshockable pathway recommendations
- post-ROSC recommendations

### post_shock_cpr

Primary action:

- resume CPR

Secondary actions may include:

- prepare next rhythm assessment
- medication if due and operationally compatible

Suppress:

- repeat shock until next accepted rhythm check or explicit shock-due state.

### awaiting_rhythm_assessment

Primary action:

- assess rhythm

Secondary actions may include:

- continue CPR until rhythm analysis moment

Suppress:

- deliver shock unless rhythm is shockable.

### nonshockable_arrest

Primary action:

- continue CPR or give epinephrine depending on the owning-machine recommendations and timing state

Suppress:

- deliver shock
- amiodarone for refractory VF/pVT

---

# Suppression Rules

Suppression is deterministic and explainable.

Suppression does not delete recommendations.

Suppressed recommendations remain available for audit/debug output.

## ROSC Suppression

If ROSC is confirmed:

Suppress:

- deliver shock
- resume CPR
- continue CPR
- assess rhythm for active arrest
- give epinephrine during active arrest
- consider amiodarone

Rationale:

- Confirmed ROSC changes the workflow out of active cardiac arrest.

## Rhythm Unknown Suppression

If rhythm is unknown:

Suppress:

- deliver shock
- amiodarone for refractory VF/pVT

Do not suppress:

- assess rhythm
- continue CPR if CPR is active

## Nonshockable Suppression

If rhythm category is nonshockable:

Suppress:

- deliver shock
- amiodarone for VF/pVT

Do not suppress:

- CPR actions
- epinephrine if medication owner says due

## Shock Already Delivered Suppression

If shock was delivered for the current rhythm-check cycle:

Suppress:

- repeat shock recommendation

Until:

- next rhythm check confirms shockable rhythm
- or a separate accepted clinical event indicates another shock is due according to future design

Open ambiguity:

- "Current rhythm-check cycle" requires CPR/shock/rhythm machines to expose enough immutable state to identify cycle boundaries.

## CPR Running Suppression

If CPR is already confirmed running:

Suppress:

- resume CPR

Do not suppress:

- continue CPR
- prepare rhythm assessment if due

## CPR Paused Suppression

If CPR is paused and rhythm analysis/shock is not the primary action:

Elevate:

- resume CPR

Rationale:

- Avoid unnecessary interruption of compressions.

## Duplicate Ownership Suppression

If two recommendations have same user-facing action but different owners:

Keep:

- recommendation from the documented owner

Suppress:

- duplicate recommendation from non-owner

Suppression reason:

- `duplicate_action_wrong_owner`

## Confirmation Blocking Suppression

If a recommendation depends on an unconfirmed high-impact event:

Suppress or demote it until confirmation.

Example:

- possible VF with low confidence should not produce primary "Deliver shock" without confirmation policy.

## Stale State Suppression

If a recommendation depends on stale rhythm or timer state:

Suppress or mark as requires confirmation.

Open ambiguity:

- Staleness thresholds are not yet defined.

---

# Secondary Action Rules

Secondary actions should be:

- clinically relevant
- lower urgency than primary action
- non-conflicting with primary action
- limited in number

Initial cap:

- maximum 3 secondary actions

Ordering:

1. critical non-conflicting recommendations
2. high-priority non-conflicting recommendations
3. confirmation requests
4. medium/low recommendations if space remains

Do not show secondary actions that contradict the primary action.

Examples:

- Primary: Deliver shock.
- Secondary: Prepare to resume CPR.
- Do not show: Continue CPR if shock action requires pause/clear workflow.

---

# Rationale Rules

The coordinator rationale must be deterministic and source-linked.

It should include:

- selected primary recommendation rationale
- relevant phase reason
- key state fields
- suppression explanation when important

Example:

```text
Current rhythm is VF/shockable from accepted rhythm event <id>.
Shock is the highest-priority action for shockable arrest.
CPR and medication actions remain secondary until shock delivery is recorded.
```

Rationale must not:

- include LLM-generated uncertainty
- infer hidden events
- claim certainty if source state is low confidence
- include broad teaching text during active arrest

---

# Replay Behavior

The coordinator does not replay events itself.

Replay happens in each state machine.

Coordinator replay behavior:

1. Event store provides canonical event history.
2. Each machine replays the same canonical history or its routed subset.
3. Each machine emits replayed state and deterministic recommendations.
4. Coordinator receives replayed machine outputs.
5. Coordinator recomputes phase, primary action, secondary actions, suppression, and rationale from scratch.

Coordinator output must be:

- deterministic
- stateless
- reproducible
- independent of previous coordinator output

Same replayed machine outputs must produce identical coordinator decision.

Corrections are handled before coordinator input by state-machine replay.

The coordinator may include replay metadata in rationale/debug output but must not reinterpret superseded events directly.

---

# Handling Corrections

The coordinator consumes final replayed states, not raw correction semantics.

Expected behavior:

- Superseded events do not affect machine state.
- Coordinator sees only the resulting state/recommendations.
- Suppressed or selected recommendations update automatically after replay.

Example:

1. Original rhythm: VF.
2. Shock recommendation exists.
3. Rhythm corrected to asystole.
4. `RhythmStateMachine` replay outputs nonshockable.
5. `ShockStateMachine` no longer owns deliver-shock recommendation if its state/recommendation design depends on current shockable context.
6. Coordinator suppresses any remaining shockable duplicate as inconsistent with nonshockable phase.

Coordinator must not mutate events or machines to achieve this.

---

# Coordinator Invariants

- Coordinator is deterministic.
- Coordinator is stateless between evaluations.
- Coordinator never mutates machine state.
- Coordinator never calls `apply_event()`.
- Coordinator never creates `ClinicalEvent`s.
- Coordinator never calls AI.
- Coordinator never directly calls another external service.
- Coordinator does not replace `ClinicalWorkflowEngine`.
- Coordinator output is derived only from input states, recommendations, confirmations, timeline, and metadata.
- At most one primary action exists.
- Suppression is explicit and explainable.
- Recommendations from wrong owner are suppressed if ownership conflict exists.
- Confirmed ROSC suppresses active-arrest actions unless recurrent arrest policy is explicitly designed.
- Unknown rhythm suppresses shock-specific actions.
- Nonshockable rhythm suppresses shock-specific actions.
- Repeated evaluation over same input produces identical output.

---

# Required Input Contracts Before Implementation

Before implementation, each state machine must expose enough state for the coordinator to avoid hidden clinical assumptions.

## RhythmStateMachine

Required fields:

- current rhythm
- current category
- confidence
- last rhythm event ID
- last rhythm timestamp

Already mostly available.

## CPRStateMachine

Required fields:

- CPR status
- current cycle start
- last pause event ID/timestamp
- last resume event ID/timestamp
- rhythm-assessment due flag or due timestamp

## ShockStateMachine

Required fields:

- shock count
- last shock event ID/timestamp
- whether shock is currently due
- rhythm-check cycle ID or equivalent cycle marker

## MedicationStateMachine

Required fields:

- epinephrine administrations
- amiodarone administrations
- medication due flags
- last medication timestamps
- medication recommendation rationale

## ROSCStateMachine

Required fields:

- ROSC status
- ROSC event ID/timestamp
- confidence

---

# Acceptance Tests

The coordinator is complete only when these deterministic tests pass.

## 1. Unknown State

Input:

- no machine states or all unknown states

Expected:

- phase: `unknown`
- primary action: confirm or assess clinical state
- no shock recommendation displayed

## 2. CPR Started, Rhythm Unknown

Input:

- CPR running
- rhythm unknown
- ROSC not achieved

Expected:

- phase: `awaiting_rhythm_assessment`
- primary action: assess rhythm
- shock-specific actions suppressed

## 3. VF, Shock Due

Input:

- rhythm VF/shockable
- shock owner emits deliver-shock
- CPR running or paused for analysis
- ROSC not achieved

Expected:

- phase: `shockable_arrest`
- primary action: deliver shock
- rationale references rhythm and shock recommendation

## 4. VF, Shock Delivered, CPR Not Running

Input:

- rhythm shockable
- shock count increased
- CPR not running/paused
- CPR owner emits resume CPR

Expected:

- phase: `post_shock_cpr`
- primary action: resume CPR
- repeat shock suppressed

## 5. VF, Shock Delivered, CPR Running

Input:

- rhythm shockable
- shock delivered
- CPR running

Expected:

- phase: `post_shock_cpr` or active CPR cycle phase
- primary action: continue CPR or next due action according to CPR owner
- resume CPR suppressed

## 6. Epinephrine Due But Shock Due

Input:

- shockable rhythm
- shock owner emits deliver-shock critical
- medication owner emits epinephrine high

Expected:

- primary action: deliver shock
- epinephrine appears as secondary if not conflicting

## 7. Confirmed ROSC

Input:

- ROSC achieved
- active-arrest recommendations still present from other machines

Expected:

- phase: `rosc` or `post_cardiac_arrest_care`
- primary action: transition to post-cardiac arrest care
- shock/CPR/epinephrine/amiodarone suppressed

## 8. Nonshockable Rhythm

Input:

- rhythm asystole/nonshockable
- shock owner mistakenly or stale emits deliver-shock

Expected:

- phase: `nonshockable_arrest`
- deliver shock suppressed
- nonshockable owner actions selected according to priority

## 9. Duplicate Deliver-Shock Recommendations

Input:

- rhythm recommendation says deliver shock
- shock recommendation says deliver shock

Expected:

- keep shock-owned recommendation
- suppress rhythm-owned duplicate
- suppression reason: duplicate action wrong owner

## 10. Replay Determinism

Input:

- same replayed machine states and recommendations passed twice

Expected:

- identical coordinator output

## 11. Corrected VF to Asystole

Input:

- replayed rhythm state is asystole/nonshockable after correction
- stale shockable recommendation appears in input

Expected:

- phase: nonshockable
- shockable action suppressed
- rationale references current nonshockable state

## 12. Missing Machine State

Input:

- rhythm state exists
- CPR state missing

Expected:

- coordinator degrades gracefully
- phase uses known rhythm if safe
- safety flag notes missing CPR state

---

# Open Design Questions

1. Should the coordinator own a formal "episode" concept, or should recurrent arrest after ROSC wait for a separate episode manager?
2. How exactly should machine recommendations be wrapped with owner identity without changing the frozen `Recommendation` model?
3. Should state-machine outputs include stable "action kind" identifiers to improve duplicate suppression beyond matching message text?
4. What is the canonical event ordering policy for late events and equal timestamps?
5. Should confirmation requests be prioritized before all clinical actions or only when they block high-impact decisions?
6. How should demo-only `RhythmStateMachine` recommendations be handled once full `ShockStateMachine` ownership exists?
7. Should the coordinator live under `backend/workflow/` or `backend/services/` when implemented?

---

# Implementation Guidance

When implementation begins:

- Build the coordinator as a pure deterministic function or small class.
- Accept input snapshots; do not access machines directly.
- Do not call `ClinicalWorkflowEngine.process()`.
- Do not call `apply_event()`.
- Do not aggregate by asking machines dynamically during evaluation unless recommendations are already part of the input.
- Use explicit rule tables for phase, priority, and suppression.
- Unit test every rule.
- Add replay determinism tests.
- Add duplicate recommendation ownership tests.

