# Obstacle 1: Reliable Perception in a CPR Room

Version: 1.0
Status: In Progress

---

# Problem

Pulse must understand clinically meaningful events during cardiac arrest from a chaotic room.

The room may include:

- multiple speakers
- overlapping speech
- alarms
- monitor sounds
- defibrillator sounds
- CPR-related noise
- hurried communication
- incomplete statements

Speech recognition alone is not enough.

Pulse needs multiple evidence sources and explicit uncertainty handling.

---

# Agreed Direction

Use one recorder phone as the initial hardware platform.

Do not change the existing ACLS workflow.

The recorder phone captures audio from the room.

The system extracts evidence through multiple pipelines:

- voice activity detection
- speaker diarization
- speech recognition
- ACLS vocabulary biasing
- medical event extraction
- audio event extraction
- manual confirmation

Those evidence sources feed the Evidence Fusion Engine.

The Evidence Fusion Engine emits clinical events.

The Clinical State Engine updates state deterministically.

---

# Important Clarification

A clinical event may be supported by one evidence item or many evidence items.

The system should not require multiple evidence sources for every event.

However, when multiple independent sources agree, confidence should increase.

When confidence is low or the event is high impact, Pulse should ask for confirmation.

---

# Perception Pipeline

## Voice Activity Detection

Purpose:

Detect when speech is happening and split audio into manageable segments.

Expected outputs:

- speech segment start time
- speech segment end time
- confidence or quality metadata

Risk:

Noisy CPR rooms may trigger false positives or miss quiet speech.

Mitigation:

Treat VAD as a routing signal, not clinical truth.

---

## Speaker Diarization

Purpose:

Estimate who is speaking when.

Expected outputs:

- transcript segment
- speaker label
- timestamp

Risk:

Speaker labels may be unstable in overlapping speech.

Mitigation:

Use speaker labels as helpful context, not as the only basis for clinical state.

The system should work even if speaker identity is uncertain.

---

## Speech Recognition

Purpose:

Transcribe spoken clinical communication.

Expected outputs:

- transcript text
- timestamp
- confidence
- optional speaker label

Risk:

Critical phrases may be missed or misheard.

Mitigation:

Bias transcription toward ACLS vocabulary and combine with acoustic/manual evidence.

---

## ACLS Vocabulary Biasing

Purpose:

Improve recognition of likely resuscitation terms.

Example vocabulary:

- CPR
- compressions
- pause compressions
- resume compressions
- rhythm check
- shock
- charging
- clear
- adrenaline
- epinephrine
- amiodarone
- airway
- intubated
- VF
- VT
- PEA
- asystole
- H's and T's

Risk:

Over-biasing may hallucinate clinical terms.

Mitigation:

Use vocabulary guidance to improve recognition, not to create events without evidence.

---

## Medical Event Extraction

Purpose:

Convert transcript segments into structured observations.

Example distinction:

- "Prepare adrenaline" is intent.
- "Give adrenaline" may be command or intent.
- "Adrenaline given" is stronger evidence of administration.

Risk:

Commands, plans, and completed actions can be confused.

Mitigation:

Represent extracted observations with type and confidence:

- intent
- command
- completed_action
- question
- correction

---

## Audio Event Extraction

Purpose:

Detect clinically meaningful non-speech audio.

Potential signals:

- defibrillator charging
- defibrillator discharge
- monitor alarms
- rhythm tones
- compression metronome

Risk:

Acoustic patterns may vary across devices and environments.

Mitigation:

Use audio events as supporting evidence and require confirmation when ambiguous.

---

## Manual Confirmation

Purpose:

Allow the recorder to confirm, reject, or correct uncertain events.

Manual confirmation is part of the perception system.

It is not a fallback failure.

It is a clinical safety feature.

---

# Evidence Fusion Policy Draft

Evidence confidence levels:

- high: can update state if event type allows automatic acceptance
- medium: may update low-risk state or request confirmation
- low: queue for confirmation or ignore depending on event type
- conflicting: request confirmation

High-impact events should be treated cautiously:

