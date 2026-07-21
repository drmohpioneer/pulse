# Pulse Project State

Version: 1.0

---

## Current Phase

Pulse v0.5 — submitted to OpenAI Build Week (July 2026).

---

## Current Milestone

Live microphone capture, swappable multi-vendor ASR adapter (fake / OpenAI /
Groq / ElevenLabs / any OpenAI-compatible endpoint), and the end-to-end demo
shipped on top of the v0.4 multimodal voice-intelligence safety slice.

---

## Technology Decisions

The core technology stack has been officially accepted. See `docs/tech_stack.md` for the full record.

- Frontend: Next.js, TypeScript, Tailwind CSS
- Backend: FastAPI, Python, Pydantic
- Testing: Pytest
- Version Control: Git, GitHub

The stack is no longer under discussion.

---

## Current Obstacle

Obstacle #1

Reliable multimodal perception of clinically meaningful events during real cardiac arrest using a single recorder phone in a noisy room.

Status:
In Progress

---

## Last Completed

- Deterministic ACLS workflow engine
- Rhythm, CPR, shock, medication, and ROSC state machines
- Hs & Ts reversible-cause reasoning
- Workflow coordinator
- Replay and correction architecture
- Evidence fusion layer
- Transcript pipeline
- Safe clinical copilot boundary
- First mobile UI
- Provider-neutral multimodal perception contracts
- Diarization placeholder
- Deterministic English/Egyptian Arabic phrase normalization for the first supported phrases
- Normalized observation to Evidence conversion
- Conservative command/intent confirmation behavior in the fusion layer
- Multimodal evidence grouping before fusion
- High-impact event confirmation policy
- Mutually exclusive high-impact conflict grouping
- Manual confirmation semantics for high-impact acceptance
- Acoustic and future-device source-gating safety fixes
- Expanded deterministic Arabic/English phrase normalization for medication,
  shock, CPR, rhythm, ROSC/pulse, and correction/negation phrase families
- UI-safe negative-only correction fusion results with auditable evidence and no
  positive-shaped candidate event
- Deterministic medication dose/route extraction for supported English,
  Egyptian Arabic, and mixed phrases
- Human confirmation/rejection actions for voice-derived candidates
- Provider-neutral simulated live transcription session foundation
- Provider-neutral live audio + ASR adapter foundation with deterministic fake
  ASR
- Env-gated OpenAI ASR adapter path for server-side audio references, with
  fake/demo fallback when configuration is absent
- Browser audio upload/storage bridge for microphone chunks with temp local
  session storage and ASR handoff
- Explicit human-authored correction payload flow
- Minimal local JSONL demo audit/session persistence with deterministic replay
  from accepted/corrected timeline events
- Provider-neutral diarization and role metadata adapter skeleton with
  deterministic fake provider
- Provider-neutral acoustic event adapter skeleton with deterministic fake
  defibrillator discharge support
- Multimodal perception slice tests
- Backend and frontend tests passing

---

## Current Task

Work in progress:

- Harden Arabic/English medical normalization with richer timing,
  speaker-context, and correction-target handling.
- Harden browser audio upload UX and provider error recovery before field demos.
- Harden correction UX and replay/audit browsing before production storage.
- Add real provider-backed ASR/diarization research after provider decision.
- Add real acoustic event detection after validation design.

---

## Current Limitations

- Current voice pipeline supports deterministic manual transcripts, simulated
  live transcript sessions, browser microphone chunk upload to temporary local
  session storage, fake ASR by default, and env-gated OpenAI ASR for stored
  server-side audio references.
- Real speaker diarization is not activated yet; current support is
  provider-neutral contracts plus deterministic fake/demo metadata.
- Role assignment is advisory metadata only and currently comes from fake/demo
  metadata, not a validated clinical identity system.
- Egyptian Arabic medical speech and Arabic/English code switching are covered
  by deterministic phrase tables for the current v0.4 families, but broad
  natural-language understanding is not implemented.
- Real acoustic event detection is not activated yet; current support is
  provider-neutral contracts plus deterministic fake/demo metadata.
- Multimodal fusion is still early and does not yet combine live voice, acoustic
  events, and manual confirmation in a production-ready loop.
