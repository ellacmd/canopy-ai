import { expect, test } from "@playwright/test";
import { VoiceTurn } from "../../src/lib/voice-turn";

test("a tentative interruption restores playback when speech evidence disappears", () => {
  const turn = new VoiceTurn();
  const decisions = [0.93, 0.94, 0.01, 0.01].map((score) => turn.observe(score, true, ""));
  expect(decisions).toEqual([undefined, "duck", undefined, "resume"]);
  expect(turn.speaking).toBe(false);
});

test("a confirmed speaker can pause after an unfinished phrase", () => {
  const turn = new VoiceTurn();
  for (let position = 0; position < 5; position += 1) turn.observe(0.95, true, "");
  expect(turn.observe(0.95, true, "")).toBe("start");
  for (let position = 0; position < 25; position += 1) {
    expect(turn.observe(0.01, false, "My order number is")).toBeUndefined();
  }
  expect(turn.observe(0.95, false, "AMZ-1024")).toBeUndefined();
  for (let position = 0; position < 20; position += 1) {
    expect(turn.observe(0.01, false, "AMZ-1024")).toBeUndefined();
  }
  expect(turn.observe(0.01, false, "AMZ-1024")).toBe("end");
});
