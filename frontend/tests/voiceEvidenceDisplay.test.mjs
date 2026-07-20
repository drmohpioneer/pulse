import assert from "node:assert/strict";
import test from "node:test";

import {
  evidenceSourceSummary,
  evidenceSpeakerSummary,
  fusionDecisionLabel,
  isConfirmableFusionResult,
  isNegativeEvidenceResult,
  isRejectableFusionResult,
  observationKindFromEvidence,
  observationKindLabel,
  payloadSummary,
} from "../lib/voiceEvidenceDisplay.mjs";

test("voice evidence labels distinguish command intent completed and correction", () => {
  assert.equal(observationKindLabel("command"), "Command heard");
  assert.equal(observationKindLabel("intent"), "Intent heard");
  assert.equal(observationKindLabel("completed_action"), "Completed action heard");
  assert.equal(observationKindLabel("correction"), "Correction heard");
});

test("negative evidence renders as evidence-only audit", () => {
  const result = {
    candidate_event: null,
    result_kind: "negative_evidence",
    is_negative_evidence: true,
  };

  assert.equal(isNegativeEvidenceResult(result), true);
  assert.equal(fusionDecisionLabel(result), "Evidence-only audit");
});

test("conflicting evidence asks for human clarification", () => {
  assert.equal(
    fusionDecisionLabel({
      candidate_event: { status: "needs_confirmation" },
      uncertainty_reason: "conflicting_evidence",
    }),
    "Needs human clarification",
  );
});

test("medication payload summary includes dose unit and route", () => {
  assert.equal(
    payloadSummary("medication_given", {
      medication: "epinephrine",
      dose: 1,
      unit: "mg",
      route: "IV",
    }),
    "Epinephrine 1 mg IV",
  );
});

test("evidence helpers extract observation kind and compact sources", () => {
  const evidence = [
    { source: "speech", payload: { observation_kind: "completed_action" } },
    { source: "manual", payload: {} },
  ];

  assert.equal(observationKindFromEvidence(evidence), "completed_action");
  assert.equal(evidenceSourceSummary(evidence), "Speech + Manual");
});

test("speaker and advisory role metadata render only when present", () => {
  assert.equal(evidenceSpeakerSummary({ payload: {} }), "");
  assert.equal(
    evidenceSpeakerSummary({
      payload: {
        speaker_id: "speaker_1",
        role: "team_leader",
        role_confidence: 0.77,
      },
    }),
    "Speaker 1 · Team Leader advisory 77%",
  );
  assert.equal(
    evidenceSpeakerSummary({
      payload: { speaker_id: "speaker_2" },
    }),
    "Speaker 2 · role unknown",
  );
});

test("acoustic evidence source renders compactly", () => {
  assert.equal(
    evidenceSourceSummary([
      {
        source: "acoustic",
        payload: { observation_type: "defibrillator_discharge" },
      },
    ]),
    "Acoustic Defibrillator Discharge",
  );
});

test("confirmation eligibility allows completed action candidates", () => {
  const result = {
    requires_confirmation: true,
    candidate_event: {
      status: "needs_confirmation",
      evidence: [
        {
          source: "speech",
          evidence_type: "normalized_clinical_observation",
          payload: { observation_kind: "completed_action" },
        },
      ],
    },
  };

  assert.equal(isConfirmableFusionResult(result), true);
  assert.equal(isRejectableFusionResult(result), true);
});

test("confirmation eligibility allows command candidates and blocks negative evidence", () => {
  const command = {
    requires_confirmation: true,
    candidate_event: {
      status: "needs_confirmation",
      evidence: [
        {
          source: "speech",
          evidence_type: "normalized_clinical_observation",
          payload: { observation_kind: "command" },
        },
      ],
    },
  };
  const negative = {
    result_kind: "negative_evidence",
    is_negative_evidence: true,
    candidate_event: null,
  };

  assert.equal(isConfirmableFusionResult(command), true);
  assert.equal(isRejectableFusionResult(command), true);
  assert.equal(isConfirmableFusionResult(negative), false);
  assert.equal(isRejectableFusionResult(negative), false);
});

test("confirmation eligibility allows intent candidates", () => {
  const intent = {
    requires_confirmation: true,
    candidate_event: {
      status: "needs_confirmation",
      evidence: [
        {
          source: "speech",
          evidence_type: "normalized_clinical_observation",
          payload: { observation_kind: "intent" },
        },
      ],
    },
  };

  assert.equal(isConfirmableFusionResult(intent), true);
  assert.equal(isRejectableFusionResult(intent), true);
});

test("confirmation eligibility blocks conflicts from choosing a side", () => {
  const conflict = {
    requires_confirmation: true,
    uncertainty_reason: "conflicting_evidence",
    candidate_event: {
      status: "needs_confirmation",
      evidence: [
        {
          source: "speech",
          evidence_type: "normalized_clinical_observation",
          payload: { observation_kind: "observation" },
        },
      ],
    },
  };

  assert.equal(isConfirmableFusionResult(conflict), false);
  assert.equal(isRejectableFusionResult(conflict), true);
});
