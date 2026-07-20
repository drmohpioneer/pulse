export async function resolveVoiceConfirmation({
  apiBase,
  action,
  candidateEventId,
  confirmationRequestId,
  fetchFn = fetch,
}) {
  if (!["confirm", "reject"].includes(action)) {
    throw new Error("Unsupported confirmation action.");
  }
  const response = await fetchFn(`${apiBase}/api/demo/confirmations/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      candidate_event_id: candidateEventId,
      confirmation_request_id: confirmationRequestId,
      resolved_by: "demo-clinician",
    }),
  });
  if (!response.ok) {
    throw new Error(`Unable to ${action} voice candidate.`);
  }
  return response.json();
}

export async function resolveVoiceCorrection({
  apiBase,
  candidateEventId,
  confirmationRequestId,
  correction,
  fetchFn = fetch,
}) {
  const payload = buildCorrectionRequest({
    candidateEventId,
    confirmationRequestId,
    correction,
  });
  const response = await fetchFn(`${apiBase}/api/demo/confirmations/correct`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error("Unable to correct voice candidate.");
  }
  return response.json();
}

export function buildCorrectionRequest({
  candidateEventId,
  confirmationRequestId,
  correction,
}) {
  if (!correction?.event_type) {
    throw new Error("Correction event type is required.");
  }
  const payload = correction.payload || {};
  if (correction.event_type === "rhythm_checked" && !payload.rhythm) {
    throw new Error("Rhythm correction requires a rhythm.");
  }
  if (correction.event_type === "medication_given" && !payload.medication) {
    throw new Error("Medication correction requires a medication.");
  }
  return {
    candidate_event_id: candidateEventId,
    confirmation_request_id: confirmationRequestId,
    resolved_by: "demo-clinician",
    event_type: correction.event_type,
    payload,
    observation_kind: "completed_action",
  };
}
