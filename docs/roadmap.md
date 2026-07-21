# Pulse Roadmap

Version: 1.1
Status: Current as of the Build Week submission

---

# Roadmap Philosophy

Pulse should be built as if it may become a real clinical product.

The hackathon demo should prove the core idea without damaging the long-term architecture.

The product roadmap is organized around reducing uncertainty in the riskiest parts of the system first.

---

# Current Phase

Architecture Foundation

Goal:

Complete the documentation backbone and agree on the first implementation slice.

Success criteria:

- Project constitution exists.
- Vision is documented.
- Architecture is documented.
- Engineering decisions are documented.
- Current project state is clear.
- Roadmap, tech stack, journal, demo plan, and prompts exist.

---

# Milestone 1: Documentation Backbone

Status:

In Progress

Deliverables:

- `AGENT.md`
- `docs/vision.md`
- `docs/architecture.md`
- `docs/decisions.md`
- `docs/project_state.md`
- `docs/roadmap.md`
- `docs/tech_stack.md`
- `docs/journal.md`
- `docs/demo.md`
- `docs/prompts.md`

---

# Milestone 2: Minimal Product Skeleton

Goal:

Create a maintainable full-stack skeleton that clearly separates frontend, backend, AI, and clinical state responsibilities.

Deliverables:

- Backend application scaffold
- Frontend application scaffold
- Shared event and state contracts
- Local development instructions
- Basic test setup
- Environment variable template

Non-goals:

- No real clinical advice yet.
- No production deployment yet.
- No unsafe shortcut where the frontend owns clinical state.

---

# Milestone 3: Clinical State Prototype

Goal:

Build the deterministic Clinical State Engine before relying on LLM behavior.

Deliverables:

- Clinical event schema
- Clinical state schema
- State transition engine
- Timeline model
- Confidence handling model
- Unit tests for state transitions

Recommended input:

- simulated evidence streams
- simulated fused clinical events
- manual confirmation fixtures

Example events:

- CPR started
- CPR paused
- Rhythm checked
- Shock delivered
- Medication given
- Airway secured
- Reversible cause considered

---

# Milestone 4: Evidence Fusion Prototype

Goal:

Convert uncertain observations into clinical events with confidence and confirmation requirements.

Deliverables:

- Evidence schema
- Fusion rules
- Confidence scoring policy
- Confirmation queue
- Tests for conflicting and low-confidence evidence

Initial evidence sources:

- Manual event entry
- Simulated speech extraction
- Simulated acoustic extraction

Future evidence sources:

- Real speech-to-text
- Acoustic event recognition
- Defibrillator integration
- Monitor integration

---

# Milestone 5: Hackathon Demo

Goal:

Demonstrate the core innovation: Pulse reasons over Clinical State using evidence fusion.

Demo should show:

- A realistic resuscitation timeline
- Incoming evidence with confidence
- Fused clinical events
- Deterministic state updates
- Low-confidence confirmation
- Copilot output based only on clinical state
- A dashboard that reduces cognitive load

---

# Post-Hackathon Roadmap

Areas to explore after the demo:

- Real-time audio ingestion
- Speech model evaluation in noisy clinical environments
- Acoustic event detection
- Clinical simulation dataset
- Prospective usability testing
- Security hardening
- Audit logs
- Deployment architecture
- Regulatory strategy

---

# Next Immediate Step

Agree on the first implementation slice:

Build the deterministic Clinical State Engine and a simple local dashboard that can replay simulated CPR events.

See `docs/system_skeleton.md` and `docs/obstacle_1_perception.md`.
