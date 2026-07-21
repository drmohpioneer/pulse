"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  type CprTimerSnapshot,
  cprTimerSnapshot,
} from "../lib/cprTimer.mjs";
import {
  advanceScriptedLiveVoice,
  startLiveVoiceSession,
  stopLiveVoiceSession,
} from "../lib/liveVoiceClient.mjs";
import {
  createMicrophoneCaptureController,
  providerStatusLabel,
  startLiveAudioSession,
  stopLiveAudioSession,
  uploadLiveAudioChunk,
} from "../lib/liveAudioClient.mjs";
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
import {
  resolveVoiceConfirmation,
  resolveVoiceCorrection,
} from "../lib/voiceConfirmationClient.mjs";

type EvidenceSummary = {
  id: string;
  source: string;
  evidence_type: string;
  confidence: number;
  raw_reference: string | null;
  payload?: Record<string, unknown>;
};

type DemoAction =
  | "cpr_started"
  | "cpr_paused"
  | "cpr_resumed"
  | "vf"
  | "pvt"
  | "asystole"
  | "pea"
  | "shock_delivered"
  | "epinephrine_given"
  | "amiodarone_given"
  | "rosc";

type TimelineEntry = {
  id: string;
  timestamp: string;
  event_type: string;
  label: string;
  payload?: Record<string, string>;
};

type FusionResult = {
  candidate_event: {
    id: string;
    event_type: string;
    confidence: number;
    status: string;
    source?: string;
    evidence?: EvidenceSummary[];
    payload: Record<string, unknown>;
  } | null;
  requires_confirmation: boolean;
  evidence_ids: string[];
  uncertainty_reason: string | null;
  result_kind: string;
  is_negative_evidence: boolean;
  correction_target_event_type: string | null;
};

type ConfirmationRequest = {
  id: string;
  candidate_event_id: string;
  reason: string;
  confidence: number;
};

type CopilotResponse = {
  message: string;
  priority: "low" | "medium" | "high";
  reason: string;
  referenced_state_fields: string[];
  referenced_event_ids: string[];
  source_recommendation_ids: string[];
  requires_confirmation: boolean;
};

type ScenarioTimelineEntry = {
  transcript: string;
  evidence: EvidenceSummary[];
  evidence_ids: string[];
  result_kind: string | null;
  confidence: number | null;
  fusion_decision: string;
  accepted_event: {
    id: string;
    event_type: string;
    confidence: number;
    status: string;
    payload: Record<string, string>;
  } | null;
  engine_state: {
    rhythm: string;
    pathway: string;
    shock_count: number;
    medication_history: string[];
    rosc_status: string;
  };
  recommendation: string;
  secondary_recommendations: string[];
  rationale: string;
};

type DemoState = {
  current_workflow_phase: string;
  current_rhythm: string;
  current_pathway: string;
  cpr_status: string;
  cpr_cycle_number: number;
  cpr_cycle_elapsed_seconds: number | null;
  cpr_hands_off_elapsed_seconds: number | null;
  shock_count: number;
  medication_history: string[];
  rosc_status: string;
  top_reversible_causes: string[];
  primary_action: string;
  secondary_actions: string[];
  clinical_rationale: string;
  safety_flags: string[];
  undoable_event_ids: string[];
  timeline: TimelineEntry[];
};

type TranscriptResponse = {
  state: DemoState;
  fusion_results: FusionResult[];
  confirmation_requests: ConfirmationRequest[];
  accepted_event_ids: string[];
  undoable_event_ids: string[];
  evidence: EvidenceSummary[];
};

type LiveVoiceSessionSummary = {
  session_id: string;
  active: boolean;
  script_name: string;
  started_at: string;
  stopped_at: string | null;
  next_sequence: number;
  chunk_count: number;
};

type LiveTranscriptChunk = {
  session_id: string;
  sequence: number;
  text: string;
  confidence: number;
  timestamp: string;
  speaker_label: string | null;
  language: string;
};

type LiveVoiceSessionResponse = {
  session: LiveVoiceSessionSummary;
  chunks: LiveTranscriptChunk[];
  state: DemoState;
};

type LiveScriptedStreamResponse = {
  session: LiveVoiceSessionSummary;
  chunk: LiveTranscriptChunk | null;
  result: TranscriptResponse | null;
  is_complete: boolean;
};

type LiveAudioSessionSummary = {
  session_id: string;
  active: boolean;
  provider_name: string;
  provider_mode: string;
  provider_available: boolean;
  fallback_provider_name: string | null;
  provider_error: string | null;
  started_at: string;
  stopped_at: string | null;
  next_sequence: number;
  chunk_count: number;
};

type LiveAudioChunk = {
  session_id: string;
  sequence: number;
  audio_reference: string;
  content_type: string | null;
  duration_ms: number | null;
  timestamp: string;
  sample_rate_hz: number | null;
  channel_count: number | null;
  metadata: Record<string, unknown>;
};

type TranscriptChunkResult = {
  session_id: string;
  sequence: number;
  text: string;
  confidence: number;
  started_at: string;
  ended_at: string;
  language: string;
  speaker_label: string | null;
  provider_name: string;
  audio_reference: string;
  provider_metadata: Record<string, unknown>;
};

type LiveAudioSessionResponse = {
  session: LiveAudioSessionSummary;
  chunks: LiveAudioChunk[];
  state: DemoState;
};

type LiveAudioChunkResponse = {
  session: LiveAudioSessionSummary;
  audio_chunk: LiveAudioChunk;
  transcript: TranscriptChunkResult | null;
  result: TranscriptResponse | null;
  transcription_error: string | null;
};

type MicrophoneController = {
  readonly active: boolean;
  start: () => Promise<void>;
  stop: () => void;
};

type CapturedMicrophoneChunk = {
  sequence: number;
  blob: Blob;
  timestamp: string;
  contentType: string;
};

type CorrectionFormValue = {
  event_type: string;
  payload: Record<string, unknown>;
};

type ScenarioResponse = {
  title: string;
  state: DemoState;
  timeline: ScenarioTimelineEntry[];
};

const API_BASE = process.env.NEXT_PUBLIC_PULSE_API_BASE ?? "http://127.0.0.1:8000";

const demoActions: Array<{ action: DemoAction; label: string }> = [
  { action: "cpr_started", label: "CPR" },
  { action: "vf", label: "VF" },
  { action: "pvt", label: "pVT" },
  { action: "shock_delivered", label: "Shock" },
  { action: "cpr_resumed", label: "Resume" },
  { action: "epinephrine_given", label: "Epi" },
  { action: "amiodarone_given", label: "Amio" },
  { action: "asystole", label: "Asystole" },
  { action: "pea", label: "PEA" },
  { action: "rosc", label: "ROSC" },
  { action: "cpr_paused", label: "Pause" },
];