- shock delivered
- medication given
- rhythm identified
- ROSC
- termination of resuscitation

These may require stricter confidence thresholds.

---

# v0.4 Minimal Multimodal Perception Design

Status:
Active implementation target.

The first v0.4 slice remains provider-neutral. It does not capture live
microphone audio and does not call provider-specific APIs.

It defines stable contracts and a deterministic simulation path:

Simulated microphone/transcript input

↓

Audio chunk contract

↓

Diarization placeholder

↓

Multilingual transcript normalization

↓

Optional acoustic observation placeholder

↓

Evidence conversion

↓

Evidence Fusion Engine

↓

Candidate ClinicalEvents and confirmation requests

Accepted/corrected ClinicalEvents remain the only perception outputs that may
enter the deterministic Clinical State Engine.

## Audio Chunking

Purpose:

Represent bounded pieces of room audio without committing to a provider.

Expected fields:

- chunk id
- session id
- device id
- start time
- end time
- sample rate
- channel count
- audio reference or simulated text reference

For the minimal slice, audio chunks may point to simulated transcript content
instead of raw audio bytes.

## Diarization Placeholder

Purpose:

Create the interface for speaker separation without claiming reliable speaker
identity yet.

Behavior:

- returns `speaker_unknown` when no deterministic speaker label is supplied
- may produce deterministic placeholder labels such as `speaker_1`
- may emit overlap metadata
- never changes event validity
- never makes confirmation role-dependent

Role hypotheses are optional context only.

Allowed role labels:

- physician
- nurse
- recorder
- team_leader
- unknown

## Multilingual Transcript Normalization

Purpose:

Normalize English, Egyptian Arabic, and Arabic/English mixed phrases into
structured clinical observations.

The normalizer preserves observation kind:

- command
- intent
- observation
- completed_action
- correction

Examples:

- "ادي ادرينالين" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine" }`, `command`
- "ادي ابي" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine" }`, `command`
- "ادي ابي واحد ملي" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine", "dose": 1, "unit": "mg" }`, `command`
- "epi 1 mg" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine", "dose": 1, "unit": "mg" }`, `observation`
- "give epi 1 mg IV" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine", "dose": 1, "unit": "mg", "route": "IV" }`, `command`
- "الادرينالين دخل واحد ملي" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine", "dose": 1, "unit": "mg" }`, `completed_action`
- "give epi" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine" }`, `command`
- "give adrenaline" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine" }`, `command`
- "epi is in" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine" }`, `completed_action`
- "الادرينالين دخل" -> `MEDICATION_GIVEN`, `{ "medication": "epinephrine" }`, `completed_action`
- "ادي اميو" -> `MEDICATION_GIVEN`, `{ "medication": "amiodarone" }`, `command`
- "amio 300" -> `MEDICATION_GIVEN`, `{ "medication": "amiodarone", "dose": 300, "unit": "mg" }`, `observation`
- "اميو 300 اتدى" -> `MEDICATION_GIVEN`, `{ "medication": "amiodarone", "dose": 300, "unit": "mg" }`, `completed_action`
- "amio given" -> `MEDICATION_GIVEN`, `{ "medication": "amiodarone" }`, `completed_action`
- "lido 100" -> `MEDICATION_GIVEN`, `{ "medication": "lidocaine", "dose": 100, "unit": "mg" }`, `observation`
- "lidocaine given" -> `MEDICATION_GIVEN`, `{ "medication": "lidocaine" }`, `completed_action`
- "shock اتعمل" -> `SHOCK_DELIVERED`, `{}`, `completed_action`
- "صدمة اتعملت" -> `SHOCK_DELIVERED`, `{}`, `completed_action`
- "اشحن" -> `SHOCK_DELIVERED`, `{}`, `command`
- "charging" -> `SHOCK_DELIVERED`, `{ "shock_preparation": "charging" }`, `intent`
- "ارجع CPR" -> `CPR_RESUMED`, `{}`, `command`
- "CPR started" -> `CPR_STARTED`, `{}`, `completed_action`
- "وقف CPR" -> `CPR_PAUSED`, `{}`, `command`
- "VF" / "في VF" -> `RHYTHM_CHECKED`, `{ "rhythm": "vf" }`, `observation`
- "PEA" / "في PEA" -> `RHYTHM_CHECKED`, `{ "rhythm": "pea" }`, `observation`
- "اسستولي" -> `RHYTHM_CHECKED`, `{ "rhythm": "asystole" }`, `observation`
- "في نبض" -> `ROSC_ACHIEVED`, `{ "rhythm": "rosc" }`, `observation`
- "ROSC حصل" -> `ROSC_ACHIEVED`, `{ "rhythm": "rosc" }`, `completed_action`
- "مفيش نبض" -> `ROSC_ACHIEVED`, `{ "rhythm": "rosc" }`, `correction`, negative evidence

