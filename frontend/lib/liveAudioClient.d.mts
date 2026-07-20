export type LiveAudioSessionResponse = {
  session: {
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
  chunks: Array<{
    session_id: string;
    sequence: number;
    audio_reference: string;
    content_type: string | null;
    duration_ms: number | null;
    timestamp: string;
    sample_rate_hz: number | null;
    channel_count: number | null;
    metadata: Record<string, unknown>;
  }>;
  state: unknown;
};

export type LiveAudioChunkResponse = {
  session: LiveAudioSessionResponse["session"];
  audio_chunk: LiveAudioSessionResponse["chunks"][number];
  transcript: {
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
  } | null;
  result: unknown | null;
  transcription_error: string | null;
};

export type MicrophoneChunk = {
  sequence: number;
  blob: Blob;
  timestamp: string;
  contentType: string;
};

export function startLiveAudioSession(args: {
  apiBase: string;
  providerName?: string | null;
  fetchFn?: typeof fetch;
}): Promise<LiveAudioSessionResponse>;

export function stopLiveAudioSession(args: {
  apiBase: string;
  sessionId: string;
  fetchFn?: typeof fetch;
}): Promise<LiveAudioSessionResponse>;

export function ingestLiveAudioChunk(args: {
  apiBase: string;
  chunk: Record<string, unknown>;
  fetchFn?: typeof fetch;
}): Promise<LiveAudioChunkResponse>;

export function uploadLiveAudioChunk(args: {
  apiBase: string;
  sessionId: string;
  sequence: number;
  blob: Blob;
  timestamp: string;
  contentType?: string;
  fetchFn?: typeof fetch;
}): Promise<LiveAudioChunkResponse>;

export function audioChunkReference(args: {
  sequence: number;
  blob: { size?: number };
  timestamp: string;
}): string;

export function providerStatusLabel(session?: LiveAudioSessionResponse["session"] | null): string;

export const SILENT_SEGMENT_THRESHOLD_DBFS: number;

export function createMicrophoneCaptureController(args: {
  mediaDevices: MediaDevices | undefined;
  MediaRecorderCtor: typeof MediaRecorder | undefined;
  AudioContextCtor?: typeof AudioContext;
  onChunk: (chunk: MicrophoneChunk) => void;
  onError?: (error: Error) => void;
  segmentMs?: number;
  silenceThresholdDbfs?: number;
}): {
  readonly active: boolean;
  start(): Promise<void>;
  stop(): void;
};
