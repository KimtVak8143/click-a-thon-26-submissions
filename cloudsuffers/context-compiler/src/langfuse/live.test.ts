import { readFileSync } from "node:fs";
import { LangfuseClient } from "@langfuse/client";
import { describe, expect, it } from "vitest";
import type { EvaluationResult, JudgeResult } from "../types/domain.js";
import type { Logger } from "../utils/logger.js";
import { LangfuseScorePublisher } from "./score-publisher.js";
import { createLangfuseTracing, withTraceAttributes } from "../tracing/langfuse.js";

const live = process.env["RUN_LANGFUSE_LIVE_TEST"] === "1";
const logger: Logger = { debug() {}, info() {}, warn() {}, error() {} };

function env(name: string): string {
  if (process.env[name]) return process.env[name]!;
  const aliases = [name, `CONTEXT_COMPILER_${name}`];
  const content = readFileSync(".env", "utf8");
  for (const line of content.split(/\r?\n/)) {
    const separator = line.indexOf("=");
    if (separator < 1) continue;
    const key = line.slice(0, separator).trim();
    if (aliases.includes(key)) return line.slice(separator + 1).trim().replace(/^['"]|['"]$/g, "");
  }
  throw new Error(`Missing ${name} for live integration test`);
}

describe.skipIf(!live)("live Langfuse v5 integration", () => {
  it("exports a semantic trace and all required scores", async () => {
    const config = { publicKey: env("LANGFUSE_PUBLIC_KEY"), secretKey: env("LANGFUSE_SECRET_KEY"), baseUrl: env("LANGFUSE_BASE_URL"), environment: "development" };
    const tracing = createLangfuseTracing({ ...config, release: "integration-test", serviceVersion: "0.1.0", mask: ({ data }) => typeof data === "string" ? data.replace(/sk-[A-Za-z0-9_-]+/g, "***") : data }, logger);
    const client = new LangfuseClient(config);
    const publisher = new LangfuseScorePublisher(client, config.environment);
    let traceId = "";
    try {
      await withTraceAttributes("0.1.0", { sessionId: `integration-${Date.now()}`, tags: ["integration-test", "context-compiler"] }, () =>
        tracing.run("context-compiler.integration-check", { kind: "chain", input: { check: "sdk-v5" } }, async () => {
          traceId = tracing.activeTraceId() ?? "";
          await tracing.run("sql-execution", { kind: "tool", input: { sql: "SELECT 42" } }, async () => ({ rows: [{ value: 42 }] }));
          await tracing.run("evidence-verification", { kind: "guardrail", input: { claim: "42%" } }, async () => ({ supported: true }));
          await tracing.run("recommendation-judge", { kind: "generation", input: { recommendation: "Use 42%." }, model: "integration-check", prompt: { name: "integration-judge", version: 1, isFallback: false } }, async () => ({ content: "ok", usage: { inputTokens: 1, outputTokens: 1, totalCostUsd: 0 } }));
        }),
      );
      expect(traceId).toHaveLength(32);
      const evaluations: EvaluationResult[] = [
        ["groundedness", 1], ["freshness", 1], ["recommendation-confidence", 0.9], ["evidence-coverage", 1],
        ["hallucination-risk", 0], ["business-impact", 0.8], ["sql-validity", 1],
      ].map(([name, score]) => ({ name: name as EvaluationResult["name"], score: score as number, passed: true, reason: "integration check", metadata: {} }));
      const judge: JudgeResult = { score: 0.9, confidence: 0.9, reason: "integration check", model: "integration-check", promptName: "integration-judge", promptVersion: "1", rawOutput: "{}", createdAt: new Date().toISOString() };
      await publisher.publish(traceId, `rec-${traceId}`, evaluations, judge);
      await tracing.flush();

      let fetched: Awaited<ReturnType<typeof client.api.trace.get>> | undefined;
      const deadline = Date.now() + 60_000;
      while (Date.now() < deadline) {
        try {
          const candidate = await client.api.trace.get(traceId);
          if (candidate.observations.length >= 4 && candidate.scores.length >= 8) { fetched = candidate; break; }
        } catch { /* ingestion is eventually consistent */ }
        await new Promise((resolve) => setTimeout(resolve, 1_000));
      }
      expect(fetched?.id).toBe(traceId);
      expect(fetched?.observations.map(({ name }) => name)).toEqual(expect.arrayContaining(["context-compiler.integration-check", "sql-execution", "evidence-verification", "recommendation-judge"]));
      expect(new Set(fetched?.scores.map(({ name }) => name))).toEqual(new Set(["Groundedness", "Freshness", "Confidence", "Evidence Coverage", "Hallucination Risk", "Business Alignment", "SQL Validity", "Recommendation Quality"]));
      console.log(`Langfuse trace: ${config.baseUrl}${fetched?.htmlPath}`);
    } finally {
      await publisher.shutdown();
      await tracing.shutdown();
    }
  }, 90_000);
});
