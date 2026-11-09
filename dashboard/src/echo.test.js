import assert from "node:assert/strict";
import test from "node:test";
import { looksLikeEcho } from "./echo.js";

test("echo guard: Saathi's own reply heard back is ignored, a real answer is not", () => {
  const reply = "Aapko kitne ka loan chahiye? Yeh sirf pre-screening hai.";
  assert.equal(looksLikeEcho("aapko kitne ka loan chahiye", reply), true);
  assert.equal(looksLikeEcho("mujhe teen lakh chahiye", reply), false);
  assert.equal(looksLikeEcho("haan", reply), false);
  assert.equal(looksLikeEcho("aapko kitne ka loan chahiye", ""), false);
});
