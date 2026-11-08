import test from "node:test";
import assert from "node:assert/strict";
import { phaseTitle, stepState, providerName } from "./components/AnalysisProgress.js";

const STEP = (id, who = "ai") => ({ id, who });

test("titles name the real model and the real phase", () => {
  const p = { phase: "score", mode: "model", model: "gemini/gemini-3.5-flash-lite" };
  assert.match(phaseTitle(p), /^Gemini is judging each moment's opening hook and cliffhanger/);
  assert.match(phaseTitle({ ...p, phase: "clarity" }), /Gemini is checking: would a stranger understand/);
  assert.match(phaseTitle({ ...p, phase: "rerank" }), /comparing the best clips/);
  assert.match(phaseTitle({ phase: "candidates", mode: "rules", model: "" }), /20–60 second moment/);
  assert.match(phaseTitle({ phase: "queued", mode: "rules", model: "" }), /Waiting/);
  assert.equal(providerName("openai/gpt"), "Openai");
  assert.equal(providerName(""), "The model");
});

test("steps are done before the current phase, active on it, pending after", () => {
  const p = { phase: "clarity", mode: "model" };
  assert.equal(stepState(STEP("reading", "code"), p), "done");
  assert.equal(stepState(STEP("candidates", "code"), p), "done");
  assert.equal(stepState(STEP("score"), p), "done");
  assert.equal(stepState(STEP("clarity"), p), "active");
  assert.equal(stepState(STEP("rerank"), p), "pending");
  assert.equal(stepState(STEP("write", "code"), p), "pending");
});

test("rules-only runs mark the AI steps as skipped, not pending", () => {
  const p = { phase: "candidates", mode: "rules" };
  assert.equal(stepState(STEP("score"), p), "skipped");
  assert.equal(stepState(STEP("clarity"), p), "skipped");
  assert.equal(stepState(STEP("candidates", "code"), p), "active");
  assert.equal(stepState(STEP("write", "code"), p), "pending");
});

test("the queued phase has nothing done or active yet", () => {
  const p = { phase: "queued", mode: "model" };
  assert.equal(stepState(STEP("reading", "code"), p), "pending");
});
