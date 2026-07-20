export async function startLiveAudioSession({
  apiBase,
  providerName = null,
  fetchFn = fetch,
}) {
  return postJson(fetchFn, `${apiBase}/api/demo/live-audio/start`, {
    provider_name: providerName,
  }, "Unable to start live audio session.");
}

export async function stopLiveAudioSession({
  apiBase,
  sessionId,
  fetchFn = fetch,
}) {
  return postJson(fetchFn, `${apiBase}/api/demo/live-audio/stop`, {
    session_id: sessionId,
  }, "Unable to stop live audio session.");
}

export async function ingestLiveAudioChunk({
  apiBase,
  chunk,
  fetchFn = fetch,
}) {
  return postJson(fetchFn, `${apiBase}/api/demo/live-audio/chunks`, chunk, "Unable to ingest live audio chunk.");
}

export async function uploadLiveAudioChunk({
  apiBase,
  sessionId,
  sequence,
  blob,
  timestamp,
  contentType,
  fetchFn = fetch,
}) {
  const form = new FormData();
  form.append("session_id", sessionId);
  form.append("sequence", String(sequence));
  form.append("timestamp", timestamp);
  form.append("content_type", contentType || blob?.type || "audio/webm");
  form.append("audio", blob, `pulse-${sequence}.${extensionForContentType(contentType || blob?.type)}`);
  const response = await fetchFn(`${apiBase}/api/demo/live-audio/uploads`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    throw new Error("Unable to upload live audio chunk.");
  }
  return response.json();
}

export function audioChunkReference({ sequence, blob, timestamp }) {
  const size = typeof blob?.size === "number" ? blob.size : 0;
  return `browser-mediarecorder:${timestamp}:seq-${sequence}:bytes-${size}`;
}

export function providerStatusLabel(session) {
  if (!session) {
    return "Provider not started";
  }
  if (session.provider_mode === "fake/demo") {
    return "Fake/demo ASR";
  }
  if (session.provider_mode === "configured_real_provider") {
    return `Configured ASR: ${formatLabel(session.provider_name)}`;
  }
  if (session.provider_mode === "provider_error_fallback") {
    return `Provider error · using ${formatLabel(session.fallback_provider_name || "fake")}`;
  }
  if (session.provider_error) {
    return "Provider error";
  }
  return formatLabel(session.provider_name || "ASR provider");
}

export const SILENT_SEGMENT_THRESHOLD_DBFS = -40;
const LOUDNESS_SAMPLE_INTERVAL_MS = 250;

