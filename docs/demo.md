# Pulse Hackathon Demo

Version: 1.1
Status: Final — the demo film is at https://youtu.be/Ou2HArr33Ag

---

# Demo Goal

Show that Pulse is not a CPR timer or chatbot.

The demo should prove the core product idea:

Pulse maintains clinical state during cardiac arrest by fusing uncertain evidence and assisting the team leader.

---

# Judge Takeaway

The judge should understand:

- CPR is cognitively overloaded.
- Existing tools record events but do not understand state.
- Pulse estimates clinical state from evidence.
- Pulse exposes uncertainty.
- Pulse supports clinicians without replacing them.

---

# Demo Narrative

## Scene

A cardiac arrest is in progress.

One recorder phone is placed near the recorder, matching existing ACLS workflow.

Pulse receives a stream of evidence:

- spoken phrases
- acoustic signals
- manual confirmations

## What Happens

1. The team starts CPR.
2. Pulse identifies CPR is running.
3. A rhythm check occurs.
4. Pulse tracks CPR pause time.
5. A shock is delivered.
6. Speech and acoustic evidence agree, raising confidence.
7. Pulse updates shock count and timeline.
8. A medication event is uncertain.
9. Pulse asks for confirmation instead of inventing certainty.
10. Pulse updates the clinical state after confirmation.
11. The copilot surfaces a concise reminder based on state.

---

# Demo Screen Requirements

The dashboard should show:

- current rhythm
- CPR status
- time since last rhythm check
- medication timeline
- shock count
- event timeline
- confidence indicators
- confirmation requests
- concise copilot guidance

The interface should be calm, dense, and clinically readable.

It should not feel like a marketing page.

---

# Suggested Demo Script

Opening:

"During cardiac arrest, the team leader is mentally tracking rhythm, shocks, medications, CPR cycles, reversible causes, communication, and documentation at the same time."

Problem:

"Most tools record what happened. They do not maintain an understanding of what is happening."

Product:

"Pulse is an AI Clinical Teammate. It converts noisy evidence into clinical events, updates deterministic clinical state, and gives the leader concise support."

Differentiation:

"The innovation is not transcription. The innovation is evidence fusion over clinical state."

Safety:

"When Pulse is uncertain, it asks for confirmation. It does not hallucinate clinical events."

Close:

"Pulse is designed to reduce cognitive load without changing the CPR workflow."

---

# Demo Non-Goals

Do not claim:

- autonomous decision making
- clinician replacement
- regulatory readiness
- perfect audio recognition
- real-world clinical validation

The demo should be ambitious but honest.

