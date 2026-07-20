# Pulse State Machine Contract

## Purpose

All clinical workflow modules must follow a common deterministic interface.

State machines receive validated clinical events and update their internal state.

They do not:
- listen to audio
- call AI models
- make UI decisions
- access external services


## Core Lifecycle

ClinicalEvent
        |
        v
State Machine
        |
        v
Updated State
        |
        v
Recommendation


## Required Methods


### apply_event(event)

Purpose:
Process a new clinical event.

Input:
ClinicalEvent

Output:
Updated internal state


Example:

Event:
"VF detected"

Rhythm State:
UNKNOWN → VF


---


### get_state()

Purpose:
Return current state.

Output:
Serializable state object


Example:

Rhythm:
VF

Shock:
Required


Note:

The method name is officially:

get_state()

and replaces the previous draft naming:

current_state()


---


### get_recommendations()

Purpose:
Return deterministic next actions based on current state.

Output:

Action[]
 

Example:

1. Charge defibrillator
2. Resume CPR
3. Give adrenaline when indicated


---


### explain()

Purpose:

Provide reasoning trace.

Example:

"VF detected.
Previous shock delivered 2 minutes ago.
Next recommended action: rhythm check."


---


### replay(events)

Purpose:

Reconstruct state from historical events.

Input:

List of ClinicalEvents

Output:

Current state


This allows correction and auditability.


---


### reset()

Purpose:

Restore the state machine to its initial state.

Used by:
- replay()
- manual reset operations
- testing

Behavior:

After reset(), the state machine must be equivalent to a newly created instance.


## Design Rules

1. State machines are deterministic.

Same events + same order = same state.

2. No AI inside state machines.

3. No UI logic inside state machines.

4. Every recommendation must have a reason.

5. Every state transition must be explainable.


## Initial State Machines

- RhythmStateMachine
- MedicationStateMachine
- ShockStateMachine
- CPRStateMachine
- AirwayStateMachine
- ReversibleCauseStateMachine
- ROSCStateMachine
- TimerStateMachine