export default function Page() {
  const [state, setState] = useState<DemoState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [transcriptText, setTranscriptText] = useState("VF detected");
  const [lastFusion, setLastFusion] = useState<TranscriptResponse | null>(null);
  const [scenario, setScenario] = useState<ScenarioResponse | null>(null);
  const [copilot, setCopilot] = useState<CopilotResponse | null>(null);
  const [liveSession, setLiveSession] = useState<LiveVoiceSessionSummary | null>(null);
  const [liveChunks, setLiveChunks] = useState<LiveTranscriptChunk[]>([]);
  const [liveComplete, setLiveComplete] = useState(false);
  const [liveAudioSession, setLiveAudioSession] = useState<LiveAudioSessionSummary | null>(null);
  const [liveAudioChunks, setLiveAudioChunks] = useState<LiveAudioChunkResponse[]>([]);
  const [isListening, setIsListening] = useState(false);
  const [audioStatus, setAudioStatus] = useState<string | null>(null);
  const [now, setNow] = useState(() => new Date());
  const microphoneControllerRef = useRef<MicrophoneController | null>(null);

  useEffect(() => {
    void refreshDemo();
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (state?.cpr_status !== "Active" && state?.cpr_status !== "Paused") {
      return;
    }
    const timer = window.setInterval(() => void refreshDemo(), 1000);
    return () => window.clearInterval(timer);
  }, [state?.cpr_status]);

  const totalArrestSeconds = useMemo(
    () => totalSecondsFromTimeline(state?.timeline ?? [], now),
    [state?.timeline, now],
  );
  const cprTimer = useMemo(
    () => cprTimerSnapshot(state?.cpr_cycle_elapsed_seconds),
    [state?.cpr_cycle_elapsed_seconds],
  );
  const pendingFusions = (lastFusion?.fusion_results ?? []).filter(
    (result) => isConfirmableFusionResult(result) || isRejectableFusionResult(result),
  );
  const pendingFusion = pendingFusions[0] ?? null;
  const hasConfirmation = pendingFusion !== null;

  useEffect(() => {
    if (!liveSession?.active || liveComplete || isListening || liveAudioSession?.active) {
      return;
    }
    const timer = window.setInterval(() => {
      if (!isLoading) {
        void advanceLiveDemo();
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [
    liveSession?.active,
    liveSession?.session_id,
    liveComplete,
    isLoading,
    isListening,
    liveAudioSession?.active,
  ]);

  useEffect(() => {
    return () => {
      microphoneControllerRef.current?.stop();
    };
  }, []);

  async function refreshDemo() {
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/demo`);
      if (!response.ok) {
        throw new Error("Unable to load Pulse.");
      }
      setState(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown Pulse error.");
    }
  }

  async function sendDemoEvent(action: DemoAction) {
    await withLoading(async () => {
      const response = await fetch(`${API_BASE}/api/demo/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!response.ok) {
        throw new Error("Unable to record event.");
      }
      setState(await response.json());
    });
  }

  async function sendTranscript() {
    if (!transcriptText.trim()) {
      return;
    }
    await withLoading(async () => {
      const response = await fetch(`${API_BASE}/api/demo/transcripts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: transcriptText,
          confidence: 0.95,
          speaker_label: "recorder",
        }),
      });
      if (!response.ok) {
        throw new Error("Unable to process voice evidence.");
      }
      const result = (await response.json()) as TranscriptResponse;
      setState(result.state);
      setLastFusion(result);
    });
  }

  async function resolvePendingConfirmation(action: "confirm" | "reject") {
    const candidate = pendingFusion?.candidate_event;
    if (!candidate) {
      return;
    }
    const confirmationRequest = lastFusion?.confirmation_requests.find(
      (request) => request.candidate_event_id === candidate.id,
    );
    await withLoading(async () => {
      const result = (await resolveVoiceConfirmation({
        apiBase: API_BASE,
        action,
        candidateEventId: candidate.id,
        confirmationRequestId: confirmationRequest?.id,
      })) as TranscriptResponse;
      setState(result.state);
      setLastFusion(result);
    });
  }

  async function resolvePendingCorrection(correction: CorrectionFormValue) {
    const candidate = pendingFusion?.candidate_event;
    if (!candidate) {
      return;
    }
    const confirmationRequest = lastFusion?.confirmation_requests.find(
      (request) => request.candidate_event_id === candidate.id,
    );
    await withLoading(async () => {
      const result = (await resolveVoiceCorrection({
        apiBase: API_BASE,
        candidateEventId: candidate.id,
        confirmationRequestId: confirmationRequest?.id,
        correction,
      })) as TranscriptResponse;
      setState(result.state);
      setLastFusion(result);
    });
  }

  async function undoAutoAcceptedEvent(eventId: string) {
    await withLoading(async () => {
      const response = await fetch(`${API_BASE}/api/demo/auto-accepted/undo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: eventId }),
      });
      if (!response.ok) {
        throw new Error("Unable to undo auto-accepted event.");
      }
      const result = (await response.json()) as TranscriptResponse;
      setState(result.state);
      setLastFusion(result);
    });
  }

  async function startLiveDemo() {
    if (isListening || liveAudioSession?.active) {
      setError("Stop the microphone session before running the scripted demo.");
      return;
    }
    await withLoading(async () => {
      const result = (await startLiveVoiceSession({
        apiBase: API_BASE,
      })) as LiveVoiceSessionResponse;
      setLiveSession(result.session);
      setLiveChunks(result.chunks);
      setLiveComplete(false);
      setState(result.state);
      setLastFusion(null);
      setScenario(null);
    });
  }

  async function stopLiveDemo() {
    if (!liveSession) {
      return;
    }
    await withLoading(async () => {
      const result = (await stopLiveVoiceSession({
        apiBase: API_BASE,
        sessionId: liveSession.session_id,
      })) as LiveVoiceSessionResponse;
      setLiveSession(result.session);
      setLiveChunks(result.chunks);
      setState(result.state);
    });
  }

  async function advanceLiveDemo() {
    if (!liveSession?.active || liveComplete) {
      return;
    }
    await withLoading(async () => {
      const result = (await advanceScriptedLiveVoice({
        apiBase: API_BASE,
        sessionId: liveSession.session_id,
      })) as LiveScriptedStreamResponse;
      setLiveSession(result.session);
      if (result.chunk) {
        setLiveChunks((chunks) => [...chunks, result.chunk as LiveTranscriptChunk]);
      }
      if (result.result) {
        setState(result.result.state);
        setLastFusion(result.result);
      }
      if (result.is_complete) {
        setLiveComplete(true);
      }
    });
  }

  async function startListening() {
    await withLoading(async () => {
      if (liveSession?.active) {
        const stoppedScript = (await stopLiveVoiceSession({
          apiBase: API_BASE,
          sessionId: liveSession.session_id,
        })) as LiveVoiceSessionResponse;
        setLiveSession(stoppedScript.session);
        setLiveChunks(stoppedScript.chunks);
      }
      const result = (await startLiveAudioSession({
        apiBase: API_BASE,
      })) as LiveAudioSessionResponse;
      const sessionId = result.session.session_id;
      const controller = createMicrophoneCaptureController({
        mediaDevices: navigator.mediaDevices,
        MediaRecorderCtor: window.MediaRecorder,
        onChunk: (chunk: CapturedMicrophoneChunk) => {
          void sendLiveAudioChunk(sessionId, chunk);
        },
        onError: (err: Error) => setAudioStatus(err.message),
      }) as MicrophoneController;
      microphoneControllerRef.current = controller;
      await controller.start();
      setLiveAudioSession(result.session);
      setLiveAudioChunks([]);
      setAudioStatus(`Listening with ${providerStatusLabel(result.session)}.`);
      setIsListening(true);
      setState(result.state);
      setLastFusion(null);
      setScenario(null);
    });
  }

  async function stopListening() {
    const sessionId = liveAudioSession?.session_id;
    microphoneControllerRef.current?.stop();
    microphoneControllerRef.current = null;
    setIsListening(false);
    if (!sessionId) {
      return;
    }
    await withLoading(async () => {
      const result = (await stopLiveAudioSession({
        apiBase: API_BASE,
        sessionId,
      })) as LiveAudioSessionResponse;
      setLiveAudioSession(result.session);
      setState(result.state);
      setAudioStatus("Listening stopped.");
    });
  }

  async function sendLiveAudioChunk(
    sessionId: string,
    chunk: CapturedMicrophoneChunk,
  ) {
    try {
      const response = (await uploadLiveAudioChunk({
        apiBase: API_BASE,
        sessionId,
        sequence: chunk.sequence,
        blob: chunk.blob,
        timestamp: chunk.timestamp,
        contentType: chunk.contentType,
      })) as LiveAudioChunkResponse;
      setLiveAudioSession(response.session);
      setLiveAudioChunks((chunks) => [...chunks, response]);
      if (response.result) {
        setState(response.result.state);
        setLastFusion(response.result);
      }
      setAudioStatus(response.transcription_error);
    } catch (err) {
      setAudioStatus(err instanceof Error ? err.message : "Unable to process audio chunk.");
    }
  }

  async function runScenario() {
    await withLoading(async () => {
      const response = await fetch(`${API_BASE}/api/demo/scenario/end-to-end`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error("Unable to run demo scenario.");
      }
      const result = (await response.json()) as ScenarioResponse;
      setScenario(result);
      setState(result.state);
    });
  }

  async function refreshCopilot() {
    await withLoading(async () => {
      const response = await fetch(`${API_BASE}/api/demo/copilot`);
      if (!response.ok) {
        throw new Error("Unable to load Pulse summary.");
      }
      setCopilot((await response.json()) as CopilotResponse);
    });
  }

  async function endDemo() {
    microphoneControllerRef.current?.stop();
    microphoneControllerRef.current = null;
    await withLoading(async () => {
      const response = await fetch(`${API_BASE}/api/demo/reset`, {
        method: "POST",
      });
      if (!response.ok) {
        throw new Error("Unable to end demo.");
      }
      setState(await response.json());
      setLastFusion(null);
      setScenario(null);
      setCopilot(null);
      setLiveSession(null);
      setLiveChunks([]);
      setLiveComplete(false);
      setLiveAudioSession(null);
      setLiveAudioChunks([]);
      setIsListening(false);
      setAudioStatus(null);
    });
  }

  async function withLoading(work: () => Promise<void>) {
    setIsLoading(true);
    setError(null);
    try {
      await work();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown Pulse error.");
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="min-h-screen overflow-x-hidden bg-[#05070B] text-[#F4EFE6] antialiased">
      <div
        className="mx-auto box-border flex min-h-screen min-w-0 flex-col pb-8 pt-4"
        style={{ width: "min(760px, calc(100vw - 2rem))" }}
      >
        <TopStatus state={state} isLoading={isLoading} />
        {error ? <ErrorBanner message={error} /> : null}

        <section className="flex min-w-0 flex-1 flex-col justify-center py-5">
          <PrimaryClinicalState
            state={state}
            confidence={acceptedConfidence(lastFusion)}
            hasConfirmation={hasConfirmation}
            cprTimer={cprTimer}
            totalArrestSeconds={totalArrestSeconds}
            pendingFusion={pendingFusion}
            onResumeCpr={() => void sendDemoEvent("cpr_resumed")}
            undoableEventIds={lastFusion?.undoable_event_ids ?? state?.undoable_event_ids ?? []}
            onUndoAutoAccepted={(eventId) => void undoAutoAcceptedEvent(eventId)}
          />
        </section>

        <div className="space-y-6">
          <details className="rounded-[1.25rem] bg-[#101217]/72 px-5 py-4">
            <summary className="cursor-pointer text-sm font-semibold text-[#F4EFE6]/64">
              Audit trail: recent events and voice evidence
            </summary>
            <div className="mt-5 space-y-6">
              <EventStream state={state} lastFusion={lastFusion} />
              <VoiceEvidencePanel lastFusion={lastFusion} />
            </div>
          </details>
          <CopilotPanel
            state={state}
            copilot={copilot}
            refreshCopilot={refreshCopilot}
            isLoading={isLoading}
          />
          <DemoDrawer
            transcriptText={transcriptText}
            setTranscriptText={setTranscriptText}
            sendTranscript={sendTranscript}
            sendDemoEvent={sendDemoEvent}
            runScenario={runScenario}
            endDemo={endDemo}
            scenario={scenario}
            liveSession={liveSession}
            liveChunks={liveChunks}
            liveComplete={liveComplete}
            liveAudioSession={liveAudioSession}
            liveAudioChunks={liveAudioChunks}
            isListening={isListening}
            audioStatus={audioStatus}
            startLiveDemo={startLiveDemo}
            stopLiveDemo={stopLiveDemo}
            advanceLiveDemo={advanceLiveDemo}
            startListening={startListening}
            stopListening={stopListening}
            isLoading={isLoading}
          />
        </div>
      </div>

      <ConfirmationSheet
        key={pendingFusion?.candidate_event?.id ?? "no-pending-confirmation"}
        fusion={pendingFusion}
        visible={hasConfirmation}
        isLoading={isLoading}
        queueTotal={pendingFusions.length}
        onConfirm={() => resolvePendingConfirmation("confirm")}
        onReject={() => resolvePendingConfirmation("reject")}
        onCorrect={resolvePendingCorrection}
      />
    </main>
  );
}