Medication-specific phrases initially map to `MEDICATION_GIVEN` with medication
details in payload. They do not introduce medication-specific EventTypes.

Medication dose/route extraction is deterministic and conservative:

- Supported medication names are epinephrine/adrenaline/epi/ادرينالين/ابي,
  amiodarone/amio/اميو, and lidocaine/lido/ليدوكايين.
- Supported dose payloads are `dose` numeric plus `unit: "mg"` for clear
  phrases in the current ACLS vocabulary: epinephrine 1 mg, amiodarone 300 mg,
  and lidocaine 100 mg.
- Dose-only medication phrases such as "epi 1 mg" are observations, not proof
  of completed administration.
- Clear routes map to `route: "IV"` or `route: "IO"` only when explicitly
  stated, including "IV", "IV push", "through the IO", "وريدي", and
  "عن طريق الوريد".
- Ambiguous routes such as both IV and IO in the same phrase do not populate
  `route`.
- Commands remain commands even when dose or route is present.
- Completed-action phrases remain completed-action evidence only. High-impact
  medication policy still requires confirmation unless supported by allowed
  corroboration.

Negation and correction handling is conservative:

- Target-specific negations produce negative correction evidence, not positive
  completed actions.
- Negative-only observations without a known accepted/candidate target remain
  evidence-only at fusion time. The fusion result has no positive-shaped
  `candidate_event`, uses `result_kind == "negative_evidence"`, preserves
  `evidence_ids`, and records the correction target event type when known.
- Negative-only observations must not generate confirmation prompts that can be
  mistaken for "confirm the positive event happened."
- Ambiguous corrections such as "cancel that", "not given", or "متدّاش" do not
  create a clinical observation until context can be represented safely.

## Acoustic Observation Placeholder

Purpose:

Reserve the interface for non-speech clinical audio.

Initial acoustic observation types:

- monitor_alarm
- defibrillator_charging
- defibrillator_discharge
- suction
- ventilator_alarm
- cpr_feedback

For the minimal slice, acoustic observations may be simulated. They are
supporting evidence unless confidence and policy allow a stronger outcome.

## Evidence Conversion

Every normalized speech or acoustic observation becomes an `Evidence` object.

Speech evidence should include:

- event_type
- payload
- observation_kind
- language
- speaker id
- role hypothesis if available
- raw text

Acoustic evidence should include:

- acoustic event type
- confidence
- timestamp span
- raw label or audio reference

Evidence is not clinical state.

## Simulated Live Transcription Foundation

The first live layer is provider-neutral and demo-only. It does not capture
microphone audio and does not call ASR providers.

Live transcript sessions support:

- start session
- stop session
- ingest ordered transcript chunks
- advance a deterministic scripted stream

Each transcript chunk includes:

- session id
- sequence number
- text
- confidence
- timestamp
- optional speaker label
- optional language

Each chunk flows through the same v0.4 multimodal pipeline used by manual demo
transcript ingestion. The live layer preserves fusion results, confirmation
requests, negative evidence audit rows, and accepted events. Only events with
`ACCEPTED` or `CORRECTED` status may enter the deterministic clinical engine.

The current scripted stream includes Arabic, English, and mixed phrases:

