import { describe, expect, it } from "vitest";
import type { EvaluationContext } from "../types/domain.js";
import { LlmJudge, type JudgeModel } from "./llm-judge.js";

const context = { candidate: { id: "r", question: "Why?", text: "Because 42% converted.", confidence: 0.9, featureSpec: "conversion", businessContext: "growth", sql: "SELECT 0.42", evidenceIds: ["e"], prompt: { name: "p", version: "1" }, model: { provider: "x", name: "y", version: "1" }, versions: { spec: { id: "s", checksum: "a" }, schema: { id: "h", checksum: "b" }, businessContext: { id: "c", checksum: "c" } } }, currentVersions: { spec: { id: "s", checksum: "a" }, schema: { id: "h", checksum: "b" }, businessContext: { id: "c", checksum: "c" } }, evidence: [], now: "2026-08-02T00:00:00.000Z", maxEvidenceAgeMs: 1 } satisfies EvaluationContext;

describe("LlmJudge", () => {
  it("validates and records the structured judge output", async () => {
    const model: JudgeModel = { model: "judge-v1", async generate() { return { content: JSON.stringify({ score: 0.8, confidence: 0.9, reason: "Supported" }), usage: { inputTokens: 10, outputTokens: 5 } }; } };
    const result = await new LlmJudge(model, () => new Date("2026-08-02T00:00:00.000Z")).evaluate(context);
    expect(result).toMatchObject({ score: 0.8, confidence: 0.9, model: "judge-v1", promptVersion: "1" });
  });
});
