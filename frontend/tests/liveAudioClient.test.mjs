import assert from "node:assert/strict";
import test from "node:test";

import {
  audioChunkReference,
  createMicrophoneCaptureController,
  ingestLiveAudioChunk,
  providerStatusLabel,
  SILENT_SEGMENT_THRESHOLD_DBFS,
  startLiveAudioSession,
  stopLiveAudioSession,
  uploadLiveAudioChunk,
} from "../lib/liveAudioClient.mjs";

test("start live audio session calls expected endpoint", async () => {
  const calls = [];
  await startLiveAudioSession({
    apiBase: "http://pulse.local",
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, async json() { return { session: { session_id: "demo-audio-1" } }; } };
    },
  });

  assert.equal(calls[0].url, "http://pulse.local/api/demo/live-audio/start");
  assert.deepEqual(JSON.parse(calls[0].options.body), { provider_name: null });
});

test("stop live audio session calls expected endpoint", async () => {
  const calls = [];
  await stopLiveAudioSession({
    apiBase: "http://pulse.local",
    sessionId: "demo-audio-1",
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, async json() { return { session: { active: false } }; } };
    },
  });

  assert.equal(calls[0].url, "http://pulse.local/api/demo/live-audio/stop");
  assert.deepEqual(JSON.parse(calls[0].options.body), { session_id: "demo-audio-1" });
});

test("ingest live audio chunk posts metadata reference", async () => {
  const calls = [];
  const chunk = {
    session_id: "demo-audio-1",
    sequence: 1,
    audio_reference: "browser-mediarecorder:now:seq-1:bytes-512",
    content_type: "audio/webm",
    duration_ms: 1500,
  };
  await ingestLiveAudioChunk({
    apiBase: "http://pulse.local",
    chunk,
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, async json() { return { audio_chunk: chunk }; } };
    },
  });

  assert.equal(calls[0].url, "http://pulse.local/api/demo/live-audio/chunks");
  assert.deepEqual(JSON.parse(calls[0].options.body), chunk);
});

test("upload live audio chunk posts FormData with blob and metadata", async () => {
  const calls = [];
  const blob = new Blob(["audio"], { type: "audio/webm" });
  await uploadLiveAudioChunk({
    apiBase: "http://pulse.local",
    sessionId: "demo-audio-1",
    sequence: 2,
    blob,
    timestamp: "2026-07-19T12:00:00.000Z",
    contentType: "audio/webm",
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        async json() {
          return { transcript: null, result: null };
        },
      };
    },
  });

  const body = calls[0].options.body;
  assert.equal(calls[0].url, "http://pulse.local/api/demo/live-audio/uploads");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(body.get("session_id"), "demo-audio-1");
  assert.equal(body.get("sequence"), "2");
  assert.equal(body.get("timestamp"), "2026-07-19T12:00:00.000Z");
  assert.equal(body.get("content_type"), "audio/webm");
  assert.equal(body.get("audio").type, "audio/webm");
});

test("audio chunk reference is deterministic from metadata", () => {
  assert.equal(
    audioChunkReference({
      sequence: 3,
      blob: { size: 2048 },
      timestamp: "2026-07-19T12:00:00.000Z",
    }),
    "browser-mediarecorder:2026-07-19T12:00:00.000Z:seq-3:bytes-2048",
  );
});

test("microphone capture controller emits standalone segment blobs with increasing sequence", async () => {
  const stopped = [];
  const stream = {
    getTracks() {
      return [{ stop() { stopped.push("track"); } }];
    },
  };
  const recorders = [];
  let getUserMediaCalls = 0;
  class MockMediaRecorder {
    static isTypeSupported(type) {
      return type === "audio/webm;codecs=opus";
    }

    constructor(inputStream, options = {}) {
      this.stream = inputStream;
      this.mimeType = options.mimeType || "audio/webm";
      this.state = "inactive";
      recorders.push(this);
    }

    start(timesliceMs) {
      this.state = "recording";
      this.timesliceMs = timesliceMs;
    }

    stop() {
      this.ondataavailable?.({
        data: new Blob([`header-${recorders.length};audio-${recorders.length}`], {
          type: this.mimeType,
        }),
      });
      this.state = "inactive";
      this.onstop?.();
    }
  }
  const chunks = [];
  const controller = createMicrophoneCaptureController({
    mediaDevices: {
      async getUserMedia(constraints) {
        getUserMediaCalls += 1;
        assert.deepEqual(constraints, { audio: true });
        return stream;
      },
    },
    MediaRecorderCtor: MockMediaRecorder,
    onChunk: (chunk) => chunks.push(chunk),
    segmentMs: 60_000,
  });

  await controller.start();
  assert.equal(controller.active, true);
  assert.equal(recorders[0].timesliceMs, undefined);
  assert.equal(recorders[0].mimeType, "audio/webm;codecs=opus");

  recorders[0].stop();
  assert.equal(recorders.length, 2);
  assert.equal(getUserMediaCalls, 1);
  assert.equal(recorders[1].stream, stream);
  assert.equal(chunks[0].sequence, 1);
  assert.equal(chunks[0].contentType, "audio/webm;codecs=opus");
  assert.equal(await chunks[0].blob.text(), "header-1;audio-1");

  recorders[1].stop();
  assert.equal(recorders.length, 3);
  assert.equal(chunks[1].sequence, 2);
  assert.equal(await chunks[1].blob.text(), "header-2;audio-2");

  controller.stop();
  assert.equal(chunks[2].sequence, 3);
  assert.equal(await chunks[2].blob.text(), "header-3;audio-3");
  assert.equal(controller.active, false);
  assert.deepEqual(stopped, ["track"]);
});

