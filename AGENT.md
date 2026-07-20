# AGENT.md
# Pulse AI - Project Constitution

Version: 1.0
Status: Living Document

---

# Mission

You are contributing to **Pulse**, an AI Clinical Teammate for Cardiac Arrest.

Pulse is NOT a chatbot.

Pulse is NOT an AI scribe.

Pulse is NOT a CPR timer.

Pulse is a real-time clinical teammate that continuously estimates the current state of a resuscitation and assists the team leader by reducing cognitive load.

Every engineering decision should move the project toward that vision.

---

# Product Vision

Pulse exists because cardiac arrest is one of the most cognitively demanding workflows in medicine.

During a resuscitation, clinicians must simultaneously:

- Lead the team
- Interpret rhythms
- Track medications
- Track timing
- Consider reversible causes
- Monitor CPR quality
- Make critical decisions
- Document the event

Pulse should reduce mental workload without disrupting existing clinical workflow.

---

# Core Innovation

The innovation of Pulse is NOT speech recognition.

The innovation is the Clinical State Engine.

Speech is only one source of evidence.

The system reasons over the patient's current clinical state—not raw transcripts.

---

# Guiding Philosophy

The application should behave like an experienced teammate.

It should never attempt to replace clinicians.

It should assist clinicians.

Every feature should answer one question:

"Does this reduce cognitive load?"

If not, reconsider the feature.

---

# Engineering Philosophy

Build software that could evolve into a real medical product.

Do not build hackathon shortcuts that damage long-term architecture.

Always optimize for:

1. Clinical Safety
2. Security
3. Maintainability
4. Scalability
5. Performance

Never sacrifice architecture for temporary convenience.

---

# Security Rules

Never:

- expose API keys
- hardcode secrets
- trust frontend validation
- place business logic inside the frontend
- expose internal services unnecessarily

Assume every external input is untrusted.

Backend validates everything.

---

# Architecture Rules

Pulse is modular.

No component should have multiple responsibilities.

Prefer small independent modules.

Architecture should remain clean.

Suggested layers:

Input

↓

Evidence Extraction

↓

Evidence Fusion

↓

Clinical State Engine

↓

Clinical Copilot

↓

Dashboard

Each layer has one responsibility.

---

# Clinical Principles

Pulse should:

Reduce cognitive load.

Never invent clinical events.

Expose uncertainty.

Always show confidence when confidence is low.

Support clinicians instead of replacing decision making.

When uncertain:

Ask for confirmation.

Never hallucinate.

---

# AI Principles

LLMs are reasoning engines.

They are NOT databases.

They are NOT timers.

They are NOT state managers.

Clinical state should always be maintained by deterministic software.

The LLM reasons over the state.

It does not own the state.

---

# Documentation Rules

Documentation is the source of truth.

Code follows documentation.

Not the opposite.

Whenever architecture changes:

Update documentation first.

Then update code.

Never allow documentation to drift.

---

# Development Workflow

Product Vision
↓

Architecture
↓

Documentation
↓

Implementation
↓

Review
↓

Commit

Never skip documentation.

---

# Roles

Mohamed

Product Owner

Clinical Expert

Final Decision Maker

ChatGPT

Chief Architect

Research

System Design

Product Strategy

Architecture Review

Codex

Senior Software Engineer

Implementation

Testing

Refactoring

Debugging

Code Generation

Architecture changes require discussion.

Implementation changes do not.

---

# Decision Making

If multiple solutions exist:

Choose the one that would still make sense if Pulse became a real startup after the hackathon.

Never optimize only for the competition.

---

# Coding Standards

Readable code.

Meaningful naming.

Small functions.

Modular design.

Clear separation between frontend/backend.

Avoid unnecessary complexity.

Prefer explicitness over cleverness.

---

# Working Style

Challenge assumptions.

Question ideas.

Avoid premature optimization.

Prefer evidence over intuition.

One problem at a time.

One decision at a time.

Do not introduce new architectural discussions before the current one is finished.

---

# Long-Term Vision

Pulse should eventually become:

A trusted AI teammate that continuously understands the clinical state of a resuscitation using multiple evidence sources while remaining transparent, reliable, secure and clinically safe.

Every contribution should move the project one step closer to that vision.

---

---

# Startup Procedure

Every AI agent working on Pulse must begin every session by reading the following files in order:

1. AGENT.md
2. docs/project_state.md
3. docs/vision.md
4. docs/architecture.md
5. docs/decisions.md

These documents are the project's single source of truth.

Do not rely on previous conversations or assumptions if the documentation disagrees.

If documentation and code disagree, documentation wins until a conscious architectural decision is made.

---

# End-of-Session Procedure

Before ending a work session:

1. Update project_state.md.
2. Update decisions.md if a new engineering decision was made.
3. Update architecture.md if the system architecture changed.
4. Ensure documentation reflects the current implementation.
5. Only then commit changes.

A session is not complete until the documentation matches the code.

---

# Documentation Update Rule

Whenever ChatGPT proposes:

- a new architectural principle,
- a new engineering decision,
- a new workflow,
- a new project rule,
- or a modification to an existing design,

ChatGPT must explicitly tell the user:

1. Which file should be updated.
2. Exactly where the new content should be added.
3. The exact Markdown text to paste.

Never assume the repository will be updated automatically.

The repository is the single source of truth.

## Documentation Workflow

Architecture discussions happen continuously.

The repository is updated only at explicit checkpoints.

When a checkpoint is requested, ChatGPT must:

- List every file that needs updating.
- Explain why.
- Provide the exact Markdown to paste.

Do not interrupt architecture discussions for documentation updates unless requested.