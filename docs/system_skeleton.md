# Pulse System Skeleton

Version: 1.0
Status: Draft

---

# Purpose

This document defines how Pulse should work before implementation begins.

It converts the product vision into a buildable system skeleton while preserving the central rule:

Pulse reasons over Clinical State, not raw audio, transcripts, or chat history.

---

# Product Loop

Pulse runs a continuous loop during cardiac arrest:

1. Capture evidence from the room.
2. Extract structured observations.
3. Fuse observations into clinical events.
4. Update deterministic clinical state.
5. Ask for confirmation when confidence is low.
6. Show the state and next relevant reminders.
7. Let the copilot reason over the state only.

---

# System Layers

## 1. Input Layer

Initial source:

- One recorder phone

Initial input types:

- live microphone audio
- manual event confirmations
- manual event corrections
- simulated event stream for development and demo

Future input types:

- defibrillator integration
- patient monitor integration
- compression device integration
- multiple phones

This layer does not decide what happened clinically.

---

## 2. Audio Preprocessing Layer

Responsibilities:

- voice activity detection
- noise handling
- audio chunking
- timestamping
- buffering
- stream health monitoring

Outputs:

- speech candidate segments
- non-speech audio segments
- audio quality metadata

This layer prepares evidence for downstream extraction.

It does not produce final clinical events.

---

## 3. Speech Understanding Layer

Responsibilities:

- speech recognition
- speaker diarization
- ACLS vocabulary biasing
- partial transcript streaming
- transcript confidence estimation
- provider capability adaptation

Outputs:

- timestamped transcript segments
- speaker labels when available
- confidence scores
- raw transcript evidence

Important rule:

Speaker labels help interpret context, but Pulse should not require perfect speaker identity to update state.

The layer should allow multiple speech-related extraction passes when one provider cannot provide diarization, streaming, and vocabulary guidance in the same call.

---

## 4. Medical Event Extraction Layer

Responsibilities:

- convert transcript segments into structured clinical observations
- detect explicit mentions of CPR events
- detect medication statements
- detect rhythm statements
- detect shock-related statements
- detect airway and reversible-cause statements
- assign confidence and uncertainty reason

Examples:

- "Shock delivered" becomes a shock-delivered observation.
- "Give adrenaline" becomes a medication-intent observation, not automatically medication-given.
- "Adrenaline is in" becomes a medication-given observation.

This layer extracts possible observations.

It does not update clinical state.

---

## 5. Audio Event Extraction Layer

Responsibilities:

- detect non-speech clinical audio patterns
- identify possible defibrillator charging sounds
- identify possible shock/discharge sounds
- identify monitor alarms or rhythm-related audio later
- identify CPR metronome or compression-related sounds later

Outputs:

- timestamped acoustic observations
- confidence scores
- source metadata

Important rule:

Acoustic observations are supporting evidence, not final clinical truth.

---

## 6. Manual Confirmation Layer

Responsibilities:

- let the recorder confirm low-confidence events
- let the recorder correct wrong events
- let the team manually add clinically important events
- preserve audit trail of confirmations and corrections

Manual confirmation is an evidence source.

It can raise event confidence or override an uncertain candidate according to deterministic rules.

---

## 7. Evidence Fusion Engine

Responsibilities:

- group observations that likely refer to the same clinical event
- combine evidence from speech, acoustic signals, and manual confirmation
- assign final event confidence
- identify conflicts
- request confirmation when needed
- emit validated clinical events

Example:

Speech observation:

- "shock delivered", confidence 0.78

Acoustic observation:

- defibrillator discharge sound, confidence 0.82

Manual observation:

- recorder taps confirm shock, confidence 1.0

Fused event:

- shock delivered, confidence high

Important rule:

A clinical event may be supported by one evidence item or many evidence items.

Multiple independent evidence items increase confidence.

One high-confidence evidence item may be enough for low-risk state updates, but high-impact or ambiguous events may still require confirmation depending on policy.

---

## 8. Clinical Event Store

Responsibilities:

- persist accepted clinical events
- retain rejected or corrected candidates
- maintain an audit trail
- provide replay support

The event store is the backbone of explainability.

Pulse should always be able to explain why the state changed.

---

## 9. Clinical State Engine

Responsibilities:

- maintain the current resuscitation state
- apply clinical events deterministically
- compute timers
- track shock count
- track medication timing
- track CPR pause and resume events
- track rhythm checks
- track reversible causes
- expose state snapshots

The Clinical State Engine never calls an LLM.

It is deterministic and testable.

---

## 10. Clinical Copilot

Responsibilities:

- reason over structured clinical state
- produce concise reminders
- summarize important state changes
- highlight missing or delayed actions
- prepare documentation summaries

The copilot does not own state.

It does not invent events.

It must cite or reference the state fields that triggered its output.

---

## 11. Dashboard

Responsibilities:

- show current clinical state
- show timing-critical information
- show event timeline
- show active confirmation requests
- show uncertainty clearly
- provide manual correction controls

The dashboard should be calm and clinically readable.

It should not feel like a chatbot-first interface.

---

# Core Data Objects

## Evidence

An uncertain observation from one source.

Fields:

- id
- source
- type
- timestamp
- confidence
- payload
- raw_reference
- uncertainty_reason

Sources:

- speech
- acoustic
- manual
- simulated
- device_future

---

## Clinical Event

A fused, clinically meaningful event.

Fields:

- id
- event_type
- timestamp
- confidence
- evidence_ids
- confirmation_status
- payload
- created_at

Examples:

- cpr_started
- cpr_paused
- cpr_resumed
- rhythm_checked
- shock_delivered
- medication_given
- airway_secured
- reversible_cause_considered

---

## Clinical State

The current structured state of the resuscitation.

Fields:

- arrest_started_at
- current_rhythm
- cpr_status
- last_rhythm_check_at
- last_shock_at
- shock_count
- medications
- airway_status
- reversible_causes
- active_confirmations
- timeline

---

## Confirmation Request

A request for human confirmation when uncertainty matters.

Fields:

- id
- candidate_event_id
- reason
- suggested_event
- confidence
- options
- expires_at

---

# First Build Slice

Recommended first implementation slice:

Build the Clinical State Engine and replay simulated CPR events through it.

This should include:

- event schema
- state schema
- deterministic state transitions
- event timeline
- confirmation request model
- simple dashboard
- replay fixtures
- tests

Why this first:

It proves the heart of Pulse before adding complex perception.

---

# Non-Goals for First Build

- real microphone ingestion
- production diarization
- production speech-to-text
- production acoustic event recognition
- hospital integration
- autonomous clinical decision making