test("microphone capture controller stop flushes the in-flight segment", async () => {
  const stream = {
    getTracks() {
      return [{ stop() {} }];
    },
  };
  const recorders = [];
  class MockMediaRecorder {
    constructor() {
      this.state = "inactive";
      this.mimeType = "audio/webm";
      recorders.push(this);
    }

    start(timesliceMs) {
      this.timesliceMs = timesliceMs;
      this.state = "recording";
    }

    stop() {
      this.ondataavailable?.({
        data: new Blob(["final segment"], { type: this.mimeType }),
      });
      this.state = "inactive";
      this.onstop?.();
    }
  }
  const chunks = [];
  const controller = createMicrophoneCaptureController({
    mediaDevices: { async getUserMedia() { return stream; } },
    MediaRecorderCtor: MockMediaRecorder,
    onChunk: (chunk) => chunks.push(chunk),
    segmentMs: 60_000,
  });

  await controller.start();
  controller.stop();

  assert.equal(recorders[0].timesliceMs, undefined);
  assert.equal(chunks.length, 1);
  assert.equal(chunks[0].sequence, 1);
  assert.equal(await chunks[0].blob.text(), "final segment");
});

test("microphone capture controller selects mp4 fallback when webm is unsupported", async () => {
  const stream = { getTracks() { return [{ stop() {} }]; } };
  const recorders = [];
  class MockMediaRecorder {
    static isTypeSupported(type) {
      return type === "audio/mp4";
    }

    constructor(inputStream, options = {}) {
      this.state = "inactive";
      this.mimeType = options.mimeType;
      recorders.push(this);
    }

    start() {
      this.state = "recording";
    }

    stop() {
      this.ondataavailable?.({
        data: new Blob(["mp4 segment"], { type: this.mimeType }),
      });
      this.state = "inactive";
      this.onstop?.();
    }
  }
  const chunks = [];
  const controller = createMicrophoneCaptureController({
    mediaDevices: { async getUserMedia() { return stream; } },
    MediaRecorderCtor: MockMediaRecorder,
    onChunk: (chunk) => chunks.push(chunk),
    segmentMs: 60_000,
  });

  await controller.start();
  controller.stop();

  assert.equal(recorders[0].mimeType, "audio/mp4");
  assert.equal(chunks[0].contentType, "audio/mp4");
});

test("microphone capture controller skips below-threshold silent segments", async () => {
  const recorders = [];
  class MockMediaRecorder {
    constructor(inputStream, options = {}) {
      this.state = "inactive";
      this.mimeType = options.mimeType || "audio/webm";
      recorders.push(this);
    }

    start() {
      this.state = "recording";
    }

    stop() {
      this.ondataavailable?.({
        data: new Blob(["silent segment"], { type: this.mimeType }),
      });
      this.state = "inactive";
      this.onstop?.();
    }
  }
  const chunks = [];
  const controller = createMicrophoneCaptureController({
    mediaDevices: { async getUserMedia() { return fakeStream(); } },
    MediaRecorderCtor: MockMediaRecorder,
    AudioContextCtor: fakeAudioContextForLevels(["silent", "silent"], () => recorders.length - 1),
    onChunk: (chunk) => chunks.push(chunk),
    segmentMs: 60_000,
  });

  await controller.start();
  recorders[0].stop();
  controller.stop();

  assert.equal(SILENT_SEGMENT_THRESHOLD_DBFS, -40);
  assert.equal(chunks.length, 0);
});

