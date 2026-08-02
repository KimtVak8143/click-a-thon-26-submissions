import type { RecommendationDecision, ReasoningProvenance } from "../types/domain.js";

export interface ProvenanceRepository {
  persist(decision: RecommendationDecision): Promise<void>;
  find(recommendationId: string): Promise<ReasoningProvenance | undefined>;
}

export class InMemoryProvenanceRepository implements ProvenanceRepository {
  private readonly records = new Map<string, ReasoningProvenance>();
  async persist(decision: RecommendationDecision): Promise<void> { this.records.set(decision.recommendation.id, decision.provenance); }
  async find(recommendationId: string): Promise<ReasoningProvenance | undefined> { return this.records.get(recommendationId); }
}
