import { LangfuseClient } from "@langfuse/client";
import type { EvaluationResult, JudgeResult } from "../types/domain.js";
import { deterministicId } from "../utils/hash.js";

export interface ScorePublisher {
  publish(traceId: string, recommendationId: string, evaluations: readonly EvaluationResult[], judge?: JudgeResult): Promise<void>;
  shutdown(): Promise<void>;
}

const scoreNames: Readonly<Record<string, string>> = {
  "groundedness": "Groundedness",
  "freshness": "Freshness",
  "recommendation-confidence": "Confidence",
  "evidence-coverage": "Evidence Coverage",
  "hallucination-risk": "Hallucination Risk",
  "business-impact": "Business Alignment",
  "sql-validity": "SQL Validity",
};

export class LangfuseScorePublisher implements ScorePublisher {
  constructor(private readonly client: LangfuseClient, private readonly environment: string) {}

  async publish(traceId: string, recommendationId: string, evaluations: readonly EvaluationResult[], judge?: JudgeResult): Promise<void> {
    for (const evaluation of evaluations) {
      const name = scoreNames[evaluation.name];
      if (!name) continue;
      this.client.score.create({
        id: deterministicId(traceId, recommendationId, name), traceId, name,
        value: evaluation.score, dataType: "NUMERIC", comment: evaluation.reason,
        environment: this.environment, metadata: { recommendationId, passed: evaluation.passed },
      });
    }
    if (judge) {
      this.client.score.create({ id: deterministicId(traceId, recommendationId, "Recommendation Quality"), traceId, name: "Recommendation Quality", value: judge.score, dataType: "NUMERIC", comment: judge.reason, environment: this.environment, metadata: { recommendationId, confidence: judge.confidence, model: judge.model, promptVersion: judge.promptVersion } });
    }
    await this.client.flush();
  }

  async shutdown(): Promise<void> { await this.client.shutdown(); }
}

export function createLangfuseScorePublisher(config: { publicKey: string; secretKey: string; baseUrl: string; environment: string }): LangfuseScorePublisher {
  return new LangfuseScorePublisher(new LangfuseClient({ publicKey: config.publicKey, secretKey: config.secretKey, baseUrl: config.baseUrl }), config.environment);
}
