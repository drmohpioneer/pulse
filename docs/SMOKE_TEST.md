# Pulse Smoke Test

A manual pass that proves the whole system works end to end. Takes about five
minutes. No credentials required for steps 1 through 6.

Every command below has been run against a clean checkout. Expected output is
stated so you can tell a pass from a failure.

---

## 0. Start the servers

```bash
# terminal 1
uv run --project backend uvicorn backend.api.main:app --port 8000

# terminal 2
cd frontend && npm install && npm run dev
```

---

## 1. Backend responds

```bash
curl http://127.0.0.1:8000/api/demo
```

**Pass:** JSON with `"current_rhythm": "Unknown"` and
`"primary_action": "Assess rhythm."` The engine starts with no assumptions.

---

## 2. Frontend loads

Open http://localhost:3000.

**Pass:** the dashboard renders and shows the same starting state, meaning the
frontend is reading real backend state rather than local mock data.

---

## 3. Speech becomes a candidate, not a fact

```bash
curl -s -X POST http://127.0.0.1:8000/api/demo/transcripts \
  -H 'Content-Type: application/json' \
  -d '{"text":"rhythm is vf"}'
```

**Pass:** the response contains a `fusion_results` entry with
`"event_type": "rhythm_checked"`, `"status": "needs_confirmation"`, and a
`confirmation_requests` entry with
`"reason": "high_impact_requires_corroboration"`.

**Critically:** `state.current_rhythm` is still `Unknown` and
`accepted_event_ids` is empty. Pulse heard it and did not act on it. This is
the central safety property.

---

## 4. Human confirmation advances the engine

Take the `candidate_event_id` from step 3, then:

```bash
curl -s -X POST http://127.0.0.1:8000/api/demo/confirmations/confirm \
  -H 'Content-Type: application/json' \
  -d '{"candidate_event_id":"<ID FROM STEP 3>","resolved_by":"team_leader"}'
```

**Pass:** `current_rhythm` becomes `VF`, `current_pathway` becomes `Shockable`,
and `primary_action` becomes `Deliver shock.` The deterministic engine, not the
AI, produced that guidance.

---

## 5. Rejection and correction

- Send another transcript, then POST the candidate to
  `/api/demo/confirmations/reject`. **Pass:** state does not change.
- Send another, then POST to `/api/demo/confirmations/correct` with an explicit
  corrected event type and payload. **Pass:** the corrected event is applied and
  the correction is recorded in the event's history.

---

## 6. Negation does not create a positive event

```bash
curl -s -X POST http://127.0.0.1:8000/api/demo/transcripts \
  -H 'Content-Type: application/json' \
  -d '{"text":"no shock"}'
```

**Pass:** evidence is recorded with `"is_positive": false` and **no** positive
candidate event is created. A denial can never be turned into a shock.

---

## 7. Live microphone, fake ASR

In the browser, open the Live Audio panel and start listening. Grant microphone
permission when prompted.

**Pass:**
- the provider label reads `Fake/demo ASR`
- audio segments upload without errors
- transcript chunks appear and flow into the same confirmation pipeline
- clinical state still only moves on confirmation

This exercises the full capture, upload, storage, and handoff path with no
credentials.

---

## 8. Live microphone, real ASR

Requires a funded OpenAI API key.

```bash
cp backend/.env.example backend/.env
# set PULSE_ASR_PROVIDER=openai and OPENAI_API_KEY=sk-...
```

Restart the backend and start listening again.

**Pass:**
- the provider label reads `Configured ASR: Openai`
- speaking `rhythm is vf` into the microphone produces a real transcript and a
  candidate event awaiting confirmation
- **speech after the first segment keeps transcribing**, which confirms each
  uploaded segment is a complete standalone audio file
- speaking `الريذم vf` also produces a candidate, confirming the Arabic path

**Fail-closed check:** set a deliberately invalid `OPENAI_API_KEY` and restart.
Pulse must report the provider error in the UI, fall back to fake ASR, and leave
clinical state untouched. It must not crash and must not guess.

---

## 9. Automated tests

```bash
uv run --project backend pytest
cd frontend && npm test
cd frontend && npm run build
```

**Pass:** all backend tests pass, all frontend tests pass, and the build
completes with no type errors.
