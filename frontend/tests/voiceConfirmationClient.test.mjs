import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCorrectionRequest,
  resolveVoiceConfirmation,
  resolveVoiceCorrection,
} from "../lib/voiceConfirmationClient.mjs";

test("confirm action calls expected endpoint and returns response JSON", async () => {
  const calls = [];
  const result = await resolveVoiceConfirmation({
    apiBase: "http://pulse.local",
    action: "confirm",
    candidateEventId: "candidate-1",
    confirmationRequestId: "request-1",
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        async json() {
          return { accepted_event_ids: ["candidate-1"] };
        },
      };
    },
  });

  assert.deepEqual(result, { accepted_event_ids: ["candidate-1"] });
  assert.equal(calls[0].url, "http://pulse.local/api/demo/confirmations/confirm");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    candidate_event_id: "candidate-1",
    confirmation_request_id: "request-1",
    resolved_by: "demo-clinician",
  });
});

test("reject action calls expected endpoint", async () => {
  const calls = [];
  await resolveVoiceConfirmation({
    apiBase: "http://pulse.local",
    action: "reject",
    candidateEventId: "candidate-2",
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        async json() {
          return { accepted_event_ids: [] };
        },
      };
    },
  });

  assert.equal(calls[0].url, "http://pulse.local/api/demo/confirmations/reject");
});

test("unsupported action fails before calling fetch", async () => {
  await assert.rejects(
    () =>
      resolveVoiceConfirmation({
        apiBase: "http://pulse.local",
        action: "correct",
        candidateEventId: "candidate-3",
        fetchFn: async () => {
          throw new Error("fetch should not run");
        },
      }),
    /Unsupported confirmation action/,
  );
});

test("correction request requires explicit event type", () => {
  assert.throws(
    () =>
      buildCorrectionRequest({
        confirmationRequestId: "request-1",
        correction: { payload: {} },
      }),
    /event type is required/,
  );
});

test("correction request requires rhythm payload for rhythm event", () => {
  assert.throws(
    () =>
      buildCorrectionRequest({
        confirmationRequestId: "request-1",
        correction: { event_type: "rhythm_checked", payload: {} },
      }),
    /requires a rhythm/,
  );
});

test("correction action calls endpoint with explicit completed action semantics", async () => {
  const calls = [];
  await resolveVoiceCorrection({
    apiBase: "http://pulse.local",
    confirmationRequestId: "request-1",
    correction: {
      event_type: "rhythm_checked",
      payload: { rhythm: "pea" },
    },
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        async json() {
          return { accepted_event_ids: ["corrected-1"] };
        },
      };
    },
  });

  assert.equal(calls[0].url, "http://pulse.local/api/demo/confirmations/correct");
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    confirmation_request_id: "request-1",
    resolved_by: "demo-clinician",
    event_type: "rhythm_checked",
    payload: { rhythm: "pea" },
    observation_kind: "completed_action",
  });
});