test("microphone capture controller uploads above-threshold speech segments", async () => {
  const recorders = [];
  class MockMediaRecorder {
    constructor(inputStream, options = {}) {
      this.state = "inactive";
      this.mimeType = options.mimeType || "audio/webm";
      recorders.push(this);
    }

    start() {
      this.state = "recording";
    }

    stop() {
      this.ondataavailable?.({
        data: new Blob(["speech segment"], { type: this.mimeType }),
      });
      this.state = "inactive";
      this.onstop?.();
    }
  }
  const chunks = [];
  const controller = createMicrophoneCaptureController({
    mediaDevices: { async getUserMedia() { return fakeStream(); } },
    MediaRecorderCtor: MockMediaRecorder,
    AudioContextCtor: fakeAudioContextForLevels(["speech", "silent"], () => recorders.length - 1),
    onChunk: (chunk) => chunks.push(chunk),
    segmentMs: 60_000,
  });

  await controller.start();
  recorders[0].stop();
  controller.stop();

  assert.equal(chunks.length, 1);
  assert.equal(chunks[0].sequence, 1);
  assert.equal(await chunks[0].blob.text(), "speech segment");
});

test("microphone capture controller keeps sequences contiguous across skipped segments", async () => {
  const recorders = [];
  class MockMediaRecorder {
    constructor(inputStream, options = {}) {
      this.state = "inactive";
      this.mimeType = options.mimeType || "audio/webm";
      recorders.push(this);
    }

    start() {
      this.state = "recording";
    }

    stop() {
      this.ondataavailable?.({
        data: new Blob([`segment-${recorders.indexOf(this) + 1}`], {
          type: this.mimeType,
        }),
      });
      this.state = "inactive";
      this.onstop?.();
    }
  }
  const chunks = [];
  const controller = createMicrophoneCaptureController({
    mediaDevices: { async getUserMedia() { return fakeStream(); } },
    MediaRecorderCtor: MockMediaRecorder,
    AudioContextCtor: fakeAudioContextForLevels(
      ["silent", "speech", "silent", "speech", "silent"],
      () => recorders.length - 1,
    ),
    onChunk: (chunk) => chunks.push(chunk),
    segmentMs: 60_000,
  });

  await controller.start();
  recorders[0].stop();
  recorders[1].stop();
  recorders[2].stop();
  recorders[3].stop();
  controller.stop();

  assert.deepEqual(chunks.map((chunk) => chunk.sequence), [1, 2]);
  assert.equal(await chunks[0].blob.text(), "segment-2");
  assert.equal(await chunks[1].blob.text(), "segment-4");
});

test("microphone capture controller fails safely when browser APIs are missing", async () => {
  const controller = createMicrophoneCaptureController({
    mediaDevices: undefined,
    MediaRecorderCtor: undefined,
    onChunk: () => {},
  });

  await assert.rejects(
    () => controller.start(),
    /Microphone capture is not available/,
  );
});

test("provider status renders fake configured and error states", () => {
  assert.equal(providerStatusLabel(null), "Provider not started");
  assert.equal(
    providerStatusLabel({
      provider_name: "fake",
      provider_mode: "fake/demo",
    }),
    "Fake/demo ASR",
  );
  assert.equal(
    providerStatusLabel({
      provider_name: "openai",
      provider_mode: "configured_real_provider",
    }),
    "Configured ASR: Openai",
  );
  assert.equal(
    providerStatusLabel({
      provider_name: "openai",
      provider_mode: "provider_error_fallback",
      fallback_provider_name: "fake",
    }),
    "Provider error · using Fake",
  );
});

test("provider status tolerates absent provider metadata", () => {
  assert.equal(providerStatusLabel({}), "ASR Provider");
});

function fakeStream() {
  return {
    getTracks() {
      return [{ stop() {} }];
    },
  };
}

function fakeAudioContextForLevels(levels, getSegmentIndex) {
  return class FakeAudioContext {
    constructor() {
      this.state = "running";
    }

    createMediaStreamSource() {
      return {
        connect() {},
        disconnect() {},
      };
    }

    createAnalyser() {
      return {
        fftSize: 8,
        frequencyBinCount: 8,
        disconnect() {},
        getByteTimeDomainData(samples) {
          const level = levels[Math.max(0, getSegmentIndex())] || "speech";
          fillAudioSamples(samples, level === "silent" ? 0 : 8);
        },
      };
    }

    async resume() {}

    close() {}
  };
}

function fillAudioSamples(samples, amplitude) {
  for (let index = 0; index < samples.length; index += 1) {
    samples[index] = 128 + (index % 2 === 0 ? amplitude : -amplitude);
  }
}
