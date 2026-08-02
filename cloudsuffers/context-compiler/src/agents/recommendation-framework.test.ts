import { describe, expect, it } from "vitest";
import { defaultEvaluators } from "../evaluators/deterministic.js";
import { EvaluationEngine } from "../evaluators/engine.js";
import { InMemoryScorePublisher } from "../langfuse/in-memory.js";
import { InMemoryProvenanceRepository } from "../provenance/repository.js";
import { ProvenanceService } from "../provenance/service.js";
import { InMemoryTraceService } from "../tracing/in-memory.js";
import type { EvaluationContext } from "../types/domain.js";
import type { Logger } from "../utils/logger.js";
import { RecommendationFramework } from "./recommendation-framework.js";

const logger: Logger = { debug() {}, info() {}, warn() {}, error() {} };
const v = (id: string) => ({ id, checksum: "a".repeat(64) });
const context: EvaluationContext = {
  candidate: {
    id: "rec-1", question: "How should checkout improve?",
    text: "Recommend testing checkout because conversion is 42%.", confidence: 0.9,
    featureSpec: "Improve checkout conversion with controlled experiments.", businessContext: "Prioritize checkout conversion growth experiments.",
    sql: "SELECT conversion_rate FROM analytics_events", evidenceIds: ["ev-1"],
    prompt: { name: "recommendation", version: "7" }, model: { provider: "openai", name: "gpt-5", version: "2026-08" },
    versions: { spec: v("spec-1"), schema: v("schema-1"), businessContext: v("context-1") }, allowedSchema: { analytics_events: ["conversion_rate"] },
  },
  currentVersions: { spec: v("spec-1"), schema: v("schema-1"), businessContext: v("context-1") },
  evidence: [{ id: "ev-1", sql: "SELECT conversion_rate FROM analytics_events", rows: [{ conversion_rate: 0.42 }], checksum: "e".repeat(64), executedAt: "2026-08-02T00:00:00.000Z" }],
  now: "2026-08-02T01:00:00.000Z", maxEvidenceAgeMs: 86_400_000,
};

describe("RecommendationFramework", () => {
  it("approves, traces, scores, and persists a fully grounded recommendation", async () => {
    const tracing = new InMemoryTraceService();
    const scores = new InMemoryScorePublisher();
    const repository = new InMemoryProvenanceRepository();
    const framework = new RecommendationFramework(tracing, new EvaluationEngine(defaultEvaluators()), new ProvenanceService(() => new Date(context.now)), repository, scores, logger);
    const decision = await framework.evaluate(context);
    expect(decision.status).toBe("APPROVED");
    expect(decision.provenance.traceId).toHaveLength(32);
    expect(await repository.find("rec-1")).toEqual(decision.provenance);
    expect(scores.published).toHaveLength(1);
    expect(tracing.observations.map(({ name }) => name)).toEqual(expect.arrayContaining(["evaluate.freshness", "evaluate.groundedness", "persist-provenance", "publish-langfuse-scores"]));
  });

  it("blocks an unsupported number and still persists provenance", async () => {
    const repository = new InMemoryProvenanceRepository();
    const framework = new RecommendationFramework(new InMemoryTraceService(), new EvaluationEngine(defaultEvaluators()), new ProvenanceService(), repository, new InMemoryScorePublisher(), logger);
    const decision = await framework.evaluate({ ...context, candidate: { ...context.candidate, text: "Recommend testing checkout because conversion is 99%." } });
    expect(decision.status).toBe("BLOCKED_UNSUPPORTED_EVIDENCE");
    expect((await repository.find("rec-1"))?.status).toBe("BLOCKED_UNSUPPORTED_EVIDENCE");
  });
});
