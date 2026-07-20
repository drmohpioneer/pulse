import assert from "node:assert/strict";
import test from "node:test";

import {
  advanceScriptedLiveVoice,
  ingestLiveTranscriptChunk,
  startLiveVoiceSession,
  stopLiveVoiceSession,
} from "../lib/liveVoiceClient.mjs";

test("start live voice session calls expected endpoint", async () => {
  const calls = [];
  await startLiveVoiceSession({
    apiBase: "http://pulse.local",
    scriptName: "v0.4-demo",
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, async json() { return { session: { session_id: "demo-live-1" } }; } };
    },
  });

  assert.equal(calls[0].url, "http://pulse.local/api/demo/live/start");
  assert.deepEqual(JSON.parse(calls[0].options.body), { script_name: "v0.4-demo" });
});

test("stop live voice session calls expected endpoint", async () => {
  const calls = [];
  await stopLiveVoiceSession({
    apiBase: "http://pulse.local",
    sessionId: "demo-live-1",
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, async json() { return { session: { active: false } }; } };
    },
  });

  assert.equal(calls[0].url, "http://pulse.local/api/demo/live/stop");
  assert.deepEqual(JSON.parse(calls[0].options.body), { session_id: "demo-live-1" });
});

test("advance scripted live voice calls expected endpoint", async () => {
  const calls = [];
  await advanceScriptedLiveVoice({
    apiBase: "http://pulse.local",
    sessionId: "demo-live-1",
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, async json() { return { is_complete: false }; } };
    },
  });

  assert.equal(calls[0].url, "http://pulse.local/api/demo/live/scripted/next");
});

test("ingest live transcript chunk posts the chunk payload", async () => {
  const calls = [];
  const chunk = {
    session_id: "demo-live-1",
    sequence: 1,
    text: "Rhythm is VF.",
    confidence: 0.95,
  };
  await ingestLiveTranscriptChunk({
    apiBase: "http://pulse.local",
    chunk,
    fetchFn: async (url, options) => {
      calls.push({ url, options });
      return { ok: true, async json() { return { chunk }; } };
    },
  });

  assert.equal(calls[0].url, "http://pulse.local/api/demo/live/chunks");
  assert.deepEqual(JSON.parse(calls[0].options.body), chunk);
});
