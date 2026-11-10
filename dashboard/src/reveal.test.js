import assert from "node:assert/strict";
import test from "node:test";
import { inView } from "./reveal.js";

test("reveal: on screen means visible; well above or below the screen means hidden", () => {
  assert.equal(inView({ top: 100, bottom: 400 }, 800), true);
  assert.equal(inView({ top: -300, bottom: 200 }, 800), true, "partly scrolled past is still shown");
  assert.equal(inView({ top: 900, bottom: 1200 }, 800), false, "below the fold waits for the scroll");
  assert.equal(inView({ top: -600, bottom: -100 }, 800), false, "scrolled away fades out");
});
