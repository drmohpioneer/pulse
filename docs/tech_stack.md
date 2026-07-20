# Pulse Tech Stack

Version: 1.1
Status: Accepted

---

# Tech Stack Philosophy

The stack should support a real medical software path:

- clear separation of responsibilities
- deterministic clinical state handling
- strong validation
- secure secret management
- reliable testing
- local demo speed
- future scalability

---

# Current Stack Status

The core implementation stack has been officially accepted.

This document records the accepted stack. Items still marked as candidates below (audio provider, storage engine, LLM provider, deployment target) remain evaluation choices and are not part of the accepted core stack.

---

# Officially Accepted Technology Stack

## Frontend

- Next.js
- TypeScript
- Tailwind CSS

## Backend

- FastAPI
- Python
- Pydantic

## Testing

- Pytest

## Version Control

- Git
- GitHub

## Architecture Principles

- Deterministic Clinical Workflow Engine
- AI isolated from deterministic logic
- Modular backend
- Test-first development
- Security by design

---

# Backend Responsibilities

Accepted stack: Python with FastAPI and Pydantic.

Why:

- Strong fit for AI and audio pipelines.
- Good support for typed request and response models.
- Fast local development.
- Mature testing ecosystem.
- Easy integration with future ML/audio services.

Responsibilities:

- API surface
- authentication later
- validation
- clinical event intake
- Evidence Fusion Engine
- Clinical State Engine
- copilot orchestration
- audit logging later

---

# Frontend Responsibilities

Accepted stack: Next.js with TypeScript and Tailwind CSS.

Why:

- Strong ecosystem for real-time dashboards.
- Good state management and component composition.
- TypeScript improves contract clarity.
- Good hackathon velocity without giving up maintainability.

Responsibilities:

- presentation
- user confirmation controls
- timeline display
- clinical state display
- event stream visualization

Frontend must not own clinical state or business logic.

---

# Shared Contracts

Backend-owned schemas with generated or manually mirrored TypeScript types.

Initial models:

- Evidence
- ClinicalEvent
- ClinicalState
- ConfirmationRequest
- CopilotMessage
- TimelineEntry

---

# AI Direction

LLM-based copilot that consumes only structured Clinical State and relevant timeline context.

Rules:

- The LLM does not own state.
- The LLM does not infer hidden events.
- The LLM must expose uncertainty.
- The LLM output is advisory.
- Deterministic software handles timers, state, and event transitions.

---

# Audio Direction

Initial demo:

Simulated evidence stream.

Near-term:

Speech-to-text pipeline plus acoustic event classifier prototypes.

Long-term:

Multi-source evidence ingestion including medical devices where possible.

Candidate OpenAI components to evaluate:

- Realtime API for low-latency audio sessions and server-side VAD.
- Transcription API for chunked or streamed transcription.
- Diarized transcription when speaker labels are needed.

These are candidates, not accepted decisions.

Accepted model and provider choices must be recorded in `docs/decisions.md`.

Important evaluation note:

Diarization and vocabulary biasing may not be available together in a single transcription call depending on provider/model choice.

The architecture must support combining outputs from separate extraction passes as independent evidence.

---

# Storage Direction

Initial demo:

In-memory state with replayable fixtures.

Near-term:

SQLite or Postgres depending on scope.

Long-term:

Postgres with audit logs, event sourcing, and secure retention policies.

---

# Testing Direction

Accepted framework: Pytest.

Backend:

- unit tests for state transitions
- unit tests for evidence fusion
- contract tests for schemas

Frontend:

- component tests for critical displays
- interaction tests for confirmation workflows

End-to-end:

- replay a simulated cardiac arrest
- verify final clinical state
- verify expected confirmations and reminders

---

# Environment and Secrets

Rules:

- No hardcoded secrets.
- No API keys in frontend code.
- Use `.env` files locally.
- Commit only `.env.example`.
- Backend validates all external input.

---

# Remaining Open Stack Decisions

The core stack is accepted. The following remain open and should be recorded in `docs/decisions.md` when chosen:

- Package managers
- Database for prototype
- LLM provider
- Speech-to-text provider
- Deployment target
