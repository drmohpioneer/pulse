# Pulse Engineering Decision Log

Version: 1.0

---

## How to Use This Document

This document is the project's engineering memory.

Every important architectural or product decision must be recorded here.

Each decision should answer:

- What did we decide?
- Why?
- What alternatives were considered?
- What is the current status?

Documentation is the source of truth.

---

# Decision 001

## Title

Pulse is a Clinical Teammate, not a CPR Timer.

### Status

Accepted

### Decision

Pulse's primary purpose is to reduce the cognitive load of the resuscitation team by maintaining situational awareness and understanding the evolving clinical state.

### Why

Existing tools primarily function as timers or documentation aids.

Our differentiation is real-time clinical understanding.

---

# Decision 002

## Title

The Clinical State Engine is the Core of Pulse.

### Status

Accepted

### Decision

The central component of the system is the Clinical State Engine.

Every subsystem exists to improve or consume this state.

### Why

Clinical reasoning should operate on structured patient state rather than raw transcripts or audio.

---

# Decision 003

## Title

Evidence Fusion Instead of Single-Source Recognition.

### Status

Accepted

### Decision

Clinical events should be inferred from multiple evidence sources whenever possible.

Examples:

- Speech
- Acoustic events
- Future monitor integration
- Future medical device integration
- Manual confirmation

### Why

Single-source recognition is more error-prone in chaotic resuscitation environments.

Multiple independent pieces of evidence increase confidence and reliability.

---

# Decision 004

## Title

One Recorder Phone is the Initial Hardware Platform.

### Status

Accepted

### Decision

Pulse will initially operate from a single smartphone placed by the recorder.

### Why

This matches current ACLS workflows.

No additional hardware is required.

Lower adoption barrier.

Future hardware integrations remain possible.

---

# Decision 005

## Title

Architecture Before Code.

### Status

Accepted

### Decision

Documentation and architecture are completed before implementation begins.

### Why

A clear architecture reduces technical debt, improves AI collaboration, and keeps the project aligned.

---

# Decision 006

## Title

Security Is a Foundational Requirement.

### Status

Accepted

### Decision

Pulse will be engineered as if it were intended to become a production medical product.

Security is not optional, even during the hackathon.

### Why

Medical software handles sensitive information and must be designed with strong engineering practices from the beginning.

---

# Decision 007

## Title

AI Roles Are Separated.

### Status

Accepted

### Decision

ChatGPT acts as Chief Architect.

Codex acts as Senior Software Engineer.

The repository documentation is the shared source of truth.

### Why

Separating responsibilities leads to more consistent design decisions and higher-quality implementation.

---

# Decision 008

## Title

One Problem at a Time.

### Status

Accepted

### Decision

Design discussions proceed sequentially.

Only one architectural problem is discussed and resolved before moving to the next.

### Why

This keeps reasoning focused and prevents important decisions from being forgotten.

---

# Decision 009

## Title

Perception Requires Multiple Evidence Pipelines.

### Status

Accepted

### Decision

Pulse will not rely on speech recognition alone to understand CPR events.

The perception layer will include voice activity detection, speaker diarization, speech recognition, ACLS vocabulary biasing, medical event extraction, acoustic event extraction, and manual confirmation.

### Why

Cardiac arrest rooms are noisy and chaotic.

Speech alone can miss, mishear, or misclassify clinically important events.

Multiple evidence sources improve reliability and allow the system to expose uncertainty.

---

# Decision 010

## Title

Clinical Events May Have One or Many Evidence Items.

### Status

Accepted

### Decision

A clinical event may be supported by a single evidence item or by multiple independent evidence items.

Pulse should not require multiple evidence sources for every event.

However, agreement across independent evidence sources should increase confidence.

Low-confidence, conflicting, or high-impact events should request confirmation according to deterministic policy.

### Why

Some events may only be captured by one source.

Requiring multiple evidence sources for every event would create missed events.

Accepting every single-source event without uncertainty handling would be unsafe.

---

# Decision 011

## Title

Build State and Fusion Before Live Audio.

### Status

Proposed

### Decision

The first implementation slice should use simulated evidence streams to build the Evidence Fusion Engine, Clinical State Engine, event timeline, confirmation model, and dashboard before integrating live audio.

### Why

The core innovation is clinical state reasoning, not audio capture.

Simulated streams let us validate the architecture, data contracts, and safety behavior before adding noisy perception complexity.

---

# Decision 012

## Title

The ACLS Algorithm Is Deterministic

### Status

Accepted

### Decision

Pulse will implement the official ACLS cardiac arrest algorithms as a deterministic state machine.

The AI layer is not permitted to alter or replace the official ACLS workflow.

### Why

The ACLS algorithm is an evidence-based clinical guideline with multiple branches based on rhythm, timing, interventions, and patient state.

Following the algorithm deterministically improves safety, predictability, and clinician trust.

AI should assist clinicians where uncertainty exists, but it should not redefine established resuscitation algorithms.

---

# Decision 013

## Title

Pulse Uses Parallel Clinical State Machines

### Status

Accepted

### Decision

Pulse will not model cardiac arrest as a single linear algorithm.

Instead, Pulse maintains multiple deterministic state machines running in parallel, including but not limited to:

- Rhythm state
- CPR cycle state
- Medication timeline
- Shock timeline
- Airway state
- ROSC state
- Reversible causes (H's & T's)

The next guideline-directed action is derived from the combined state of these parallel processes.

### Why

Real cardiac arrest management is dynamic.

Patients may transition between ACLS algorithms (e.g., asystole to VF/pVT) while other timelines, such as medication intervals, continue uninterrupted.