- command evidence
- completed action evidence requiring confirmation
- negative evidence-only audit output
- rhythm evidence requiring confirmation

This creates the UI and API foundation for streaming voice evidence without
committing to a microphone, ASR vendor, or websocket transport yet.

## Live Audio and ASR Adapter Foundation

The next v0.4 slice adds a thin real-time audio foundation while preserving the
same safety boundary.

Live audio sessions support:

- start session
- stop session
- ingest ordered browser audio chunk metadata/reference
- upload ordered browser audio chunks as multipart `FormData`
- store uploaded chunks as temporary local session files
- call a configured provider-neutral ASR adapter
- convert the ASR transcript result into the existing v0.4 multimodal pipeline

Provider-neutral ASR contracts:

- `TranscriptionProvider`
- `AudioTranscriptionRequest`
- `TranscriptChunkResult`

Transcript chunk results include:

- text
- confidence
- start/end timestamps
- optional language
- optional speaker label
- provider name
- audio reference
- provider metadata

The default demo/test provider is deterministic fake ASR. It can return a
scripted transcript from metadata for tests and returns an innocuous no-event
transcript when no scripted text is supplied. The OpenAI ASR adapter is
implemented behind environment configuration:

- `PULSE_ASR_PROVIDER=openai`
- `OPENAI_API_KEY`
- optional `PULSE_OPENAI_TRANSCRIPTION_MODEL`
- optional `PULSE_OPENAI_TRANSCRIPTION_URL`
- optional `PULSE_ASR_TIMEOUT_SECONDS`

The adapter reads a server-side audio file reference, sends it to the configured
transcription endpoint, and maps only transcript text plus optional
confidence/language/timestamp/speaker metadata back into `TranscriptChunkResult`.
It never creates ClinicalEvents.

If OpenAI/provider configuration is requested but credentials are absent, the
configured provider fails closed and the demo session falls back to deterministic
fake ASR with provider status/error metadata visible in the session response and
audit log.

The browser UI may request microphone access and produce `MediaRecorder` chunks.
The frontend uploads those blobs to `/api/demo/live-audio/uploads` with session
id, sequence number, timestamp, content type, and file data. The backend
validates active session, sequence order, allowed audio content type, and max
chunk size before writing to temporary local session storage.

Temporary storage defaults:

- root: `PULSE_AUDIO_STORAGE_DIR` or the OS temp directory under
  `pulse_live_audio`
- allowed content types: `audio/webm`, WAV variants, and MPEG/MP3 audio
- max chunk size: `PULSE_AUDIO_MAX_CHUNK_BYTES` or 5 MB
- retention: `PULSE_AUDIO_RETENTION_SECONDS` or 1 hour

Cleanup is opportunistic before writes and removes expired temp chunks. This is
demo-safe local storage, not a production audio retention/compliance design.

Safety boundary:

- ASR output is treated exactly like transcript evidence.
- ASR/perception cannot directly accept clinical events.
- ASR failures return a non-blocking live-audio chunk error response and do not
  feed the multimodal pipeline.
- High-impact confirmation policy remains unchanged.
- Negative evidence remains evidence-only/audit-safe.
- Only `ACCEPTED` or `CORRECTED` events may enter the deterministic clinical
  engine.

## Diarization, Role, and Acoustic Adapter Skeletons

Live audio now includes thin provider-neutral adapter contracts for diarization
and acoustic detection.

Diarization contracts:

- `DiarizationProvider`
- `DiarizationRequest`
- `DiarizationResult`
- `DiarizedSpeakerTurn`

Speaker turns include speaker id, confidence, timestamp span, and overlap flag.
The deterministic fake provider can read demo metadata such as `speaker_label`,
`speaker_role`, `role_confidence`, and `diarization_confidence`.

Role hypotheses are advisory only. Supported role labels are:

- `team_leader`
- `physician`
- `nurse`
- `recorder`
- `unknown`

Role metadata is persisted and shown in the UI as advisory evidence metadata,
but it does not affect event validity, confidence policy, grouping eligibility,
or engine entry.

Acoustic contracts:

