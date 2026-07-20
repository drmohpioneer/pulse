import assert from "node:assert/strict";
import test from "node:test";

import { cprTimerSnapshot } from "../lib/cprTimer.mjs";

test("CPR timer starts at 0 seconds with 0% elapsed progress", () => {
  assert.deepEqual(cprTimerSnapshot(0), {
    elapsedSeconds: 0,
    remainingSeconds: 120,
    progressPercentage: 0,
    rhythmCheckDue: false,
  });
});

test("CPR timer at 60 seconds shows 50% elapsed progress", () => {
  assert.deepEqual(cprTimerSnapshot(60), {
    elapsedSeconds: 60,
    remainingSeconds: 60,
    progressPercentage: 50,
    rhythmCheckDue: false,
  });
});

test("CPR timer at 96 seconds shows 80% elapsed progress", () => {
  assert.deepEqual(cprTimerSnapshot(96), {
    elapsedSeconds: 96,
    remainingSeconds: 24,
    progressPercentage: 80,
    rhythmCheckDue: false,
  });
});

test("CPR timer at 120 seconds shows 100% elapsed progress and rhythm check due", () => {
  assert.deepEqual(cprTimerSnapshot(120), {
    elapsedSeconds: 120,
    remainingSeconds: 0,
    progressPercentage: 100,
    rhythmCheckDue: true,
  });
});
