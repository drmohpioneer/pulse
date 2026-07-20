# Backend

Purpose:

Own server-side validation, evidence fusion, clinical state, copilot orchestration, persistence, audit behavior, and secrets.

The backend is the authority for business logic and clinical state.

The frontend must not duplicate or own these responsibilities.

Initial expected modules:

- API routes
- schemas and contracts
- Evidence Fusion Engine
- Clinical State Engine
- copilot service
- test suite