- `AcousticEventProvider`
- `AcousticEventRequest`
- `AcousticEventResult`
- `AcousticEventDetection`

The deterministic fake acoustic provider supports defibrillator discharge from
explicit demo metadata. Acoustic detections convert into existing acoustic
observations and Evidence, then flow through the same grouping and fusion
policy as speech and manual evidence.

Source-gating remains strict:

- defibrillator discharge evidence can corroborate `SHOCK_DELIVERED` only when
  it comes from `ACOUSTIC` or an explicitly allowed future device source.
- a speech/manual payload that merely says `defibrillator_discharge` does not
  become acoustic corroboration.
- remote diarization/acoustic providers are skeletons behind environment
  configuration and fail closed until explicitly activated.

## Explicit Correction and Demo Audit Storage

The correction flow is explicit and human-authored.

Correction requests must reference an existing candidate event or confirmation
request and include:

- corrected `event_type`
- corrected clinical payload
- explicit `completed_action` semantics
- resolver metadata when available

Corrections create a manual `ClinicalEvent` with `EventStatus.CORRECTED`,
preserve the original evidence, and set supersession metadata when the event
model supports it. The corrected event is the only event processed through the
deterministic clinical engine.

Correction safety:

- AI/perception never invents corrected events.
- Missing correction event type is rejected.
- Rhythm corrections require an explicit rhythm payload.
- Medication corrections require an explicit medication payload and validate
  dose/unit/route when supplied.
- Command/intent-only candidates require explicit completed-action correction
  semantics before correction.
- Conflict candidates cannot be confirmed by choosing a side; they require
  rejection or explicit correction.
- Negative/evidence-only results are not positive candidates and cannot be
  corrected through the candidate endpoint without a candidate target.

Minimal persistence is local JSONL demo audit storage. Records include session
metadata, transcript/audio chunk metadata, evidence/fusion outputs,
confirmation actions, rejected candidates, corrected events, and accepted or
corrected engine timeline events. Replay rebuilds deterministic state only from
persisted `ACCEPTED` or `CORRECTED` timeline events. Rejected and negative
evidence remains auditable but does not enter replay.

## Simulated Evidence Grouping

Policy version:
`v0.4.multimodal_grouping.v1`

Before calling the Evidence Fusion Engine, the v0.4 multimodal pipeline groups
compatible evidence items so speech, acoustic observations, and manual
confirmation can describe the same candidate clinical event.

Evidence may be grouped when all of the following are true:

- The interpreted `event_type` is the same.
- The normalized clinical payload is the same.
- The evidence timestamps fall within the deterministic grouping window.

Initial grouping windows:

- 5 seconds for medication, CPR, ROSC, rhythm, and other non-shock evidence.
- 10 seconds for shock evidence, including speech plus defibrillator acoustic
  corroboration.

Conflicting evidence should still be grouped when it falls inside the window, so
the Evidence Fusion Engine can return one `NEEDS_CONFIRMATION` result instead of
separate accepted events.

High-impact conflict grouping:

- Same-event positive/negative conflicts are grouped.
- `RHYTHM_CHECKED` observations with mutually exclusive rhythms are grouped
  inside the window, for example VF vs PEA.
- `MEDICATION_GIVEN` observations for the same medication with incompatible
  payload details are grouped, for example epinephrine 1 mg vs epinephrine 10 mg.
- `ROSC_ACHIEVED` conflicts with active-arrest rhythms inside the window.
  Active-arrest rhythms are currently VF, pulseless VT, PEA, and asystole.
- The grouped conflict must resolve to `NEEDS_CONFIRMATION`.

Command and intent evidence may join a group as context, but must not increase
confidence toward accepting a completed clinical event.

Grouping is deterministic and provider-neutral. Evidence order must not change
the grouping result or the fused event status.

## Confirmation Policy

The confirmation policy remains deterministic.

Policy version:
`v0.4.high_impact_confirmation.v1`

Rules:

- Commands and intents never auto-accept as completed clinical events.
- Completed-action observations may be accepted only according to the
  deterministic confidence policy.