export function createMicrophoneCaptureController({
  mediaDevices,
  MediaRecorderCtor,
  AudioContextCtor = globalThis.AudioContext || globalThis.webkitAudioContext,
  onChunk,
  onError,
  segmentMs = 4000,
  silenceThresholdDbfs = SILENT_SEGMENT_THRESHOLD_DBFS,
}) {
  let stream = null;
  let recorder = null;
  let audioContext = null;
  let audioSource = null;
  let analyser = null;
  let active = false;
  let sequence = 1;
  let segmentTimer = null;
  let loudnessTimer = null;
  let stopping = false;
  let currentSegmentParts = [];
  let currentSegmentRmsSamples = [];

  return {
    get active() {
      return active;
    },
    async start() {
      if (active) {
        return;
      }
      if (!mediaDevices?.getUserMedia || !MediaRecorderCtor) {
        throw new Error("Microphone capture is not available in this browser.");
      }
      stream = await mediaDevices.getUserMedia({ audio: true });
      await setupAudioAnalysis();
      active = true;
      stopping = false;
      startSegment();
    },
    stop() {
      if (!active) {
        return;
      }
      stopping = true;
      active = false;
      clearSegmentTimer();
      if (recorder && recorder.state !== "inactive") {
        recorder.stop();
      } else {
        stopStream();
      }
    },
  };

  function startSegment() {
    if (!active || !stream) {
      return;
    }
    currentSegmentParts = [];
    currentSegmentRmsSamples = [];
    const mimeType = preferredRecordingMimeType(MediaRecorderCtor);
    recorder = mimeType
      ? new MediaRecorderCtor(stream, { mimeType })
      : new MediaRecorderCtor(stream);
    recorder.ondataavailable = (event) => {
      if (event?.data && event.data.size > 0) {
        currentSegmentParts.push(event.data);
      }
    };
    recorder.onerror = (event) => {
      onError?.(event?.error instanceof Error ? event.error : new Error("Microphone recording failed."));
    };
    recorder.onstop = () => {
      sampleSegmentLoudness();
      clearSegmentTimer();
      clearLoudnessTimer();
      const type = recorder?.mimeType || mimeType || currentSegmentParts[0]?.type || "audio/webm";
      if (currentSegmentParts.length) {
        const segmentDbfs = currentSegmentDbfs();
        if (!isSilentSegment(segmentDbfs)) {
          const timestamp = new Date().toISOString();
          const blob = new Blob(currentSegmentParts, { type });
          onChunk({
            sequence: sequence++,
            blob,
            timestamp,
            contentType: type,
          });
        }
      }
      recorder = null;
      currentSegmentParts = [];
      currentSegmentRmsSamples = [];
      if (stopping) {
        stopStream();
      } else if (active) {
        startSegment();
      }
    };
    recorder.start();
    sampleSegmentLoudness();
    loudnessTimer = setInterval(sampleSegmentLoudness, LOUDNESS_SAMPLE_INTERVAL_MS);
    segmentTimer = setTimeout(() => {
      if (recorder && recorder.state !== "inactive") {
        recorder.stop();
      }
    }, segmentMs);
  }

  function clearSegmentTimer() {
    if (segmentTimer) {
      clearTimeout(segmentTimer);
      segmentTimer = null;
    }
  }

  function clearLoudnessTimer() {
    if (loudnessTimer) {
      clearInterval(loudnessTimer);
      loudnessTimer = null;
    }
  }

  async function setupAudioAnalysis() {
    if (!AudioContextCtor || !stream) {
      return;
    }
    try {
      audioContext = new AudioContextCtor();
      audioSource = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 2048;
      audioSource.connect(analyser);
      if (audioContext.state === "suspended" && typeof audioContext.resume === "function") {
        await audioContext.resume();
      }
    } catch (error) {
      audioContext = null;
      audioSource = null;
      analyser = null;
      onError?.(error instanceof Error ? error : new Error("Microphone loudness analysis failed."));
    }
  }

  function sampleSegmentLoudness() {
    if (!analyser) {
      return;
    }
    const size = analyser.fftSize || analyser.frequencyBinCount;
    if (!size) {
      return;
    }
    const samples = new Uint8Array(size);
    analyser.getByteTimeDomainData(samples);
    let sumSquares = 0;
    for (const sample of samples) {
      const centered = (sample - 128) / 128;
      sumSquares += centered * centered;
    }
    currentSegmentRmsSamples.push(Math.sqrt(sumSquares / samples.length));
  }

  function currentSegmentDbfs() {
    if (!currentSegmentRmsSamples.length) {
      return null;
    }
    const meanRms = currentSegmentRmsSamples.reduce((sum, rms) => sum + rms, 0) / currentSegmentRmsSamples.length;
    if (meanRms <= 0) {
      return -Infinity;
    }
    return 20 * Math.log10(meanRms);
  }

  function isSilentSegment(segmentDbfs) {
    return typeof segmentDbfs === "number" && segmentDbfs < silenceThresholdDbfs;
  }

  function stopStream() {
    clearSegmentTimer();
    clearLoudnessTimer();
    audioSource?.disconnect?.();
    analyser?.disconnect?.();
    audioContext?.close?.();
    for (const track of stream?.getTracks?.() ?? []) {
      track.stop();
    }
    recorder = null;
    stream = null;
    audioContext = null;
    audioSource = null;
    analyser = null;
    currentSegmentParts = [];
    currentSegmentRmsSamples = [];
  }
}

async function postJson(fetchFn, url, payload, errorMessage) {
  const response = await fetchFn(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(errorMessage);
  }
  return response.json();
}

function formatLabel(value) {
  return String(value)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function extensionForContentType(contentType = "") {
  if (contentType.includes("wav")) {
    return "wav";
  }
  if (contentType.includes("mp4") || contentType.includes("m4a")) {
    return "m4a";
  }
  if (contentType.includes("mpeg") || contentType.includes("mp3")) {
    return "mp3";
  }
  return "webm";
}

function preferredRecordingMimeType(MediaRecorderCtor) {
  const supported = MediaRecorderCtor?.isTypeSupported;
  if (typeof supported !== "function") {
    return "";
  }
  if (supported("audio/webm;codecs=opus")) {
    return "audio/webm;codecs=opus";
  }
  if (supported("audio/webm")) {
    return "audio/webm";
  }
  if (supported("audio/mp4")) {
    return "audio/mp4";
  }
  return "";
}
