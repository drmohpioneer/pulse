# Pulse Architecture

Version: 1.0

---

# Design Philosophy

Pulse is a real-time Clinical State Engine.

The system does not reason directly from audio.

Instead, Pulse continuously transforms uncertain evidence into an evolving clinical understanding of the resuscitation.

Every layer has a single responsibility.

---

# High-Level Architecture

                    User Interface
                          │
                          ▼
                Clinical Copilot Layer
                          │
                          ▼
                Clinical State Engine
                          │
                          ▼
                Evidence Fusion Engine
               ╱         │         ╲
              ▼          ▼          ▼
      Speech Pipeline  Acoustic Pipeline  Manual Confirmations
              │          │
              ▼          ▼
          Raw Audio Capture (Recorder's Phone)

---

# Layer 1 — Audio Capture

Purpose:

Capture the environment without changing the existing CPR workflow.

Current Design:

- Single recorder phone
- Continuous audio stream
- Real-time processing

Future possibilities:

- Multiple phones
- Defibrillator integration
- Patient monitor integration
- Smart compression devices

---

# Layer 2 — Evidence Extraction

Purpose:

Convert raw audio into structured evidence.

This layer DOES NOT make clinical decisions.

It only extracts observations.

Examples:

Speech Evidence

- "Shock delivered"
- "1 mg adrenaline"
- "Resume compressions"

Acoustic Evidence

- Defibrillator charging
- Defibrillator discharge
- Monitor alarm
- Rhythm tone change (future)

Each observation contains:

- timestamp
- confidence
- source
- raw evidence

Sub-pipelines:

- voice activity detection
- speaker diarization
- speech recognition
- ACLS vocabulary biasing
- medical event extraction
- audio event extraction

Detailed design lives in `docs/obstacle_1_perception.md`.

## Multimodal Voice Intelligence Layer

Pulse v0.4 introduces a provider-neutral multimodal perception layer.

Purpose:

Convert noisy room audio into structured evidence without making clinical
decisions.

The layer may perform:

- audio chunking
- voice activity detection
- speaker diarization
- speaker stream labeling
- optional role hypotheses
- multilingual transcription
- English medical language normalization
- Egyptian Arabic medical language normalization
- Arabic/English code-switching normalization
- non-speech acoustic event detection

The output of this layer is always Evidence.

It may create candidate observations such as:

- command
- intent
- observation
- completed_action
- correction

The distinction between command and completed action is clinically important.

Examples:

- "give epi" is a command.
- "ادي ادرينالين" is a command.
- "shock اتعمل" is a completed action observation.
- "ROSC حصل" is an observation of ROSC.

The perception layer must not:

- create accepted clinical state
- bypass confirmation policy
- decide ACLS actions
- call or mutate state machines
- override deterministic workflow output

Target flow:

Raw room audio

↓

Audio chunking, VAD, diarization, ASR, acoustic detection

↓

Normalized observations

↓

Evidence

↓

Evidence Fusion Engine

↓

Candidate ClinicalEvents

↓

Confirmation when required

↓

Accepted/corrected events enter the deterministic Clinical State Engine

Current live-audio foundation:

- browser microphone chunk capture in the demo UI
- ordered audio chunk metadata/reference ingestion
- multipart browser audio upload to temporary local session storage
- provider-neutral ASR contracts
- provider-neutral diarization/role metadata contracts
- provider-neutral acoustic event contracts
- deterministic fake ASR for tests and demo-safe operation
- deterministic fake diarization and acoustic providers for tests/demo metadata
- env-gated OpenAI ASR adapter for server-side audio references
- env-gated remote diarization/acoustic skeletons that fail closed without
  configuration

This foundation still produces transcripts and Evidence only. Temporary local
audio files exist only to bridge browser chunks to ASR and are governed by a
configurable cleanup policy. It does not implement production audio retention,
activate provider-backed diarization/acoustic models, or alter the deterministic
clinical engine boundary.

---

# Layer 3 — Evidence Fusion Engine

Purpose:

Combine multiple pieces of evidence describing the same event.

Example

Speech:
"Shock."

Acoustic:
Defibrillator discharge.

Recorder:
"Shock delivered."

↓

Confidence increases.

This layer produces validated clinical events.

It never invents events.

A clinical event may be supported by one evidence item or many evidence items.

Multiple independent evidence items increase confidence.

Low-confidence, conflicting, or high-impact events can request manual confirmation.

## Event Validation

Every detected clinical event receives a confidence score based on the available evidence.

Examples of evidence include:

- Speech recognition
- Acoustic event recognition
- Recorder confirmation
- Future device integrations

High-confidence low-impact events may be automatically committed according to
deterministic policy. High-impact events remain confirmation-gated unless they
have explicit manual/device/acoustic corroboration allowed by policy.

Low-confidence events are presented to the recorder for confirmation before entering the Clinical State Engine.

Events remain editable and may later be corrected or revoked if new information becomes available.

---

# Layer 4 — Clinical State Engine

### Internal Clinical State

The Clinical State Engine maintains multiple concurrent deterministic state machines.

Examples include:

- Rhythm State Machine
- CPR Cycle State Machine
- Medication Timeline State Machine
- Defibrillation State Machine
- Airway State Machine
- ROSC State Machine
- Reversible Causes Tracker (H's & T's)

These state machines evolve independently while sharing information through the Clinical State Engine.

The next recommended intervention is determined from their combined state according to the ACLS guidelines.

## State Recalculation

The Clinical State Engine must support dynamic recalculation.

If an accepted event is later corrected or removed, every affected state machine must recompute its state from the updated event history.

This ensures that all recommendations remain consistent with the current clinical reality.

---

# Layer 5 — Clinical Copilot

Purpose

Reason over the Clinical State.

Examples

- Suggest next intervention.
- Warn about delayed medications.
- Highlight forgotten reversible causes.
- Generate summaries.
- Produce documentation.

The LLM never owns the patient state.

It only reasons about it.

---

# User Interface

The interface should display:

Current clinical state.

Upcoming interventions.

Timeline.

Confidence indicators.

Critical reminders.

Confirmation requests.

Evidence confidence when relevant.

The UI should reduce cognitive load.

It should never overwhelm the clinician.

---

# Error Handling

Pulse assumes uncertainty.

Every event carries confidence.

High confidence

↓

Automatic acceptance.

Low confidence

↓

Request confirmation.

Pulse never hides uncertainty.

---

# Security Architecture

Frontend

Presentation only.

Backend

Business logic.

Clinical reasoning.

Authentication.

State management.

Secrets.

API keys.

The frontend never contains secrets.

---

# Scalability

Every layer should be independently replaceable.

For example:

Replace speech model.

↓

Nothing else changes.

Replace LLM.

↓

Nothing else changes.

Replace audio source.

↓

Nothing else changes.

Loose coupling.

High cohesion.

---

# Guiding Principle

Everything inside Pulse ultimately exists to improve one thing:

The Clinical State Engine.

Every component either:

- provides evidence,
- improves state estimation,
- or helps clinicians understand the state.

Nothing else.

---


# Layer 5 — Clinical Reasoning

This layer has two independent components.

## ACLS State Machine

Implements the official ACLS algorithms as deterministic state machines.

Responsibilities:

- Track the current ACLS pathway.
- Determine the next guideline-directed intervention.
- Monitor timing requirements.
- Follow branching algorithms based on patient state.

This component does not use an LLM.

---

## AI Clinical Copilot

Reasons around the current clinical state.

Responsibilities include:

- Suggesting reversible causes (H's & T's).
- Highlighting overlooked possibilities.
- Summarizing the resuscitation.
- Answering clinician questions.
- Providing contextual clinical insights.

The AI never modifies or overrides the deterministic ACLS pathway.

It provides decision support alongside it.

# Adaptive Presentation Layer

The presentation layer is driven by the Clinical Workflow Engine.

It does not simply render stored data.

Instead, it selects which information deserves the user's attention according to the current clinical context.

Primary components include:

- Current Action
- Upcoming Action
- CPR Cycle Timer
- Total Arrest Timer
- Human Confirmations
- Reversible Causes Panel
- Clinical Timeline

The UI progressively reveals information while minimizing cognitive load.