- High-impact events remain conservative:
  - medication given
  - shock delivered
  - rhythm identified
  - ROSC
- Conflicting evidence always requires confirmation.
- Manual confirmation remains role-agnostic.
- Speaker role may influence explanation or confidence metadata later, but it
  must never be mandatory for event validity.

High-impact event types:

- `MEDICATION_GIVEN`
- `SHOCK_DELIVERED`
- `RHYTHM_CHECKED`
- `ROSC_ACHIEVED`

High-impact policy:

- Speech-only high-impact completed-action or observation evidence does not
  auto-accept solely because confidence is high.
- High-impact events may become accepted when supported by stronger compatible
  evidence, including manual confirmation or explicitly allowlisted device
  evidence.
- Shock events may become accepted when compatible speech evidence is
  corroborated by acoustic defibrillator-discharge evidence and confidence
  passes the deterministic threshold.
- Medication, rhythm, and ROSC events require manual confirmation or an
  explicitly allowlisted future device source before automatic acceptance.
- Command and intent evidence may remain attached to the candidate event as
  context, but remains non-accepting and non-boosting.

Manual confirmation semantics:

- `source == MANUAL` alone is not proof of confirmation.
- Manual high-impact corroboration requires `evidence_type ==
  "manual_confirmation"` or explicit confirmation metadata.
- A manual observation without confirmation semantics remains evidence, but it
  does not by itself unlock high-impact automatic acceptance.

Acoustic and future-device source gating:

- Defibrillator discharge acoustic evidence may corroborate `SHOCK_DELIVERED`
  only when the evidence source is acoustic, or when the evidence is from an
  explicitly allowlisted future device source.
- A defibrillator-discharge label from the wrong source must not be interpreted
  as shock evidence.
- Future device evidence is event-type scoped. A future device source cannot
  corroborate every high-impact event by source alone.
- New device integrations must add explicit allowlist entries describing which
  device evidence types can corroborate which clinical event types.

The perception layer must never bypass `EventStatus`.

---

# Real-Time Interface Expectations

The dashboard should update continuously as evidence and state changes arrive.

The interface should distinguish:

- accepted clinical events
- candidate events
- low-confidence events
- confirmation requests
- copilot reminders

The team leader should not need to read raw transcripts during CPR.

The transcript can exist for audit and debugging, but the primary UI is clinical state.

---

# Open Technical Questions

- Which speech transcription provider should be used first?
- Should the prototype use OpenAI Realtime transcription, file/chunk transcription, or a simulated evidence stream?
- What confidence thresholds should apply to each event type?
- Which events must always request confirmation?
- How should overlapping speech be represented in evidence?
- How should the dashboard avoid distracting the team leader?

---

# Current Provider Capability Notes

Status:

Informational, not an accepted provider decision.

As of 2026-07-18, OpenAI documentation describes:

- Realtime audio sessions with server-side voice activity detection and semantic VAD.
- Input audio transcription configuration with optional language and prompt guidance.
- A transcription model with diarization and speaker labels.
- Streamed transcription events for diarized transcription segments.

Important limitation:

OpenAI's Audio API reference says the transcription `prompt` field is not supported when using `gpt-4o-transcribe-diarize`.

Implication:

If Pulse needs both speaker labels and ACLS vocabulary guidance, the architecture should support one of these approaches:

- use diarized transcription and rely on downstream medical event extraction for ACLS normalization
- use non-diarized prompted transcription for vocabulary-sensitive extraction
- run a hybrid or two-pass pipeline where diarization and vocabulary-biased transcription are treated as separate evidence sources

The project should not assume one model will solve VAD, diarization, speech recognition, vocabulary biasing, and event extraction perfectly in one step.

---

# Recommended Resolution for First Prototype

Do not start with live room audio.

Start with simulated evidence streams that represent:

- speech observations
- acoustic observations
- manual confirmations
- conflicting evidence
- missed evidence

Then implement the Evidence Fusion Engine and Clinical State Engine against those streams.

After that works, replace simulated extraction with real speech/audio pipelines.
