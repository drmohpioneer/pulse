# ClinicalEvent Design Note

Version: 1.0
Status: Review (no code changes proposed here)

This note reviews the current `ClinicalEvent` implementation in
`backend/workflow/events.py` and records design reasoning for future work.

It does not change any model. It exists so that later implementation decisions
are made against a written record instead of memory.

---

## 1. Current Implementation

The event model lives in `backend/workflow/events.py` and is built from four
Pydantic models plus three enums.

### Immutability base

All event-related models inherit from `FrozenModel`:

```
model_config = ConfigDict(frozen=True, validate_default=True)
```

Once an instance is created it cannot be mutated. The tests in
`backend/tests/workflow/test_events.py` confirm this: assigning to a field
raises `ValidationError`, and mutating a nested payload raises `TypeError`.

Nested payloads are deep-frozen by `_freeze_value`, which converts:

- `Mapping` to `MappingProxyType` (read-only)
- `list` / `tuple` to `tuple`
- `set` / `frozenset` to `frozenset`

A matching `_serialize_value` reverses this so `model_dump()` returns plain JSON
friendly structures. This means an event is immutable in memory but still
cleanly serializable.

### Enums

- `EventType`: the closed vocabulary of clinical events (cpr_started,
  cpr_paused, cpr_resumed, rhythm_checked, shock_delivered, medication_given,
  airway_secured, reversible_cause_considered, rosc_achieved, plus `unknown`
  and `todo`).
- `EventSource`: where the signal came from (speech, acoustic, manual,
  simulated, device_future, system).
- `EventStatus`: lifecycle of an event (candidate, accepted, needs_confirmation,
  rejected, corrected, revoked).

### Evidence

`Evidence` is a single observation from one source. Key fields: `id` (UUID),
`source`, `evidence_type` (free string), `timestamp` (must be timezone aware),
`confidence` (0.0 to 1.0), `payload` (frozen mapping), `raw_reference`,
`uncertainty_reason`.

Evidence is the raw, uncertain input. It is not a clinical fact on its own.

### ClinicalEvent

`ClinicalEvent` is a fused, clinically meaningful event. Key fields: `id`
(UUID), `timestamp` (clinical time, timezone aware), `event_type`, `source`,
`confidence` (0.0 to 1.0), `status`, `evidence` (tuple, minimum length 1),
`supersedes_event_id`, `correction_history` (tuple of `CorrectionRecord`),
`payload`, `created_at` (record creation time).

Two invariants are enforced today:

- Every event must carry at least one `Evidence` item (`min_length=1`). An
  event with no evidence cannot exist.
- If `status` is `corrected`, `supersedes_event_id` must be set (a model
  validator raises otherwise).

A convenience property `supersedes_another_event` returns whether the event
replaces a previous one.

### CorrectionRecord

`CorrectionRecord` captures the audit detail of a correction: `corrected_at`,
`corrected_by`, `reason`, `previous_status`, and the required
`superseded_event_id`.

### Summary of the current shape

```
Evidence (uncertain observation)
        |
        v   (fusion, not yet implemented)
ClinicalEvent (fused fact + confidence + status + evidence[])
        |
        v   (correction creates a NEW event, never mutates)
ClinicalEvent (status = corrected, supersedes previous)
```

---

## 2. EventType Scalability Concern

`EventType` is a closed `StrEnum`. Every clinically meaningful event must be a
member of that enum. This is good for the current demo (small, safe, easy to
validate) but has three scaling limits worth naming before we build on it.

### Concern A: every new event type is a code change

Adding a new clinical event (for example `pacing_started`, `access_obtained`,
`etco2_reading`, `defib_charging`) requires editing the enum and redeploying.
The vocabulary is bound to the codebase, not to data or configuration. In a
real product with device integrations, this list grows fast.

### Concern B: the enum is flat, with no category or hierarchy

There is no grouping. `shock_delivered` and `medication_given` sit at the same
level with no shared parent such as "intervention" versus "assessment" versus
"rhythm". Downstream state machines currently infer meaning from the flat name.
As the list grows, code that switches on event type will grow with it.

### Concern C: event_type and payload are not linked

`payload` is an untyped frozen mapping. Nothing ties the shape of the payload to
the event type. A `medication_given` event and a `rhythm_checked` event can
carry any payload at all. This is flexible now but means there is no schema
guarantee that a medication event actually names a drug.

### Placeholder members leak into data

`EventType.UNKNOWN` and `EventType.TODO` exist as scaffolding. `TODO` in
particular is a build-time placeholder that should never appear in real event
data. This is fine during skeleton work but should be resolved before any real
ingestion.

### Directions to consider later (not decided here)

- Keep the enum for the accepted core vocabulary, but allow a namespaced string
  form (for example `domain.action`) for extension types coming from devices.
- Introduce a category field derived from the type so state machines route on
  category, not on every individual name.
- Pair each event type with a typed payload model (a discriminated union) so the
  payload is validated per type.

None of these should be adopted until the state machine transition tables are
implemented, because the consumers of `event_type` define what structure is
actually needed.

---

## 3. Evidence Confidence versus Event Confidence

Both `Evidence` and `ClinicalEvent` carry a `confidence` float between 0.0 and
1.0, but they mean different things.

### Evidence confidence

`Evidence.confidence` answers: how sure is this single source about its own
observation?

Examples:

