export type LiveVoiceClientOptions = {
  apiBase: string;
  fetchFn?: typeof fetch;
};

export type StartLiveVoiceSessionOptions = LiveVoiceClientOptions & {
  scriptName?: string;
};

export type LiveVoiceSessionActionOptions = LiveVoiceClientOptions & {
  sessionId: string;
};

export type IngestLiveTranscriptChunkOptions = LiveVoiceClientOptions & {
  chunk: Record<string, unknown>;
};

export function startLiveVoiceSession(
  options: StartLiveVoiceSessionOptions,
): Promise<unknown>;

export function stopLiveVoiceSession(
  options: LiveVoiceSessionActionOptions,
): Promise<unknown>;

export function advanceScriptedLiveVoice(
  options: LiveVoiceSessionActionOptions,
): Promise<unknown>;

export function ingestLiveTranscriptChunk(
  options: IngestLiveTranscriptChunkOptions,
): Promise<unknown>;
