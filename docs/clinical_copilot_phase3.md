# Clinical Copilot Phase 3

Status: Implemented as a deterministic safety adapter.

## Boundary

The Clinical Copilot is a human-facing explanatory layer. It does not own clinical state.

Allowed inputs:

- WorkflowCoordinator output
- accepted event timeline
- accepted/corrected ClinicalEvents
- immutable machine state snapshots

Disallowed inputs:

- raw audio
- raw transcript streams
- frontend-only state
- workflow engine instance
- state machine instances
- event processor

## Safety Rules

- The deterministic ACLS engine remains the source of clinical decisions.
- The copilot never creates or modifies ClinicalEvents.
- The copilot never sends events into the ClinicalWorkflowEngine.
- The copilot does not recommend treatment independently.
- The copilot may restate the coordinator's selected action and explain its rationale.

## Current Implementation

`StateBoundClinicalCopilot` is deterministic. It formats a concise note from the coordinator decision and includes source recommendation IDs, referenced state fields, and referenced accepted event IDs.

A future LLM adapter may replace the wording step only. It must preserve this same input/output boundary.
