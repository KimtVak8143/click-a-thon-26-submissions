import { describe, expect, it } from "vitest";
import type { EvaluationContext } from "../types/domain.js";
import { FreshnessEvaluator, SQLValidityEvaluator } from "./deterministic.js";

const version = (id: string) => ({ id, checksum: "a".repeat(64) });
const base: EvaluationContext = {
  candidate: { id: "rec", question: "q", text: "text", confidence: 0.8, featureSpec: "spec", businessContext: "context", sql: "SELECT count() FROM events", evidenceIds: ["ev"], prompt: { name: "p", version: "1" }, model: { provider: "openai", name: "gpt", version: "1" }, versions: { spec: version("s1"), schema: version("h1"), businessContext: version("c1") } },
  currentVersions: { spec: version("s1"), schema: version("h1"), businessContext: version("c1") },
  evidence: [{ id: "ev", sql: "SELECT count() FROM events", rows: [{ count: 1 }], checksum: "b".repeat(64), executedAt: "2026-08-02T00:00:00.000Z" }],
  now: "2026-08-02T01:00:00.000Z", maxEvidenceAgeMs: 86_400_000,
};

describe("release guardrails", () => {
  it("returns STALE_CONTEXT for a version mismatch", () => {
    const context = { ...base, currentVersions: { ...base.currentVersions, schema: version("h2") } };
    expect(new FreshnessEvaluator().evaluate(context)).toMatchObject({ passed: false, reason: "STALE_CONTEXT" });
  });

  it("rejects mutating and multi-statement SQL", () => {
    const evaluator = new SQLValidityEvaluator();
    expect(evaluator.evaluate({ ...base, candidate: { ...base.candidate, sql: "DROP TABLE events" } }).passed).toBe(false);
    expect(evaluator.evaluate({ ...base, candidate: { ...base.candidate, sql: "SELECT 1; SELECT 2" } }).passed).toBe(false);
  });
});