function TopStatus({
  state,
  isLoading,
}: {
  state: DemoState | null;
  isLoading: boolean;
}) {
  return (
    <header className="flex min-h-12 min-w-0 items-center justify-between gap-4">
      <div className="text-[0.95rem] font-semibold tracking-[0.22em] text-[#F4EFE6]">
        PULSE
      </div>
      <div className="flex min-w-0 items-center gap-3">
        <div className="min-w-0 truncate text-right text-xs font-semibold uppercase tracking-[0.12em] text-[#F4EFE6]/48">
          {state
            ? `${format(state.current_workflow_phase)} · ${state.current_rhythm}`
            : "Connecting"}
        </div>
        <div className="flex items-center gap-2 text-sm font-medium text-[#F4EFE6]/72">
          <span
            className={`h-2.5 w-2.5 rounded-full ${
              isLoading ? "bg-[#D8A536]" : "bg-[#63B77A]"
            }`}
          />
          Voice
        </div>
      </div>
    </header>
  );
}

function PrimaryClinicalState({
  state,
  confidence,
  hasConfirmation,
  cprTimer,
  totalArrestSeconds,
  pendingFusion,
  onResumeCpr,
  undoableEventIds,
  onUndoAutoAccepted,
}: {
  state: DemoState | null;
  confidence: number | null;
  hasConfirmation: boolean;
  cprTimer: CprTimerSnapshot | null;
  totalArrestSeconds: number;
  pendingFusion: FusionResult | null;
  onResumeCpr: () => void;
  undoableEventIds: string[];
  onUndoAutoAccepted: (eventId: string) => void;
}) {
  const action = state?.primary_action ?? "Loading clinical state";
  const profile = actionProfile(action, state);
  const status = evidenceStatus(confidence, hasConfirmation);
  const secondaryActions = (state?.secondary_actions ?? [])
    .filter((item) => Boolean(item.trim()))
    .slice(0, 3);
  const handsOffSeconds = state?.cpr_hands_off_elapsed_seconds ?? null;
  const showHandsOff = state?.cpr_status === "Paused"
    && state?.rosc_status !== "Achieved"
    && handsOffSeconds !== null;
  const handsOffEscalated = showHandsOff && handsOffSeconds > 10;
  const cprClockLabel = showHandsOff
    ? `${handsOffSeconds}s hands-off`
    : cprTimer
      ? clock(cprTimer.elapsedSeconds)
      : "--";
  const actionText = showHandsOff
    ? `CPR PAUSED - ${handsOffSeconds}s hands-off`
    : displayAction(action);

  return (
    <section
      key={`${state?.current_workflow_phase ?? "loading"}-${action}`}
      className="transition-all duration-500 ease-out"
    >
      <div
        className="box-border min-h-[58vh] min-w-0 rounded-[2rem] bg-[#101217]/86 px-5 py-6 shadow-[inset_0_1px_0_rgba(255,255,255,0.07),0_28px_70px_rgba(0,0,0,0.42)] sm:px-7 sm:py-7 md:min-h-[560px] md:px-11 md:py-10"
        style={{ width: "100%", maxWidth: "100%" }}
      >
        <div className="min-w-0">
          <div className="text-sm font-bold uppercase tracking-[0.18em] text-[#F4EFE6]/48">
            Next action
          </div>
          <div
            className={`mt-4 rounded-[1.5rem] px-4 py-5 ${
              showHandsOff
                ? handsOffEscalated
                  ? "bg-[#E14B4B]/20 ring-2 ring-[#E14B4B]/55"
                  : "bg-[#D8A536]/14 ring-2 ring-[#D8A536]/35"
                : "bg-[#05070B]/34"
            }`}
          >
            <h1 className={`text-balance text-5xl font-black leading-[0.92] tracking-normal sm:text-6xl md:text-7xl ${
              showHandsOff
                ? handsOffEscalated ? "text-[#FFEAEA]" : "text-[#FFF4D3]"
                : profile.text
            }`}>
              {actionText}
            </h1>
            <SecondaryActions actions={secondaryActions} />
            {showHandsOff ? (
              <>
                <div className="mt-4 text-xl font-semibold leading-7 text-[#F4EFE6]/76 md:text-2xl">
                  Say "Resume CPR" or tap below.
                </div>
                <button
                  type="button"
                  onClick={onResumeCpr}
                  className={`mt-5 min-h-16 w-full rounded-[1.25rem] px-5 text-xl font-bold tracking-normal text-[#F4EFE6] shadow-[inset_0_2px_0_rgba(255,255,255,0.18),0_18px_40px_rgba(0,0,0,0.32)] transition-transform active:scale-[0.99] ${profile.button}`}
                >
                  Resume CPR
                </button>
              </>
            ) : null}
          </div>
        </div>

        <div className="mt-5 grid grid-cols-2 gap-3">
          <TimerTile label="Arrest elapsed" value={clock(totalArrestSeconds)} />
          <TimerTile
            label={showHandsOff ? "CPR paused" : "CPR cycle"}
            value={cprClockLabel}
            urgent={Boolean(handsOffEscalated)}
          />
        </div>

        <div className="mt-5 grid grid-cols-2 gap-3 rounded-[1.25rem] bg-[#05070B]/34 px-4 py-4">
          <StatusTile label="Rhythm" value={state?.current_rhythm ?? "Unknown"} tone={rhythmTone(state)} />
          <StatusTile label="Pathway" value={state?.current_pathway ?? "Unknown"} />
        </div>

        <InlineConfirmation fusion={pendingFusion} status={status.label} />
        <SafetyAdvisories flags={state?.safety_flags ?? []} />
        <AutoAcceptedUndo
          eventIds={undoableEventIds}
          isLoading={false}
          onUndo={onUndoAutoAccepted}
        />
      </div>
    </section>
  );
}

