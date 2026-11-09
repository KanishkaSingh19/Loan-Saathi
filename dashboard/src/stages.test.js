import assert from "node:assert/strict";
import { test } from "node:test";
import { currentStage, STAGES, stageStatus } from "./stages.js";

test("the pipeline is LISTEN -> EXTRACT -> VALIDATE -> CONFIRM -> ASSESS -> EXPLAIN -> SIMULATE -> HANDOFF", () => {
  assert.deepEqual(STAGES.map((s) => s.id),
    ["LISTEN", "EXTRACT", "VALIDATE", "CONFIRM", "ASSESS", "EXPLAIN", "SIMULATE", "HANDOFF"]);
  assert.ok(STAGES.every((s) => s.caption.length > 10));
});

test("the screen moves ahead of the server while recording or working", () => {
  assert.equal(currentStage("EXTRACT", "listening"), "LISTEN");
  assert.equal(currentStage("EXTRACT", "transcribing"), "LISTEN");
  assert.equal(currentStage("LISTEN", "thinking"), "EXTRACT");
  assert.equal(currentStage("CONFIRM", "thinking"), "CONFIRM"); // confirming: the server decides
  assert.equal(currentStage("EXPLAIN", null), "EXPLAIN");
  assert.equal(currentStage(undefined, null), "LISTEN");
});

test("stages before the current one are done", () => {
  const s = stageStatus("CONFIRM");
  assert.deepEqual(s.map((x) => x.status), ["done", "done", "done", "now", "next", "next", "next", "next"]);
});
