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