function SafetyAdvisories({ flags }: { flags: string[] }) {
  const advisories = flags.filter((flag) => flag.includes("Shock recorded"));
  if (!advisories.length) {
    return null;
  }
  return (
    <div className="mt-4 rounded-xl bg-[#E14B4B]/14 px-4 py-3 text-sm font-semibold leading-5 text-[#FFEAEA] ring-1 ring-[#E14B4B]/32">
      {advisories.map((flag) => <div key={flag}>{flag}</div>)}
    </div>
  );
}

function AutoAcceptedUndo({
  eventIds,
  isLoading,
  onUndo,
}: {
  eventIds: string[];
  isLoading: boolean;
  onUndo: (eventId: string) => void;
}) {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    setVisible(true);
    const timer = window.setTimeout(() => setVisible(false), 30_000);
    return () => window.clearTimeout(timer);
  }, [eventIds.join(",")]);

  if (!visible || !eventIds.length) {
    return null;
  }
  return (
    <div className="mt-4 flex items-center justify-between gap-3 rounded-xl bg-[#63B77A]/14 px-4 py-3 text-sm font-semibold text-[#EAF8EE] ring-1 ring-[#63B77A]/28">
      <span>Completion recorded</span>
      <button
        type="button"
        disabled={isLoading}
        onClick={() => onUndo(eventIds[0])}
        className="rounded-lg bg-[#F4EFE6]/12 px-3 py-2 text-[#F4EFE6] disabled:opacity-35"
      >
        Undo
      </button>
    </div>
  );
}

