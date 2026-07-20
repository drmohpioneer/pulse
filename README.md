# Pulse

**An AI clinical teammate for cardiac arrest.**

Pulse listens to a resuscitation room through a single phone, works out what is
actually happening, and keeps the team's shared picture of the arrest correct
and current.

> ⚠️ **Research prototype. Not a medical device. Not for clinical use, and not
> for use on real patients.** Pulse is unvalidated software built for the OpenAI
> Build Week challenge.

---

## The problem

During a cardiac arrest, the team leader is running an algorithm in their head
while the room is loud, crowded, and moving. They have to track the rhythm, the
CPR cycle, how many shocks have been given, when the last dose of epinephrine
went in, whether it is time to reassess, and which reversible causes have been
ruled out. All of it at once, all of it from memory, all of it while making
decisions that decide whether the patient lives.

The information that would answer every one of those questions is already being
said out loud in the room. Nobody is holding on to it.

Existing tools do not solve this. A CPR timer counts seconds and knows nothing
about the patient. An AI scribe writes a document for afterwards. A chatbot
waits to be asked a question by someone whose hands are busy.

Pulse is none of those. It is a teammate that listens continuously, maintains
the state of the arrest, and surfaces the next action without being asked.

---

## The core design rule

**The AI is never allowed to change clinical state.**

Everything Pulse hears becomes *evidence*, never fact. Evidence is fused into a
*candidate event*, and only a deterministic state machine, which contains no AI
at all, ever advances the clinical state.

Acceptance follows **ACLS closed-loop communication**, the way real resus teams
already confirm each other. When a team member *states a completion out loud* —
"shock delivered", "epi is in", "rhythm is VF" — that spoken statement is
itself the room's confirmation, so Pulse accepts it directly, shows it
prominently, and keeps a 30-second **Undo** on it. An *order* ("give 1 mg
epinephrine") is only advisory: it waits as a pending card until the matching
completion statement closes the loop, or a human confirms, rejects, or
corrects it by hand.

```
audio ──► transcript ──► normalized observation ──► evidence
                                                      │
                                            fusion + confidence
                                                      │
                                             candidate event
                                                      │
                        completion stated aloud ──► accepted (30s undo)
                        order / low confidence  ──► pending human action
                                                      │
                                    deterministic clinical engine ──► guidance
```

Additional guards: identical events spoken seconds apart are deduplicated
(echoes cannot inflate a shock count), off-protocol events such as a shock
recorded during asystole raise an explicit deviation advisory, and every
acceptance, supersession, and undo is written to the audit log.

---

## Quick start

**No API keys and no credentials are required.** Pulse ships with deterministic
offline speech recognition and runs end to end out of the box. This is the
recommended way to evaluate it.

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), and Node 18+.

**Terminal 1, backend:**

```bash
cd pulse
uv run --project backend uvicorn backend.api.main:app --port 8000
```

**Terminal 2, frontend:**

```bash
cd pulse/frontend
npm install
npm run dev
```

Open **http://localhost:3000**.

Verify the backend on its own with:

```bash
curl http://127.0.0.1:8000/api/demo
```

---

## Walk through it in two minutes

1. **Feed the room a phrase.** In the transcript panel, enter something a real
   team says out loud, in English or Egyptian Arabic:
   - `rhythm is vf`
   - `الريذم vf`
   - `give 1 mg epinephrine`
   - `ادي ادرينالين`
   - `shock delivered`
   - `we have a pulse`
   - `النبض رجع`

   The Arabic entries are code-switched on purpose. Egyptian clinicians say
   `الريذم vf`, not a formal Arabic translation of the rhythm name, and Pulse
   normalizes the language as it is actually spoken in the room.
2. **Watch the closed loop.** A completion statement ("shock delivered") is
   accepted directly — the deterministic engine advances and an Undo stays
   available for 30 seconds. An order ("give 1 mg epinephrine") becomes a
   pending card instead, resolved when its completion is spoken ("epi is in"),
   or by tapping Confirm / Reject / Correct.
3. **Try the timers.** Say `cpr started` and wait: the 2-minute rhythm-check
   prompt surfaces on its own, epinephrine timing is tracked per ACLS, and
   after a shock the hands-off counter climbs and escalates until compressions
   resume.
4. **Read the guidance.** The dashboard shows the primary action, secondary
   actions, and the clinical rationale, all derived from state by deterministic
   code rather than generated prose.
5. **Try to break it.** Enter `no shock` or `مفيش نبض`. Negation is handled as
   negative evidence: it is auditable, and it will not create a positive
   candidate event out of a denial.

