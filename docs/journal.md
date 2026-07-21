# Pulse Engineering Journal

Version: 1.0
Status: Living Document

---

# Purpose

This journal records important engineering discoveries, implementation notes, and lessons learned.

It is not a replacement for `docs/decisions.md`.

Use `docs/decisions.md` for accepted decisions.

Use this journal for context that may help future work.

---

# 2026-07-18

## Context Transfer to Codex

Pulse work moved into this repository folder so implementation can proceed with stable context.

Important inherited principles:

- Pulse is an AI Clinical Teammate for cardiac arrest.
- Pulse reasons over Clinical State, not transcripts or raw audio.
- The Clinical State Engine is deterministic.
- Evidence Fusion is the core response to unreliable perception.
- LLMs reason over state; they do not own state.
- The initial hardware platform is one recorder phone.
- Low-confidence events require confirmation.
- Documentation is the source of truth.

## Current Standing

The project is in Architecture Foundation.

The immediate task is to complete the documentation backbone, then scaffold the implementation.

## Engineering Note

The first implementation should avoid real audio complexity.

Recommended first slice:

Build a deterministic state engine and replay a simulated CPR evidence stream through the system.

This proves the central architecture before introducing noisy perception.

---

# 2026-07-21

## Build Week submission day

What actually happened, recorded because the README points here for it.

- The OpenAI transcription key ran out of quota hours before the deadline.
  That turned out to be useful pressure: instead of patching one vendor call,
  speech recognition was rewritten as a vendor table, so choosing a provider
  is now one environment variable and an unknown provider name falls through
  to any OpenAI-compatible endpoint with no code change.
- Two failures cost real time and are worth writing down. Groq sits behind
  Cloudflare, which rejects urllib's default User-Agent with a 403, and
  because `HTTPError` subclasses `URLError` every rejection was collapsing
  into one opaque message. The adapter now sets a User-Agent and surfaces the
  vendor, status code, and response body.
- Automatic language detection flapped on short clips: four seconds of
  English clinical speech came back as Polish. For a single-language demo the
  answer was to pin the language and use the larger Whisper model rather than
  the turbo one. `PULSE_ASR_LANGUAGE=auto` remains right for a code-switching
  room, and that trade-off is now documented rather than guessed at.
- The 29 scripted scenarios were moved out of a throwaway script into
  `backend/simulation/`, so the claim in the README is something a reader can
  run. They also gained a real-time mode that drives a running instance, which
  is the only way to see that a repeat shock 14 seconds later is correctly
  rejected as an echo while one at 20 seconds is not.
- Known limitation confirmed rather than fixed: the client only polls while a
  code is active, so an idle board does not notice a backend that has gone
  away until the next action.
