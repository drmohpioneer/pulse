export async function startLiveVoiceSession({
  apiBase,
  scriptName = "v0.4-demo",
  fetchFn = fetch,
}) {
  return postJson(fetchFn, `${apiBase}/api/demo/live/start`, {
    script_name: scriptName,
  }, "Unable to start live voice demo.");
}

export async function stopLiveVoiceSession({
  apiBase,
  sessionId,
  fetchFn = fetch,
}) {
  return postJson(fetchFn, `${apiBase}/api/demo/live/stop`, {
    session_id: sessionId,
  }, "Unable to stop live voice demo.");
}

export async function advanceScriptedLiveVoice({
  apiBase,
  sessionId,
  fetchFn = fetch,
}) {
  return postJson(fetchFn, `${apiBase}/api/demo/live/scripted/next`, {
    session_id: sessionId,
  }, "Unable to advance live voice demo.");
}

export async function ingestLiveTranscriptChunk({
  apiBase,
  chunk,
  fetchFn = fetch,
}) {
  return postJson(fetchFn, `${apiBase}/api/demo/live/chunks`, chunk, "Unable to ingest live transcript chunk.");
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