A parallel-state architecture more accurately reflects real clinical practice and produces deterministic recommendations aligned with ACLS.

---

# Decision 014

## Title

Human Confirmation Instead of Recorder Confirmation

### Status

Accepted

### Decision

Pulse requires confirmation from any trusted human interacting with the device.

The system does not assume a specific role (recorder, physician, nurse, etc.).

The confirmation mechanism is role-agnostic.

### Why

Clinical workflows vary across hospitals.

Pulse should integrate into existing workflows rather than enforcing one.

---

# Decision 015

## Title

Adaptive User Interface

### Status

Accepted

### Decision

The Pulse interface adapts according to the current clinical context rather than elapsed time.

Possible contexts include:

- Initial Assessment
- Shockable Arrest
- Non-Shockable Arrest
- Persistent Arrest
- ROSC

Each context emphasizes the information most relevant to that stage of resuscitation.

### Why

Clinical priorities change because patient state changes, not because a fixed amount of time has passed.

---

# Decision 016

## Title

Progressive Information Disclosure

### Status

Accepted

### Decision

Pulse always prioritizes the most actionable information.

The interface emphasizes:

1. Current action
2. Immediate upcoming action
3. Essential timers

Additional information (H's & T's, history, evidence, transcript, etc.) becomes more prominent only when clinically relevant.

### Why

Reducing cognitive load is a primary design objective.

---

# Decision 017

## Title

Adaptive Presentation Layer

### Status

Accepted

### Decision

Pulse adapts its user interface according to the current clinical context rather than elapsed time.

Examples of contexts include:

- Initial Assessment
- Shockable Arrest
- Non-Shockable Arrest
- Persistent Arrest
- ROSC

The Clinical Workflow Engine determines the active context.

Each context emphasizes the information most relevant to that stage of resuscitation.

### Why

Clinical priorities change because the patient's state changes, not because a fixed amount of time has elapsed.

---

# Decision 018

## Title

Accepted Core Technology Stack.

### Status

Accepted

### Decision

The core implementation stack is officially accepted:

- Frontend: Next.js, TypeScript, Tailwind CSS
- Backend: FastAPI, Python, Pydantic
- Testing: Pytest
- Version Control: Git, GitHub

Full record and rationale live in `docs/tech_stack.md`.

Provider-level choices that are not part of the core stack (LLM provider, speech-to-text provider, database engine, deployment target) remain open and will be recorded as separate decisions when chosen.

### Why

A settled core stack unblocks implementation and keeps the frontend/backend contract clear.

The chosen tools support typed contracts, deterministic clinical state handling, strong validation, reliable testing, and a maintainable path toward a real medical product.

---

# Decision 019

## Title

Multimodal Perception Produces Evidence Only

### Status

Accepted

### Decision

The multimodal voice intelligence layer converts room audio into Evidence records. It may perform voice activity detection, diarization, speech recognition, role labeling, language normalization, and acoustic event detection, but it must not create accepted clinical state, bypass confirmation policy, or decide ACLS actions.

### Why

Pulse's clinical safety depends on preserving the deterministic Clinical State Engine as the source of truth.

---

# Decision 020

## Title

Commands and Completed Actions Must Remain Distinct

### Status

Accepted

### Decision

Medical language normalization must preserve whether a phrase is a command, intent, observation, question, correction, or completed action. Commands such as "give epi", "give adrenaline", "ادي ادرينالين", or "ادي ابي واحد ملي" are evidence of intent, not proof that medication was administered.

Arabic and mixed Arabic/English phrases must preserve the same distinction:

- "ادي ادرينالين" -> medication command, not completed administration.
- "ادي ابي واحد ملي" -> epinephrine 1 mg command, not completed administration.
- "ارجع CPR" -> CPR resume command, not proof CPR resumed.
- "shock اتعمل" -> completed shock observation.
- "في نبض" and "ROSC حصل" -> ROSC observations requiring conservative confirmation policy.

### Why

Confusing intended actions with completed clinical events could corrupt the deterministic event timeline.

---

# Decision 021

## Title

Deterministic High-Impact Confirmation and Conflict Arbitration

### Status

Accepted

### Decision

High-impact clinical events require deterministic confirmation policy and
conflict arbitration before they can enter the deterministic clinical engine.

High-impact event types are:

- `MEDICATION_GIVEN`
- `SHOCK_DELIVERED`
- `RHYTHM_CHECKED`
- `ROSC_ACHIEVED`

Speech-only high-impact completed actions or observations must not auto-accept
solely because confidence is high. Commands and intents remain contextual
evidence only and must not increase confidence toward accepting completed
clinical events.

Mutually exclusive high-impact evidence inside the deterministic grouping window
must be routed into one conflict result or otherwise blocked as
`NEEDS_CONFIRMATION`. Examples include VF vs PEA, incompatible same-medication
payload details, and ROSC evidence conflicting with active-arrest rhythms.

Manual corroboration for high-impact acceptance requires actual confirmation
semantics. `source == MANUAL` alone is not enough; the evidence must be a manual
confirmation or carry explicit confirmation metadata.

Acoustic and future-device corroboration must be source-gated and event-type
scoped. Defibrillator discharge acoustic evidence may corroborate
`SHOCK_DELIVERED`; future device evidence must be added through explicit
allowlist entries for the clinical event types it can safely corroborate.

### Why

High-impact events can change rhythm pathway, shock count, medication history, or
ROSC state. Allowing speech-only evidence, command evidence, broad manual source
labels, or generic future-device sources to auto-accept these events could
corrupt the clinical timeline. Deterministic confirmation and conflict
arbitration keep perception as evidence reconstruction while preserving the
clinical engine as the source of truth.