Every accepted and corrected event is written to a local audit log, and the
timeline can be replayed from it.

---

## Live microphone mode

The **Live Audio** panel captures from the microphone in the browser, uploads
short self-contained audio segments to the backend, transcribes them, and pushes
the result into the same evidence pipeline described above. The confirmation
gate applies identically. Nothing reaches clinical state without a human.

The browser will ask for microphone permission, which requires `localhost` or
HTTPS. Chrome and Safari are both supported.

With the default configuration this runs against fake ASR, so you can exercise
the whole capture, upload, and storage path with no credentials.

### Enabling real speech recognition

```bash
cp backend/.env.example backend/.env
```

Then set:

```
PULSE_ASR_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Restart the backend. The Live Audio panel reports which provider is active.
If the key is missing or the provider fails, Pulse **fails closed**: it falls
back to fake ASR, reports the error in the UI, and never mutates clinical state
on a failed transcription.

See `backend/.env.example` for audio storage, retention, and timeout options.

---

## Tests

```bash
uv run --project backend pytest        # backend
cd frontend && npm test                # frontend
cd frontend && npm run build           # typecheck + build
```

A manual verification checklist lives in [`docs/SMOKE_TEST.md`](docs/SMOKE_TEST.md).

---

## How this was built

Pulse was built with **Codex, across GPT-5.5 and GPT-5.6 sessions**, working
against a written project constitution (`AGENT.md`) that fixes the safety
boundaries the agent is not allowed to cross: the AI may not own clinical
state, spoken evidence is gated before it can act, and every accepted event
must be auditable.

The architecture, the state machines, the evidence and fusion layer, the
multilingual normalization, the confirmation policy, and the audio pipeline were
implemented across Codex sessions, each one scoped to a single vertical slice
with tests. `docs/decisions.md` records the engineering decisions and
`docs/journal.md` records how the build actually went.

Before release, the whole pipeline was exercised by an external simulation
harness: **29 scripted resuscitation scenarios** (classic VF arrests, asystole
and PEA arrests, intertangled VF→asystole→VF codes, ROSC and re-arrest, echo
duplicates, room-chatter false-positive probes, order-then-completion loops,
zero-touch hands-free runs) driven through the same code path the live
microphone uses, plus replays of real recorded clinician speech through the
real ASR provider. Bugs that sweep surfaced — a phrase matcher that missed
natural speech, silence segments hallucinating transcripts, echo duplicates
inflating shock counts, amiodarone second-dose timing — were fixed by Codex
and locked with regression tests.

---

## Repository layout

```
backend/     FastAPI. Clinical state, evidence fusion, confirmation, audio, audit.
  workflow/    Deterministic ACLS state machines. No AI here by design.
  audio/       ASR, diarization, acoustic and multimodal perception contracts.
  services/    Evidence fusion, confirmation, voice pipeline, audit store.
  ai/          Copilot boundary. State-bound, cannot mutate clinical state.
frontend/    Next.js dashboard, transcript entry, live audio, confirmation UI.
docs/        Architecture, decisions, roadmap, vision, project state.
AGENT.md     Project constitution. The rules the build is not allowed to break.
```

---

## Known limitations

Stated plainly, because a clinical tool that hides its limits is worse than no
tool at all.

- **Speech understanding is deterministic, not general.** Arabic and English
  phrase recognition covers defined families of clinical speech. It is not
  broad natural language understanding, and unusual phrasing will be missed.
- **Audio is transcribed in ~4-second segments.** A phrase that straddles a
  segment boundary can be split and missed. The fix is streaming ASR (one
  continuous connection, no boundaries), which is the top roadmap item.
- **Reversible causes (H's and T's) cannot yet be fed by voice.** The engine
  models them, but no spoken vocabulary reaches that state machine yet.
- **Speaker diarization and acoustic event detection are contracts with
  deterministic stand-in providers.** Real providers are not activated.
- **Session state is in memory and is not patient-isolated or multi-user safe.**
- **The audit log is local JSONL.** It is not a clinical record and carries no
  compliance guarantees.
- **Audio storage is short-lived local files** for the transcription provider to
  read, with time-based cleanup. It is not production retention infrastructure.
- **There is no authentication, encryption policy, consent flow, or PHI
  handling.** Do not put real patient data into this.

The architecture separates providers behind interfaces specifically so these can
be replaced without touching the clinical engine. `docs/roadmap.md` covers what
comes next.

---

## License

MIT. See [LICENSE](LICENSE).