- Future device corroboration requires explicit event-type allowlist expansion
  before any provider-specific device evidence can support high-impact
  acceptance.
- Demo state is in memory and not yet patient/session isolated.
- Audit persistence is local JSONL demo storage, not a production clinical
  record or compliance boundary.
- Audio chunk storage is temporary local demo storage with opportunistic
  retention cleanup, not production retention/compliance infrastructure.
- AI/copilot output is intentionally state-bound and cannot modify clinical state.

---

## Completed v0.4 Milestone

- Minimal multimodal voice-intelligence slice:
  - AudioChunk contract.
  - VoiceActivitySegment contract.
  - SpeakerTurn contract.
  - SpeakerRoleHypothesis contract.
  - MultilingualTranscriptSegment contract.
  - NormalizedClinicalObservation contract.
  - AcousticObservation contract.
  - Diarization placeholder.
  - Deterministic Arabic/English medical phrase normalization for the first supported phrases.
  - Evidence conversion.
  - Multimodal grouping before evidence fusion.
  - Conservative confirmation behavior for commands, intent, and high-impact events.
  - High-impact conflict grouping and source-semantics safety fixes.
  - Expanded deterministic phrase coverage across the first cardiac-arrest room
    speech families.
  - Negative-only evidence semantics that preserve auditability without creating
    misleading positive confirmation candidates.
  - Deterministic medication dose/route extraction for the supported
    epinephrine, amiodarone, and lidocaine phrase families.
  - Human confirmation/rejection actions for voice-derived candidates.
  - Simulated live transcription session API and UI foundation.
  - Live audio session API, fake ASR provider, env-gated remote ASR skeleton,
    and minimal browser microphone UI foundation.
  - OpenAI ASR adapter implementation behind env configuration with provider
    status/error reporting and fake fallback when configuration is absent.
  - Browser `MediaRecorder` blob upload bridge to temporary local audio files
    for real-ASR handoff.
  - Explicit correction endpoint and UI form requiring user-selected corrected
    event type/payload.
  - Local JSONL audit store with replay from accepted/corrected events only.
  - Diarization/role metadata adapter skeletons and UI display as advisory
    evidence metadata.
  - Acoustic event adapter skeleton with fake defibrillator discharge
    corroboration through existing source-gated fusion.

---

## Next Planned Milestone

- Arabic/English normalization hardening:
  - Add context-safe correction target resolution.
  - Add richer medication timing phrase handling.
  - Add adversarial phrase fixtures for negation, overlap, and ambiguous speech.
  - Keep all outputs as Evidence until confirmation/fusion policy determines
    event status.
- Live transcription hardening:
  - Replace scripted polling with a provider-neutral streaming transport.
  - Activate a real ASR provider only after credentials, storage, consent,
    audit, and retention decisions are made.
  - Add durable session/audit storage.
  - Add real ASR/diarization adapter only after provider selection and validation.

Target pipeline:

Evidence -> confidence -> candidate event -> confirmation -> deterministic Pulse engine

Design constraint:

AI must not modify clinical state. Deterministic Pulse software remains the owner of clinical state.

---

## Long-Term Goal

Create an AI Clinical Teammate capable of maintaining a real-time Clinical State Engine during cardiac arrest.

---

## Source of Truth

Project Constitution:
AGENT.md

Architecture:
docs/architecture.md

Engineering Decisions:
docs/decisions.md

Vision:
docs/vision.md

Roadmap:
docs/roadmap.md

Tech Stack:
docs/tech_stack.md

Journal:
docs/journal.md

Demo:
docs/demo.md

Prompts:
docs/prompts.md

System Skeleton:
docs/system_skeleton.md

Obstacle 1:
docs/obstacle_1_perception.md

This file only describes the CURRENT STATE of the project.

---

## Open Questions

- Which speech/audio model stack should be used for diarization, multilingual transcription, and acoustic event detection?
- What confidence thresholds should route lower-impact multimodal events to automatic acceptance versus human confirmation?
- How should role assignment confidence influence evidence fusion without making role identity mandatory?
- Which future device evidence types should be allowlisted for each high-impact event type?
