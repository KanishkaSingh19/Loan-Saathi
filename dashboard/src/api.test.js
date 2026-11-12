// Run with: node --test src/api.test.js
import test from "node:test";
import assert from "node:assert/strict";
import { apiBase, wakeServer } from "./api.js";

test("wakeServer pings /health without CORS and never throws", async () => {
  const calls = [];
  await wakeServer((url, opts) => { calls.push([url, opts]); return Promise.resolve({ ok: true }); });
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], `${apiBase()}/health`);
  assert.equal(calls[0][1].mode, "no-cors");
});

test("wakeServer swallows network errors and synchronous throws", async () => {
  assert.equal(await wakeServer(() => Promise.reject(new Error("offline"))), null);
  assert.equal(await wakeServer(() => { throw new Error("no fetch"); }), null);
});
