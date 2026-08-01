import type { EvaluationResult, JudgeResult } from "../types/domain.js";
import type { ScorePublisher } from "./score-publisher.js";

export class InMemoryScorePublisher implements ScorePublisher {
  readonly published: Array<{ traceId: string; recommendationId: string; evaluations: readonly EvaluationResult[]; judge?: JudgeResult }> = [];
  async publish(traceId: string, recommendationId: string, evaluations: readonly EvaluationResult[], judge?: JudgeResult): Promise<void> {
    this.published.push({ traceId, recommendationId, evaluations, ...(judge ? { judge } : {}) });
  }
  async shutdown(): Promise<void> {}
}
