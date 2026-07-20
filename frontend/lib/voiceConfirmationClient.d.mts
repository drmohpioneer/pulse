export type VoiceConfirmationAction = "confirm" | "reject";

export type VoiceCorrection = {
  event_type: string;
  payload: Record<string, unknown>;
};

export type ResolveVoiceConfirmationOptions = {
  apiBase: string;
  action: VoiceConfirmationAction;
  candidateEventId?: string;
  confirmationRequestId?: string;
  fetchFn?: typeof fetch;
};

export function resolveVoiceConfirmation(
  options: ResolveVoiceConfirmationOptions,
): Promise<unknown>;

export function resolveVoiceCorrection(options: {
  apiBase: string;
  candidateEventId?: string;
  confirmationRequestId?: string;
  correction: VoiceCorrection;
  fetchFn?: typeof fetch;
}): Promise<unknown>;

export function buildCorrectionRequest(options: {
  candidateEventId?: string;
  confirmationRequestId?: string;
  correction: VoiceCorrection;
}): {
  candidate_event_id?: string;
  confirmation_request_id?: string;
  resolved_by: string;
  event_type: string;
  payload: Record<string, unknown>;
  observation_kind: "completed_action";
};