function SecondaryActions({ actions }: { actions: string[] }) {
  if (!actions.length) {
    return null;
  }
  return (
    <div className="mt-4 rounded-xl bg-[#F4EFE6]/6 px-3 py-3 text-left">
      <div className="text-xs font-bold uppercase tracking-[0.14em] text-[#F4EFE6]/48">
        Also due
      </div>
      <ul className="mt-1.5 space-y-1.5 text-base font-semibold leading-5 text-[#F4EFE6]/82 sm:text-lg">
        {actions.map((item) => (
          <li key={item} className="flex gap-2">
            <span aria-hidden="true" className="text-[#D8A536]">•</span>
            <span>{stripPeriod(item)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TimerTile({
  label,
  value,
  urgent = false,
}: {
  label: string;
  value: string;
  urgent?: boolean;
}) {
  return (
    <div className={`rounded-[1.25rem] px-4 py-4 ${urgent ? "bg-[#E14B4B]/18" : "bg-[#05070B]/34"}`}>
      <div className="text-xs font-bold uppercase tracking-[0.14em] text-[#F4EFE6]/42">
        {label}
      </div>
      <div className={`mt-2 font-mono text-3xl font-black tabular-nums tracking-normal sm:text-4xl ${
        urgent ? "text-[#FFEAEA]" : "text-[#F4EFE6]"
      }`}>
        {value}
      </div>
    </div>
  );
}

function StatusTile({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "red" | "green" | "amber" | "neutral";
}) {
  return (
    <div>
      <div className="text-xs font-bold uppercase tracking-[0.14em] text-[#F4EFE6]/38">
        {label}
      </div>
      <div className={`mt-1 truncate text-2xl font-bold tracking-normal ${stripTone(tone)}`}>
        {value}
      </div>
    </div>
  );
}

function InlineConfirmation({
  fusion,
  status,
}: {
  fusion: FusionResult | null;
  status: string;
}) {
  if (!fusion?.candidate_event) {
    return (
      <div className="mt-5 rounded-[1.25rem] bg-[#05070B]/24 px-4 py-3 text-sm font-semibold text-[#F4EFE6]/44">
        {status}
      </div>
    );
  }
  const event = fusion.candidate_event;
  return (
    <div className="mt-5 rounded-[1.25rem] bg-[#D8A536]/12 px-4 py-4 ring-1 ring-[#D8A536]/24">
      <div className="text-xs font-bold uppercase tracking-[0.14em] text-[#FFF4D3]/60">
        Pending confirmation
      </div>
      <div className="mt-1 text-xl font-bold text-[#F4EFE6]">
        {payloadSummary(event.event_type, event.payload)}
      </div>
      <div className="mt-1 text-sm font-semibold text-[#F4EFE6]/54">
        {fusion.uncertainty_reason === "conflicting_evidence"
          ? "Needs human clarification"
          : `${format(event.status)} · ${percent(event.confidence)}`}
      </div>
    </div>
  );
}

function PatientStrip({ state }: { state: DemoState | null }) {
  const items = [
    { icon: "♥", value: state?.current_rhythm ?? "--", tone: rhythmTone(state) },
    { icon: "⚡", value: `${state?.shock_count ?? 0}`, tone: "neutral" as const },
    { icon: "✚", value: compactMedication(state), tone: "neutral" as const },
    {
      icon: "✓",
      value: state?.rosc_status === "Achieved" ? "ROSC" : "No ROSC",
      tone: state?.rosc_status === "Achieved" ? ("green" as const) : ("neutral" as const),
    },
  ];

  return (
    <div className="mt-5 flex snap-x gap-2 overflow-x-auto pb-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
      {items.map((item) => (
        <div
          key={`${item.icon}-${item.value}`}
          className={`flex min-h-12 min-w-max snap-start items-center gap-2 rounded-full px-4 text-sm font-semibold ${stripTone(item.tone)}`}
        >
          <span>{item.icon}</span>
          <span>{item.value}</span>
        </div>
      ))}
    </div>
  );
}

function EventStream({
  state,
  lastFusion,
}: {
  state: DemoState | null;
  lastFusion: TranscriptResponse | null;
}) {
  const events = [...(state?.timeline ?? [])].reverse().slice(0, 4);
  return (
    <section aria-label="Recent clinical events">
      <div className="mb-3 text-sm font-medium text-[#F4EFE6]/42">Recent events</div>
      <div className="space-y-4">
        {events.length ? (
          events.map((event) => (
            <EventRow
              key={event.id}
              event={event}
              fusion={matchingFusion(event, lastFusion)}
              state={state}
            />
          ))
        ) : (
          <div className="py-4 text-base font-medium text-[#F4EFE6]/36">
            Waiting for accepted clinical events.
          </div>
        )}
      </div>
    </section>
  );
}

function EventRow({
  event,
  fusion,
  state,
}: {
  event: TimelineEntry;
  fusion: FusionResult | null;
  state: DemoState | null;
}) {
  return (
    <article className="grid grid-cols-[4.75rem_2rem_1fr] items-start gap-3">
      <time className="pt-1 font-mono text-sm font-medium tabular-nums text-[#F4EFE6]/42">
        {eventTime(event.timestamp)}
      </time>
      <div className="grid h-9 w-9 place-items-center rounded-full bg-[#15181F] text-lg">
        {eventIcon(event.event_type)}
      </div>
      <div>
        <div className="text-lg font-semibold leading-6 text-[#F4EFE6]">{eventTitle(event)}</div>
        <div className="mt-1 text-sm font-medium leading-5 text-[#F4EFE6]/48">
          {clinicalImpact(event, state)}
        </div>
        <div className="mt-1 text-xs font-medium text-[#F4EFE6]/32">
          Confidence {percent(fusion?.candidate_event?.confidence)}
        </div>
      </div>
    </article>
  );
}

function VoiceEvidencePanel({ lastFusion }: { lastFusion: TranscriptResponse | null }) {
  if (!lastFusion?.fusion_results.length) {
    return null;
  }

  return (
    <section aria-label="Voice evidence review" className="rounded-[1.25rem] bg-[#111319]/72 px-5 py-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="text-sm font-semibold text-[#F4EFE6]/58">Voice evidence</div>
        <div className="text-xs font-medium text-[#F4EFE6]/38">
          {lastFusion.fusion_results.length} result{lastFusion.fusion_results.length === 1 ? "" : "s"}
        </div>
      </div>
      <div className="space-y-3">
        {lastFusion.fusion_results.map((result, index) => (
          <VoiceFusionRow
            key={`${result.result_kind}-${result.evidence_ids.join("-")}-${index}`}
            result={result}
            evidence={evidenceForResult(result, lastFusion.evidence)}
          />
        ))}
      </div>
    </section>
  );
}

function VoiceFusionRow({
  result,
  evidence,
}: {
  result: FusionResult;
  evidence: EvidenceSummary[];
}) {
  const candidate = result.candidate_event;
  const negative = isNegativeEvidenceResult(result);
  const kind = observationKindFromEvidence(evidence);
  const eventType = candidate?.event_type ?? result.correction_target_event_type ?? "evidence";
  const confidence = candidate?.confidence ?? maxEvidenceConfidence(evidence);
  const title = negative
    ? `Negative ${format(eventType)} evidence`
    : candidate
      ? payloadSummary(candidate.event_type, candidate.payload)
      : fusionDecisionLabel(result);
  const decision = fusionDecisionLabel(result);
  const tone = voiceResultTone(result);

  return (
    <article className={`rounded-[1rem] px-4 py-3 ring-1 ${tone.surface}`}>
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className={`text-xs font-semibold ${tone.text}`}>{decision}</div>
          <div className="mt-1 truncate text-base font-semibold text-[#F4EFE6]">{title}</div>
          <div className="mt-1 text-sm font-medium text-[#F4EFE6]/50">
            {observationKindLabel(kind)} · {evidenceSourceSummary(evidence)} · {percent(confidence)}
          </div>
        </div>
        <div className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${tone.badge}`}>
          {candidate?.status ? format(candidate.status) : format(result.result_kind)}
        </div>
      </div>
      {candidate?.event_type === "medication_given" ? (
        <div className="mt-3 grid grid-cols-3 gap-2 text-xs font-semibold text-[#F4EFE6]/62">
          <MetricPill label="Medication" value={payloadValue(candidate.payload.medication)} />
          <MetricPill
            label="Dose"
            value={
              candidate.payload.dose !== undefined && candidate.payload.unit
                ? `${candidate.payload.dose} ${candidate.payload.unit}`
                : "--"
            }
          />
          <MetricPill label="Route" value={payloadValue(candidate.payload.route)} />
        </div>
      ) : null}
      <div className="mt-3 space-y-2">
        {evidence.map((item) => (
          <EvidenceLine key={item.id} evidence={item} />
        ))}
      </div>
    </article>
  );
}

function MetricPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl bg-[#05070B]/70 px-3 py-2">
      <div className="text-[0.68rem] uppercase tracking-normal text-[#F4EFE6]/34">{label}</div>
      <div className="mt-0.5 truncate text-sm text-[#F4EFE6]/82">{value}</div>
    </div>
  );
}

function EvidenceLine({ evidence }: { evidence: EvidenceSummary }) {
  const kind = observationKindFromEvidence([evidence]);
  const speakerSummary = evidenceSpeakerSummary(evidence);
  return (
    <div className="flex min-w-0 items-start justify-between gap-3 rounded-xl bg-[#05070B]/45 px-3 py-2 text-xs font-medium text-[#F4EFE6]/52">
      <div className="min-w-0">
        <div className="truncate">
          <span className="text-[#F4EFE6]/72">{observationKindLabel(kind)}</span>
          <span className="mx-1.5 text-[#F4EFE6]/24">·</span>
          <span>{evidenceSourceSummary([evidence])}</span>
          {evidence.raw_reference ? (
            <>
              <span className="mx-1.5 text-[#F4EFE6]/24">·</span>
              <span className="truncate">{evidence.raw_reference}</span>
            </>
          ) : null}
        </div>
        {speakerSummary ? (
          <div className="mt-1 truncate text-[0.68rem] text-[#F4EFE6]/34">
            {speakerSummary}
          </div>
        ) : null}
      </div>
      <span className="shrink-0 font-mono tabular-nums">{percent(evidence.confidence)}</span>
    </div>
  );
}

function CopilotPanel({
  state,
  copilot,
  refreshCopilot,
  isLoading,
}: {
  state: DemoState | null;
  copilot: CopilotResponse | null;
  refreshCopilot: () => Promise<void>;
  isLoading: boolean;
}) {
  return (
    <section className="rounded-[1.4rem] bg-[#111319]/72 px-5 py-4">
      <div className="flex items-center justify-between gap-4">
        <div className="text-sm font-semibold text-[#F4EFE6]/58">Pulse summary</div>
        <button
          type="button"
          disabled={isLoading}
          onClick={() => void refreshCopilot()}
          className="min-h-10 rounded-full bg-[#F4EFE6]/8 px-4 text-sm font-medium text-[#F4EFE6]/70 transition hover:bg-[#F4EFE6]/12 disabled:opacity-40"
        >
          Update
        </button>
      </div>
      <p className="mt-3 text-base font-medium leading-7 text-[#F4EFE6]/82">
        {copilot?.message ?? fallbackSummary(state)}
      </p>
    </section>
  );
}

function ConfirmationSheet({
  fusion,
  visible,
  isLoading,
  queueTotal,
  onConfirm,
  onReject,
  onCorrect,
}: {
  fusion: FusionResult | null;
  visible: boolean;
  isLoading: boolean;
  queueTotal: number;
  onConfirm: () => Promise<void>;
  onReject: () => Promise<void>;
  onCorrect: (correction: CorrectionFormValue) => Promise<void>;
}) {
  const [showCorrection, setShowCorrection] = useState(false);
  const [correctionEventType, setCorrectionEventType] = useState("");
  const [correctionRhythm, setCorrectionRhythm] = useState("");
  const [correctionMedication, setCorrectionMedication] = useState("");
  const [correctionDose, setCorrectionDose] = useState("");
  const [correctionRoute, setCorrectionRoute] = useState("");
  const candidate = fusion?.candidate_event ?? null;
  if (!visible || !candidate) {
    return null;
  }
  const evidence = candidate.evidence ?? [];
  const kind = observationKindFromEvidence(evidence);
  const decision = fusionDecisionLabel(fusion);
  const title = fusion?.uncertainty_reason === "conflicting_evidence"
    ? "Needs human clarification"
    : candidateLabel(candidate);
  const canConfirm = isConfirmableFusionResult(fusion) && !isLoading;
  const canReject = isRejectableFusionResult(fusion) && !isLoading;
  const correction = correctionValue({
    eventType: correctionEventType,
    rhythm: correctionRhythm,
    medication: correctionMedication,
    dose: correctionDose,
    route: correctionRoute,
  });
  const canSubmitCorrection = Boolean(correction) && !isLoading;

  return (
    <div
      aria-atomic="true"
      aria-live="assertive"
      className="fixed inset-x-4 bottom-4 z-50 mx-auto max-w-md rounded-[1.35rem] bg-[#17130B]/96 p-5 shadow-[0_24px_70px_rgba(0,0,0,0.62)] backdrop-blur-2xl"
    >
      <div className="flex items-center justify-between gap-3 text-sm font-semibold text-[#D8A536]">
        <span>{decision}</span>
        <span className="rounded-full bg-[#D8A536]/14 px-2.5 py-1 text-xs text-[#FFF4D3]">
          1 of {queueTotal}
        </span>
      </div>
      <div className="mt-2 text-2xl font-semibold leading-7 text-[#F4EFE6]">{title}</div>
      <div className="mt-2 text-base font-medium text-[#F4EFE6]/70">
        {payloadSummary(candidate.event_type, candidate.payload)}
      </div>
      <div className="mt-4 grid grid-cols-3 gap-2 text-xs font-semibold text-[#F4EFE6]/62">
        <MetricPill label="Heard" value={observationKindLabel(kind)} />
        <MetricPill label="Source" value={evidenceSourceSummary(evidence)} />
        <MetricPill label="Confidence" value={percent(candidate.confidence)} />
      </div>
      <div className="mt-3 space-y-2">
        {evidence.map((item) => (
          <EvidenceLine key={item.id} evidence={item} />
        ))}
      </div>
      <div className="mt-5 grid grid-cols-3 gap-2">
        <button
          type="button"
          disabled={!canConfirm}
          onClick={() => void onConfirm()}
          className="min-h-12 rounded-xl bg-[#63B77A]/22 text-sm font-semibold text-[#EAF8EE] disabled:opacity-35"
        >
          Confirm
        </button>
        <button
          type="button"
          disabled={!canReject}
          onClick={() => void onReject()}
          className="min-h-12 rounded-xl bg-[#E14B4B]/18 text-sm font-semibold text-[#FFEAEA] disabled:opacity-35"
        >
          Reject
        </button>
        <button
          type="button"
          disabled={isLoading}
          onClick={() => setShowCorrection((value) => !value)}
          className="min-h-12 rounded-xl bg-[#F4EFE6]/10 text-sm font-semibold text-[#F4EFE6]/80 disabled:opacity-35"
        >
          Correct
        </button>
      </div>
      {showCorrection ? (
        <div className="mt-4 space-y-3 rounded-xl bg-[#05070B]/62 p-3">
          <select
            value={correctionEventType}
            onChange={(event) => setCorrectionEventType(event.target.value)}
            className="min-h-11 w-full rounded-xl bg-[#111319] px-3 text-sm font-semibold text-[#F4EFE6] outline-none ring-1 ring-[#F4EFE6]/10"
          >
            <option value="">Choose corrected event</option>
            <option value="rhythm_checked">Rhythm checked</option>
            <option value="medication_given">Medication given</option>
            <option value="shock_delivered">Shock delivered</option>
            <option value="cpr_started">CPR started</option>
            <option value="cpr_resumed">CPR resumed</option>
            <option value="cpr_paused">CPR paused</option>
            <option value="rosc_achieved">ROSC achieved</option>
          </select>

          {correctionEventType === "rhythm_checked" ? (
            <select
              value={correctionRhythm}
              onChange={(event) => setCorrectionRhythm(event.target.value)}
              className="min-h-11 w-full rounded-xl bg-[#111319] px-3 text-sm font-semibold text-[#F4EFE6] outline-none ring-1 ring-[#F4EFE6]/10"
            >
              <option value="">Choose rhythm</option>
              <option value="vf">VF</option>
              <option value="pulseless_vt">Pulseless VT</option>
              <option value="pea">PEA</option>
              <option value="asystole">Asystole</option>
              <option value="rosc">ROSC</option>
            </select>
          ) : null}

          {correctionEventType === "medication_given" ? (
            <div className="grid gap-2">
              <select
                value={correctionMedication}
                onChange={(event) => setCorrectionMedication(event.target.value)}
                className="min-h-11 w-full rounded-xl bg-[#111319] px-3 text-sm font-semibold text-[#F4EFE6] outline-none ring-1 ring-[#F4EFE6]/10"
              >
                <option value="">Choose medication</option>
                <option value="epinephrine">Epinephrine</option>
                <option value="amiodarone">Amiodarone</option>
                <option value="lidocaine">Lidocaine</option>
              </select>
              <div className="grid grid-cols-2 gap-2">
                <input
                  value={correctionDose}
                  onChange={(event) => setCorrectionDose(event.target.value)}
                  placeholder="Dose mg"
                  inputMode="decimal"
                  className="min-h-11 rounded-xl bg-[#111319] px-3 text-sm font-semibold text-[#F4EFE6] outline-none ring-1 ring-[#F4EFE6]/10"
                />
                <select
                  value={correctionRoute}
                  onChange={(event) => setCorrectionRoute(event.target.value)}
                  className="min-h-11 rounded-xl bg-[#111319] px-3 text-sm font-semibold text-[#F4EFE6] outline-none ring-1 ring-[#F4EFE6]/10"
                >
                  <option value="">Route</option>
                  <option value="IV">IV</option>
                  <option value="IO">IO</option>
                  <option value="IV/IO">IV/IO</option>
                </select>
              </div>
            </div>
          ) : null}

          <button
            type="button"
            disabled={!canSubmitCorrection}
            onClick={() => correction ? void onCorrect(correction) : undefined}
            className="min-h-11 w-full rounded-xl bg-[#D8A536]/20 text-sm font-semibold text-[#FFF4D3] disabled:opacity-35"
          >
            Submit correction
          </button>
        </div>
      ) : null}
    </div>
  );
}

function DemoDrawer({
  transcriptText,
  setTranscriptText,
  sendTranscript,
  sendDemoEvent,
  runScenario,
  endDemo,
  scenario,
  liveSession,
  liveChunks,
  liveComplete,
  liveAudioSession,
  liveAudioChunks,
  isListening,
  audioStatus,
  startLiveDemo,
  stopLiveDemo,
  advanceLiveDemo,
  startListening,
  stopListening,
  isLoading,
}: {
  transcriptText: string;
  setTranscriptText: (value: string) => void;
  sendTranscript: () => Promise<void>;
  sendDemoEvent: (action: DemoAction) => Promise<void>;
  runScenario: () => Promise<void>;
  endDemo: () => Promise<void>;
  scenario: ScenarioResponse | null;
  liveSession: LiveVoiceSessionSummary | null;
  liveChunks: LiveTranscriptChunk[];
  liveComplete: boolean;
  liveAudioSession: LiveAudioSessionSummary | null;
  liveAudioChunks: LiveAudioChunkResponse[];
  isListening: boolean;
  audioStatus: string | null;
  startLiveDemo: () => Promise<void>;
  stopLiveDemo: () => Promise<void>;
  advanceLiveDemo: () => Promise<void>;
  startListening: () => Promise<void>;
  stopListening: () => Promise<void>;
  isLoading: boolean;
}) {
  return (
    <details className="group rounded-[1.25rem] bg-[#F4EFE6]/[0.035]">
      <summary className="flex min-h-12 cursor-pointer list-none items-center justify-between px-4 text-sm font-medium text-[#F4EFE6]/42 transition group-open:text-[#F4EFE6]/62">
        Demo
        <span>Open</span>
      </summary>
      <div className="space-y-4 px-4 pb-4">
        <LiveVoiceDemo
          liveSession={liveSession}
          liveChunks={liveChunks}
          liveComplete={liveComplete}
          isListening={isListening || Boolean(liveAudioSession?.active)}
          startLiveDemo={startLiveDemo}
          stopLiveDemo={stopLiveDemo}
          advanceLiveDemo={advanceLiveDemo}
          isLoading={isLoading}
        />
        <LiveAudioDemo
          liveAudioSession={liveAudioSession}
          liveAudioChunks={liveAudioChunks}
          isListening={isListening}
          audioStatus={audioStatus}
          startListening={startListening}
          stopListening={stopListening}
          isLoading={isLoading}
        />
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            disabled={isLoading}
            onClick={() => void runScenario()}
            className="min-h-12 rounded-2xl bg-[#F4EFE6]/10 px-4 text-sm font-semibold text-[#F4EFE6] disabled:opacity-40"
          >
            Run scenario
          </button>
          <button
            type="button"
            disabled={isLoading}
            onClick={() => void endDemo()}
            className="min-h-12 rounded-2xl bg-[#F4EFE6]/6 px-4 text-sm font-semibold text-[#F4EFE6]/72 disabled:opacity-40"
          >
            End demo
          </button>
        </div>
        <div className="grid gap-2 sm:grid-cols-[1fr_96px]">
          <input
            value={transcriptText}
            onChange={(event) => setTranscriptText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !isLoading) {
                event.preventDefault();
                void sendTranscript();
              }
            }}
            aria-label="Say something the team would say out loud"
            placeholder="rhythm is vf"
            className="min-h-12 rounded-2xl bg-[#05070B] px-4 font-medium text-[#F4EFE6] outline-none ring-1 ring-[#F4EFE6]/10 placeholder:text-[#F4EFE6]/28 focus:ring-[#D8A536]/70"
          />
          <button
            type="button"
            disabled={isLoading}
            onClick={() => void sendTranscript()}
            className="min-h-12 rounded-2xl bg-[#F4EFE6]/8 px-4 font-semibold text-[#F4EFE6]/82 disabled:opacity-40"
          >
            Send
          </button>
        </div>
        <div className="flex flex-wrap gap-2">
          {demoActions.map((item) => (
            <button
              key={item.action}
              type="button"
              disabled={isLoading}
              onClick={() => void sendDemoEvent(item.action)}
              className="min-h-10 rounded-full bg-[#F4EFE6]/7 px-3 text-sm font-medium text-[#F4EFE6]/72 disabled:opacity-40"
            >
              {item.label}
            </button>
          ))}
        </div>
        <ScenarioProgress scenario={scenario} />
      </div>
    </details>
  );
}

function LiveVoiceDemo({
  liveSession,
  liveChunks,
  liveComplete,
  isListening,
  startLiveDemo,
  stopLiveDemo,
  advanceLiveDemo,
  isLoading,
}: {
  liveSession: LiveVoiceSessionSummary | null;
  liveChunks: LiveTranscriptChunk[];
  liveComplete: boolean;
  isListening: boolean;
  startLiveDemo: () => Promise<void>;
  stopLiveDemo: () => Promise<void>;
  advanceLiveDemo: () => Promise<void>;
  isLoading: boolean;
}) {
  const isActive = Boolean(liveSession?.active);
  return (
    <details className="rounded-[1rem] bg-[#05070B]/58 px-4 py-3">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-[#F4EFE6]/72">
            Scripted Demo (no microphone)
          </div>
          <div className="mt-0.5 text-xs font-medium text-[#F4EFE6]/38">
            Canned transcript stream
          </div>
        </div>
        <div className="text-xs font-semibold text-[#F4EFE6]/44">
          {isListening ? "Mic active" : isActive ? "Running" : liveSession ? "Stopped" : "Ready"}
        </div>
      </summary>
      <div className="mt-3 grid grid-cols-3 gap-2">
        <button
          type="button"
          disabled={isLoading || isActive || isListening}
          onClick={() => void startLiveDemo()}
          className="min-h-11 rounded-xl bg-[#63B77A]/16 px-3 text-sm font-semibold text-[#EAF8EE] disabled:opacity-35"
        >
          Run Script
        </button>
        <button
          type="button"
          disabled={isLoading || !isActive}
          onClick={() => void stopLiveDemo()}
          className="min-h-11 rounded-xl bg-[#E14B4B]/14 px-3 text-sm font-semibold text-[#FFEAEA] disabled:opacity-35"
        >
          Stop
        </button>
        <button
          type="button"
          disabled={isLoading || !isActive || liveComplete || isListening}
          onClick={() => void advanceLiveDemo()}
          className="min-h-11 rounded-xl bg-[#F4EFE6]/8 px-3 text-sm font-semibold text-[#F4EFE6]/78 disabled:opacity-35"
        >
          Next
        </button>
      </div>
      {liveChunks.length ? (
        <div className="mt-3 max-h-40 space-y-2 overflow-y-auto pr-1">
          {liveChunks.map((chunk) => (
            <div
              key={`${chunk.session_id}-${chunk.sequence}`}
              className="rounded-xl bg-[#111319]/78 px-3 py-2 text-xs font-medium text-[#F4EFE6]/58"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="font-mono text-[#F4EFE6]/34">#{chunk.sequence}</span>
                <span>{format(chunk.language)} · {percent(chunk.confidence)}</span>
              </div>
              <div className="mt-1 text-sm font-semibold text-[#F4EFE6]/82">{chunk.text}</div>
            </div>
          ))}
          {liveComplete ? (
            <div className="text-xs font-medium text-[#F4EFE6]/34">Script complete.</div>
          ) : null}
        </div>
      ) : null}
    </details>
  );
}

function LiveAudioDemo({
  liveAudioSession,
  liveAudioChunks,
  isListening,
  audioStatus,
  startListening,
  stopListening,
  isLoading,
}: {
  liveAudioSession: LiveAudioSessionSummary | null;
  liveAudioChunks: LiveAudioChunkResponse[];
  isListening: boolean;
  audioStatus: string | null;
  startListening: () => Promise<void>;
  stopListening: () => Promise<void>;
  isLoading: boolean;
}) {
  return (
    <section className="rounded-[1rem] bg-[#05070B]/58 px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-[#F4EFE6]/72">Live Audio</div>
          <div className="mt-0.5 text-xs font-medium text-[#F4EFE6]/38">
            Browser microphone foundation
          </div>
        </div>
        <div className="text-xs font-semibold text-[#F4EFE6]/44">
          {isListening ? "Listening" : liveAudioSession ? "Stopped" : "Ready"}
        </div>
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2">
        <button
          type="button"
          disabled={isLoading || isListening}
          onClick={() => void startListening()}
          className="min-h-11 rounded-xl bg-[#63B77A]/16 px-3 text-sm font-semibold text-[#EAF8EE] disabled:opacity-35"
        >
          Start Listening
        </button>
        <button
          type="button"
          disabled={isLoading || !isListening}
          onClick={() => void stopListening()}
          className="min-h-11 rounded-xl bg-[#E14B4B]/14 px-3 text-sm font-semibold text-[#FFEAEA] disabled:opacity-35"
        >
          Stop Listening
        </button>
      </div>
      <div className="mt-3 text-xs font-medium text-[#F4EFE6]/42">
        {providerStatusLabel(liveAudioSession)} · audio references only
      </div>
      {liveAudioSession?.provider_error ? (
        <div className="mt-1 text-xs font-medium text-[#F4EFE6]/34">
          Real provider unavailable; demo fallback is active.
        </div>
      ) : null}
      {audioStatus ? (
        <div className="mt-2 rounded-xl bg-[#D8A536]/12 px-3 py-2 text-xs font-semibold text-[#FFF4D3]">
          {audioStatus}
        </div>
      ) : null}
      {liveAudioChunks.length ? (
        <div className="mt-3 max-h-40 space-y-2 overflow-y-auto pr-1">
          {liveAudioChunks.map((item) => (
            <div
              key={`${item.audio_chunk.session_id}-${item.audio_chunk.sequence}`}
              className="rounded-xl bg-[#111319]/78 px-3 py-2 text-xs font-medium text-[#F4EFE6]/58"
            >
              <div className="flex items-center justify-between gap-3">
                <span className="font-mono text-[#F4EFE6]/34">
                  #{item.audio_chunk.sequence}
                </span>
                <span>
                  {item.transcript
                    ? `${format(item.transcript.language)} · ${percent(item.transcript.confidence)}`
                    : "Transcription error"}
                </span>
              </div>
              <div className="mt-1 text-sm font-semibold text-[#F4EFE6]/82">
                {item.transcript?.text ?? item.transcription_error ?? "No transcript"}
              </div>
              <div className="mt-1 truncate font-mono text-[0.68rem] text-[#F4EFE6]/30">
                {item.audio_chunk.audio_reference}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function ScenarioProgress({ scenario }: { scenario: ScenarioResponse | null }) {
  const steps = scenario?.timeline ?? [];
  if (!steps.length) {
    return null;
  }

  return (
    <div className="space-y-3 pt-1">
      {steps.map((step, index) => (
        <div key={`${step.transcript}-${index}`} className="flex gap-3">
          <div
            className={`mt-1 h-2.5 w-2.5 rounded-full transition-colors ${
              step.accepted_event ? "bg-[#63B77A]" : "bg-[#F4EFE6]/18"
            }`}
          />
          <div>
            <div className="text-sm font-medium text-[#F4EFE6]/82">{step.transcript}</div>
            <div className="text-xs text-[#F4EFE6]/34">
              {step.accepted_event ? `${format(step.fusion_decision)} · ${step.recommendation}` : "Ready"}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="mb-3 rounded-2xl bg-[#E14B4B]/15 px-4 py-3 text-sm font-medium text-[#FFEAEA]">
      {message}
    </div>
  );
}

function actionProfile(action: string, state: DemoState | null) {
  const value = action.toLowerCase();
  if (value.includes("shock")) {
    return {
      icon: "⚡",
      text: "text-[#FF5A5A]",
      iconSurface: "bg-[#E14B4B]/18 text-[#FFEAEA]",
      button: "bg-[#B91F2C]",
    };
  }
  if (value.includes("epinephrine") || value.includes("amiodarone") || value.includes("lidocaine")) {
    return {
      icon: "✚",
      text: "text-[#D8A536]",
      iconSurface: "bg-[#D8A536]/16 text-[#FFF4D3]",
      button: "bg-[#8F6917]",
    };
  }
  if (state?.rosc_status === "Achieved" || value.includes("post-cardiac") || value.includes("post cardiac")) {
    return {
      icon: "✓",
      text: "text-[#63B77A]",
      iconSurface: "bg-[#63B77A]/16 text-[#EAF8EE]",
      button: "bg-[#246B3B]",
    };
  }
  if (value.includes("rhythm")) {
    return {
      icon: "⏱",
      text: "text-[#F4EFE6]",
      iconSurface: "bg-[#F4EFE6]/10 text-[#F4EFE6]",
      button: "bg-[#24272F]",
    };
  }
  return {
    icon: "↻",
    text: "text-[#F4EFE6]",
    iconSurface: "bg-[#F4EFE6]/10 text-[#F4EFE6]",
    button: "bg-[#24272F]",
  };
}

function primaryStateText(state: DemoState | null, action: string) {
  if (!state) {
    return "Pulse";
  }
  if (state.rosc_status === "Achieved") {
    return "ROSC";
  }
  if (action.toLowerCase().includes("cpr") && state.cpr_status === "Active") {
    return "CPR";
  }
  if (state.current_rhythm && state.current_rhythm !== "Unknown") {
    return state.current_rhythm;
  }
  return action.toLowerCase().includes("rhythm") ? "Check" : "Pulse";
}

function clinicalStateLabel(state: DemoState | null, action: string) {
  if (!state) {
    return "Connecting";
  }
  if (state.rosc_status === "Achieved") {
    return "ROSC";
  }
  if (action.toLowerCase().includes("cpr") && state.cpr_status === "Active") {
    return "CPR active";
  }
  if (state.current_rhythm !== "Unknown") {
    return state.current_rhythm;
  }
  return "Rhythm unknown";
}

function displayAction(action: string) {
  const clean = stripPeriod(action);
  if (clean.toLowerCase() === "continue cpr") {
    return "Continue compressions";
  }
  return clean;
}

function actionDetailLines(state: DemoState | null, cprTimer: CprTimerSnapshot | null) {
  const action = state?.primary_action.toLowerCase() ?? "";
  if (!state) {
    return ["Connecting to deterministic workflow"];
  }
  if (action.includes("shock")) {
    return [`${state.current_rhythm} confirmed`, "Shockable rhythm"];
  }
  if (action.includes("cpr")) {
    const remaining = cprTimer?.remainingSeconds;
    return [
      `Cycle ${state.cpr_cycle_number || 1}`,
      remaining === undefined ? "Continue compressions" : `Rhythm check in ${clock(remaining)}`,
    ];
  }
  if (action.includes("epinephrine")) {
    return ["Medication timing due", compactMedication(state)];
  }
  if (action.includes("rhythm")) {
    return ["Pause for assessment", "Use accepted rhythm evidence"];
  }
  if (action.includes("post-cardiac") || action.includes("post cardiac")) {
    return ["ROSC confirmed", "Arrest actions suppressed"];
  }
  return [state.current_rhythm, state.current_pathway];
}

function evidenceStatus(confidence: number | null, hasConfirmation: boolean) {
  if (hasConfirmation) {
    return { label: "Confirmation required", dot: "bg-[#D8A536]" };
  }
  if (confidence !== null) {
    return { label: `Voice evidence ${percent(confidence)}`, dot: "bg-[#63B77A]" };
  }
  return { label: "Clinical state confirmed", dot: "bg-[#F4EFE6]/38" };
}

function acceptedConfidence(lastFusion: TranscriptResponse | null) {
  const event = lastFusion?.fusion_results.find(
    (result) => result.candidate_event?.status === "accepted",
  )?.candidate_event;
  return event?.confidence ?? null;
}

function matchingFusion(event: TimelineEntry, lastFusion: TranscriptResponse | null) {
  return (
    lastFusion?.fusion_results.find(
      (result) => result.candidate_event?.id === event.id,
    ) ?? null
  );
}

function evidenceForResult(result: FusionResult, responseEvidence: EvidenceSummary[]) {
  const candidateEvidence = result.candidate_event?.evidence ?? [];
  if (candidateEvidence.length) {
    return candidateEvidence;
  }
  const evidenceIds = new Set(result.evidence_ids);
  return responseEvidence.filter((item) => evidenceIds.has(item.id));
}

function maxEvidenceConfidence(evidence: EvidenceSummary[]) {
  if (!evidence.length) {
    return null;
  }
  return Math.max(...evidence.map((item) => item.confidence));
}

function payloadValue(value: unknown) {
  if (value === undefined || value === null || value === "") {
    return "--";
  }
  return format(String(value));
}

function voiceResultTone(result: FusionResult) {
  if (isNegativeEvidenceResult(result)) {
    return {
      surface: "bg-[#15181F]/80 ring-[#F4EFE6]/8",
      text: "text-[#9CA3AF]",
      badge: "bg-[#F4EFE6]/8 text-[#F4EFE6]/58",
    };
  }
  if (result.uncertainty_reason === "conflicting_evidence") {
    return {
      surface: "bg-[#2A1717]/70 ring-[#E14B4B]/24",
      text: "text-[#FFB4B4]",
      badge: "bg-[#E14B4B]/18 text-[#FFEAEA]",
    };
  }
  if (result.requires_confirmation || result.candidate_event?.status === "needs_confirmation") {
    return {
      surface: "bg-[#241C0D]/70 ring-[#D8A536]/24",
      text: "text-[#FFD889]",
      badge: "bg-[#D8A536]/16 text-[#FFF4D3]",
    };
  }
  if (result.candidate_event?.status === "accepted") {
    return {
      surface: "bg-[#102016]/75 ring-[#63B77A]/20",
      text: "text-[#A9E8B8]",
      badge: "bg-[#63B77A]/16 text-[#EAF8EE]",
    };
  }
  return {
    surface: "bg-[#15181F]/80 ring-[#F4EFE6]/8",
    text: "text-[#F4EFE6]/58",
    badge: "bg-[#F4EFE6]/8 text-[#F4EFE6]/58",
  };
}

function clinicalImpact(event: TimelineEntry, state: DemoState | null) {
  if (event.event_type === "shock_delivered") {
    return `Shock count increased to ${state?.shock_count ?? "-"}`;
  }
  if (event.event_type === "medication_given") {
    return "Medication timeline updated";
  }
  if (event.event_type === "rhythm_checked") {
    return `${state?.current_pathway ?? "Pathway"} pathway`;
  }
  if (event.event_type === "rosc_achieved") {
    return "Post-cardiac arrest care active";
  }
  if (event.event_type.includes("cpr")) {
    return `CPR ${state?.cpr_status ?? "updated"}`;
  }
  return "Clinical state updated";
}

function eventIcon(type: string) {
  if (type === "shock_delivered") {
    return "⚡";
  }
  if (type === "medication_given") {
    return "✚";
  }
  if (type === "rosc_achieved") {
    return "✓";
  }
  if (type.includes("cpr")) {
    return "↻";
  }
  return "♥";
}

function eventTitle(event: TimelineEntry) {
  if (event.event_type === "shock_delivered") {
    return "Shock delivered";
  }
  if (event.event_type === "medication_given") {
    const name = event.payload?.medication ? format(event.payload.medication) : "Medication";
    return `${name} ${event.payload?.dose ?? ""} ${event.payload?.unit ?? ""}`.trim();
  }
  if (event.event_type === "rhythm_checked") {
    return `${event.payload?.rhythm ? format(event.payload.rhythm) : event.label} confirmed`;
  }
  if (event.event_type === "rosc_achieved") {
    return "ROSC achieved";
  }
  return format(event.event_type);
}

function candidateLabel(candidate: NonNullable<FusionResult["candidate_event"]>) {
  const summary = payloadSummary(candidate.event_type, candidate.payload);
  if (summary) {
    return summary;
  }
  if (candidate.event_type === "rhythm_checked") {
    return `${candidate.payload.rhythm ?? "Rhythm"} detected`;
  }
  return format(candidate.event_type);
}

function correctionValue({
  eventType,
  rhythm,
  medication,
  dose,
  route,
}: {
  eventType: string;
  rhythm: string;
  medication: string;
  dose: string;
  route: string;
}): CorrectionFormValue | null {
  if (!eventType) {
    return null;
  }
  if (eventType === "rhythm_checked") {
    return rhythm ? { event_type: eventType, payload: { rhythm } } : null;
  }
  if (eventType === "medication_given") {
    if (!medication) {
      return null;
    }
    const payload: Record<string, unknown> = { medication };
    const numericDose = Number(dose);
    if (dose.trim() && Number.isFinite(numericDose)) {
      payload.dose = numericDose;
      payload.unit = "mg";
    }
    if (route) {
      payload.route = route;
    }
    return { event_type: eventType, payload };
  }
  return { event_type: eventType, payload: {} };
}

function compactMedication(state: DemoState | null) {
  const latest = state?.medication_history.at(-1);
  if (!latest) {
    return "Epi --";
  }
  return latest.replace("epinephrine", "Epi");
}

function fallbackSummary(state: DemoState | null) {
  if (!state) {
    return "Awaiting clinical state.";
  }
  return `${state.current_rhythm}. ${state.shock_count} shocks. Next: ${stripPeriod(state.primary_action)}.`;
}

function rhythmTone(state: DemoState | null): "red" | "green" | "amber" | "neutral" {
  const rhythm = state?.current_rhythm.toLowerCase() ?? "";
  if (rhythm === "rosc") {
    return "green";
  }
  if (rhythm === "vf" || rhythm.includes("vt")) {
    return "red";
  }
  if (rhythm === "unknown") {
    return "amber";
  }
  return "neutral";
}

function stripTone(tone: "red" | "green" | "amber" | "neutral") {
  const styles = {
    red: "bg-[#E14B4B]/14 text-[#FFEAEA]",
    green: "bg-[#63B77A]/14 text-[#EAF8EE]",
    amber: "bg-[#D8A536]/14 text-[#FFF4D3]",
    neutral: "bg-[#F4EFE6]/7 text-[#F4EFE6]/78",
  };
  return styles[tone];
}

function totalSecondsFromTimeline(timeline: TimelineEntry[], now: Date) {
  if (!timeline.length) {
    return 0;
  }
  const first = new Date(timeline[0].timestamp).getTime();
  return Math.max(0, Math.floor((now.getTime() - first) / 1000));
}

function eventTime(timestamp: string) {
  return new Date(timestamp).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function stripPeriod(value: string) {
  return value.endsWith(".") ? value.slice(0, -1) : value;
}

function percent(value: number | undefined | null) {
  if (value === undefined || value === null) {
    return "--";
  }
  return `${Math.round(value * 100)}%`;
}

function clock(totalSeconds: number) {
  const safe = Math.max(0, totalSeconds);
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function format(value: string | undefined) {
  return value
    ? value.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase())
    : "Loading";
}
