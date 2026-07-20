export type VoiceEvidenceDisplayItem = {
  source?: string;
  payload?: Record<string, unknown>;
};

export type VoiceFusionDisplayResult = {
  candidate_event?: {
    status?: string;
    evidence?: VoiceEvidenceDisplayItem[];
  } | null;
  requires_confirmation?: boolean;
  uncertainty_reason?: string | null;
  result_kind?: string | null;
  is_negative_evidence?: boolean;
};

export function observationKindFromEvidence(
  evidenceItems?: VoiceEvidenceDisplayItem[],
): string | null;

export function observationKindLabel(kind: string | null | undefined): string;

export function fusionDecisionLabel(
  result: VoiceFusionDisplayResult | null | undefined,
): string;

export function payloadSummary(
  eventType: string | undefined,
  payload?: Record<string, unknown>,
): string;

export function evidenceSourceSummary(
  evidenceItems?: VoiceEvidenceDisplayItem[],
): string;

export function evidenceSpeakerSummary(
  evidenceItem?: VoiceEvidenceDisplayItem | null,
): string;

export function isNegativeEvidenceResult(
  result: VoiceFusionDisplayResult | null | undefined,
): boolean;

export function isConfirmableFusionResult(
  result: VoiceFusionDisplayResult | null | undefined,
): boolean;

export function isRejectableFusionResult(
  result: VoiceFusionDisplayResult | null | undefined,
): boolean;
