import { readFile } from "node:fs/promises";
import { test } from "node:test";
import assert from "node:assert/strict";

const source = await readFile(new URL("../app/static/interview-vision.js", import.meta.url));
const { poseObservations } = await import(`data:text/javascript;base64,${source.toString("base64")}`);
const sample = (time, center = 0.5, tilt = 0, hands = [{ x: 0, y: 0 }, null]) => ({ time, center, width: 0.2, tilt, hands });

test("insufficient visibility is not treated as an observation", () => {
  const report = poseObservations([sample(0), sample(1)], 10);
  assert.equal(report.visible, 2);
  assert.deepEqual(report.events, []);
});

test("body shifts are timestamped without inventing hand motion", () => {
  const report = poseObservations([sample(0), sample(1), sample(2, 0.65), sample(3, 0.65)], 4);
  assert.equal(report.events.length, 1);
  assert.match(report.events[0].text, /upper body shifted/);
  assert.equal(report.events[0].time, 1);
});

test("gaps do not produce movement observations", () => {
  const report = poseObservations([sample(0), sample(4, 0.8), sample(5, 0.8)], 5);
  assert.deepEqual(report.events, []);
});

test("sustained tilt and separate hand motion produce reviewable observations", () => {
  const report = poseObservations([sample(0, 0.5, 20), sample(1, 0.5, 20),
    sample(2, 0.5, 20, [{ x: 0.8, y: 0 }, null])], 3);
  assert.equal(report.events.length, 2);
  assert.ok(report.events.some((e) => e.text.includes("Shoulder line")));
  assert.ok(report.events.some((e) => e.text.includes("hand moved")));
});
