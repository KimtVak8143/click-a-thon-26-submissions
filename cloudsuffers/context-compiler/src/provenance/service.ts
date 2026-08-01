import type { EvaluationResult, JudgeResult, ReasoningProvenance, RecommendationCandidate, RecommendationStatus } from "../types/domain.js";
import { sha256 } from "../utils/hash.js";

export class ProvenanceService {
  constructor(private readonly clock: () => Date = () => new Date()) {}

  build(candidate: RecommendationCandidate, traceId: string, status: RecommendationStatus, evaluations: readonly EvaluationResult[], judge?: JudgeResult): ReasoningProvenance {
    return {
      recommendationId: candidate.id, traceId, status, versions: candidate.versions,
      prompt: candidate.prompt, model: candidate.model, sql: candidate.sql,
      evidenceIds: candidate.evidenceIds, evaluations,
      ...(judge ? { judge } : {}),
      inputChecksum: sha256({ question: candidate.question, featureSpec: candidate.featureSpec, businessContext: candidate.businessContext, sql: candidate.sql, evidenceIds: candidate.evidenceIds, versions: candidate.versions }),
      outputChecksum: sha256(candidate.text), timestamp: this.clock().toISOString(),
    };
  }
}