- Speech recognition is 0.78 sure it heard "shock delivered".
- An acoustic classifier is 0.82 sure it heard a defibrillator discharge.
- A manual confirmation is 1.0, because a human asserted it.

Evidence confidence is local to one source and one observation.

### Event confidence

`ClinicalEvent.confidence` answers: how sure are we that this clinical event
actually happened, given all the evidence combined?

This is a fused judgment. Two agreeing sources at 0.78 and 0.82 should produce
an event more confident than either alone. One conflicting source should lower
it. This is exactly the job of the Evidence Fusion Engine described in
`docs/architecture.md`.

### Important gap in the current code

Right now `ClinicalEvent.confidence` is simply a field that is passed in at
construction. Nothing computes it from the attached evidence. The fusion policy
(`backend/services/evidence_fusion.py`) is still a `Protocol` stub with a TODO
and does not run yet.

Consequences to keep in mind:

- Today it is possible to build a `ClinicalEvent` with confidence 0.95 whose
  only evidence item has confidence 0.30. The model will not object. The two
  numbers are independent until fusion enforces a relationship.
- The mapping from evidence confidence to event confidence is a deliberate
  policy decision (how agreement raises confidence, how conflict lowers it,
  which event types demand confirmation regardless). That policy belongs in the
  fusion layer, not in the model.

For this reason the model correctly stays neutral: it stores both numbers and
lets a later deterministic layer own the relationship. This note records that
the relationship is currently unenforced so it is not mistaken for a guarantee.

---

## 4. Correction and Superseding Philosophy

The core rule is: events are immutable, and history is append only.

You never edit a past event. If an event was wrong, you add a new event that
supersedes it. The old event stays in the log.

### How a correction works today

1. The original event exists, for example `status = accepted`.
2. A `CorrectionRecord` is created capturing who corrected it, when, why, and the
   previous status, plus `superseded_event_id` pointing at the original.
3. A new `ClinicalEvent` is created with `status = corrected`,
   `supersedes_event_id` set to the original id, and the `CorrectionRecord` in
   its `correction_history`.

The model enforces that a `corrected` event must reference what it supersedes,
so a correction can never be untraceable. `EventStatus.REVOKED` exists for the
case where an event should be withdrawn rather than replaced.

### Why this shape

- Auditability: the full clinical record is preserved. Every state the system
  ever believed can be reconstructed and explained. For medical software this
  is a safety and trust requirement, not a nicety.
- Deterministic replay: this ties directly to the state machine contract in
  `docs/state_machine_contract.md`. State is a derived function of the accepted
  event history. To apply a correction, you correct the event history and
  replay, rather than reaching into current state and patching it. Same events
  in the same order always produce the same state.
- Immutability makes this safe. Because events are frozen, a corrected event
  and its superseded original cannot silently diverge from what was recorded.

### The mental model

The event log is the source of truth. Clinical state is a projection of it.
Corrections rewrite the projection by extending the log, never by rewriting the
past.

---

## 5. Future Fields

Two fields are anticipated but deliberately not added yet. They are recorded
here so their intended meaning is fixed before implementation.

### detected_at

The model already has two time fields:

- `timestamp`: when the event clinically happened.
- `created_at`: when this record object was constructed.

`detected_at` would capture a third, distinct moment: when the system first
perceived the evidence for this event, before it was fused and committed.

Why it matters:

- Perception is not instant. Speech recognition and acoustic classification add
  latency. `detected_at` lets us measure the gap between when something happened
  clinically (`timestamp`) and when Pulse noticed it (`detected_at`).
- It supports honest ordering when recognition is delayed or arrives out of
  order, without corrupting the clinical timeline.

Design caution: `detected_at`, `timestamp`, and `created_at` must be defined
clearly against each other so they are not conflated. Clinical time, detection
time, and record time are three different clocks.

### actor_role

Who performed or reported the action, expressed as a role rather than a person,
for example team leader, nurse, recorder, or system.

Why it matters:

- Context for interpretation. "Adrenaline given" reported by the person pushing
  the drug is stronger evidence than the same phrase overheard from across the
  room.
- It can feed fusion and explanation without identifying a specific individual.

Design caution: this must stay consistent with Decision 014 (Human Confirmation
Instead of Recorder Confirmation), which makes confirmation role agnostic.
`actor_role` should be optional and advisory context, never a gate that blocks
an event because a particular role was not present. Note that `CorrectionRecord`
already has a free text `corrected_by`; `actor_role` would be a structured,
enumerated concept and the two should be reconciled when it is added.

---

## Open Observations (for later triage, not action items)

These surfaced during review and are worth tracking, but no change is proposed
in this note.

- Identifier types are inconsistent across modules. `ClinicalEvent.id` and
  `Evidence.id` are `UUID`, but `backend/services/confirmation.py` and
  `backend/services/evidence_fusion.py` refer to event and evidence ids as
  plain `str`. This should be unified before those services are implemented.
- `payload` is intentionally untyped today. Section 2 (Concern C) is where this
  would be addressed if we adopt typed per-type payloads.
- Event confidence is unenforced against evidence confidence today. Section 3
  records this so it is not mistaken for a validated invariant.
- `EventType.TODO` and other `TODO` enum members are scaffolding and should be
  removed before real ingestion.

---

## What This Note Does Not Do

- It does not modify any model in `backend/workflow/events.py`.
- It does not add `detected_at` or `actor_role`.
- It does not change any decision in `docs/decisions.md`.

It is a design record to guide the next implementation slice.
