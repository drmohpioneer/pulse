export function observationKindFromEvidence(evidenceItems = []) {
  for (const item of evidenceItems) {
    const kind = item?.payload?.observation_kind;
    if (typeof kind === "string" && kind.length > 0) {
      return kind;
    }
  }
  return null;
}

export function observationKindLabel(kind) {
  if (kind === "command") {
    return "Command heard";
  }
  if (kind === "intent") {
    return "Intent heard";
  }
  if (kind === "completed_action") {
    return "Completed action heard";
  }
  if (kind === "correction") {
    return "Correction heard";
  }
  if (kind === "observation") {
    return "Observation heard";
  }
  if (kind === "rhythm_identification") {
    return "Rhythm heard";
  }
  return "Voice evidence";
}

export function fusionDecisionLabel(result) {
  if (!result) {
    return "No evidence";
  }
  if (result.is_negative_evidence || result.result_kind === "negative_evidence") {
    return "Evidence-only audit";
  }
  if (result.uncertainty_reason === "conflicting_evidence") {
    return "Needs human clarification";
  }
  const status = result.candidate_event?.status;
  if (status === "accepted") {
    return "Accepted event";
  }
  if (status === "rejected") {
    return "Rejected";
  }
  if (result.requires_confirmation || status === "needs_confirmation") {
    return "Needs confirmation";
  }
  if (status === "corrected") {
    return "Corrected event";
  }
  return formatLabel(result.result_kind || status || "Candidate event");
}

export function payloadSummary(eventType, payload = {}) {
  if (eventType === "medication_given") {
    const parts = [formatLabel(payload.medication || "Medication")];
    if (payload.dose !== undefined && payload.unit) {
      parts.push(`${payload.dose} ${payload.unit}`);
    }
    if (payload.route) {
      parts.push(String(payload.route));
    }
    return parts.join(" ");
  }
  if (eventType === "rhythm_checked" && payload.rhythm) {
    return formatLabel(payload.rhythm);
  }
  if (eventType === "rosc_achieved") {
    return "ROSC";
  }
  return formatLabel(eventType || "Clinical event");
}

export function evidenceSourceSummary(evidenceItems = []) {
  const sources = new Set();
  for (const item of evidenceItems) {
    if (item?.source) {
      sources.add(evidenceSourceLabel(item));
    }
  }
  return sources.size ? Array.from(sources).join(" + ") : "Evidence";
}

export function evidenceSpeakerSummary(evidenceItem) {
  const payload = evidenceItem?.payload || {};
  const speaker = typeof payload.speaker_id === "string" ? payload.speaker_id : "";
  const role = typeof payload.role === "string" ? payload.role : "";
  const roleConfidence = typeof payload.role_confidence === "number"
    ? ` ${Math.round(payload.role_confidence * 100)}%`
    : "";
  if (role && role !== "unknown" && speaker && speaker !== "speaker_unknown") {
    return `${formatLabel(speaker)} · ${formatLabel(role)} advisory${roleConfidence}`;
  }
  if (speaker && speaker !== "speaker_unknown") {
    return `${formatLabel(speaker)} · role unknown`;
  }
  if (role && role !== "unknown") {
    return `${formatLabel(role)} advisory${roleConfidence}`;
  }
  return "";
}

export function isNegativeEvidenceResult(result) {
  return Boolean(result?.is_negative_evidence || result?.result_kind === "negative_evidence");
}

export function isConfirmableFusionResult(result) {
  if (!result?.candidate_event || isNegativeEvidenceResult(result)) {
    return false;
  }
  if (!result.requires_confirmation || result.uncertainty_reason === "conflicting_evidence") {
    return false;
  }
  const status = result.candidate_event.status;
  if (status !== "needs_confirmation") {
    return false;
  }
  return (result.candidate_event.evidence || []).some((item) =>
    ["command", "intent", "completed_action", "observation", "rhythm_identification"].includes(
      item?.payload?.observation_kind,
    ) || item?.evidence_type === "manual_confirmation",
  );
}

export function isRejectableFusionResult(result) {
  return Boolean(
    result?.candidate_event
      && result.requires_confirmation
      && !isNegativeEvidenceResult(result)
      && result.candidate_event.status === "needs_confirmation",
  );
}

function formatLabel(value) {
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function evidenceSourceLabel(item) {
  const source = formatLabel(item.source);
  const observationType = item?.payload?.observation_type;
  if (item.source === "acoustic" && observationType) {
    return `${source} ${formatLabel(observationType)}`;
  }
  return source;
}
