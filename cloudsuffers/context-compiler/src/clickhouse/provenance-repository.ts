import type { RecommendationDecision, ReasoningProvenance } from "../types/domain.js";
import type { ProvenanceRepository } from "../provenance/repository.js";
import type { ClickHouseStore } from "./client.js";

export class ClickHouseProvenanceRepository implements ProvenanceRepository {
  constructor(private readonly store: ClickHouseStore, private readonly database: string) {}

  async persist(decision: RecommendationDecision): Promise<void> {
    const { recommendation, provenance } = decision;
    await this.store.insert(`${this.database}.ai_evaluations`, decision.evaluations.map((evaluation) => ({ recommendation_id: recommendation.id, trace_id: decision.traceId, evaluator: evaluation.name, score: evaluation.score, passed: evaluation.passed ? 1 : 0, reason: evaluation.reason, metadata_json: JSON.stringify(evaluation.metadata), created_at: provenance.timestamp })));
    if (decision.judge) await this.store.insert(`${this.database}.ai_judge_results`, [{ recommendation_id: recommendation.id, trace_id: decision.traceId, score: decision.judge.score, confidence: decision.judge.confidence, reason: decision.judge.reason, model: decision.judge.model, prompt_name: decision.judge.promptName, prompt_version: decision.judge.promptVersion, raw_output: decision.judge.rawOutput, created_at: decision.judge.createdAt }]);
    await this.store.insert(`${this.database}.reasoning_provenance`, [{ recommendation_id: recommendation.id, trace_id: decision.traceId, status: decision.status, spec_version_id: provenance.versions.spec.id, spec_checksum: provenance.versions.spec.checksum, schema_version_id: provenance.versions.schema.id, schema_checksum: provenance.versions.schema.checksum, context_version_id: provenance.versions.businessContext.id, context_checksum: provenance.versions.businessContext.checksum, prompt_name: provenance.prompt.name, prompt_version: provenance.prompt.version, model_provider: provenance.model.provider, model_name: provenance.model.name, model_version: provenance.model.version, sql: provenance.sql, evidence_ids: [...provenance.evidenceIds], evaluations_json: JSON.stringify(provenance.evaluations), judge_json: provenance.judge ? JSON.stringify(provenance.judge) : "", input_checksum: provenance.inputChecksum, output_checksum: provenance.outputChecksum, provenance_json: JSON.stringify(provenance), created_at: provenance.timestamp }]);
    // Publication marker is deliberately last: a visible recommendation implies all audit rows exist.
    await this.store.insert(`${this.database}.ai_recommendations`, [{ recommendation_id: recommendation.id, trace_id: decision.traceId, status: decision.status, question: recommendation.question, recommendation: recommendation.text, confidence: recommendation.confidence, prompt_name: recommendation.prompt.name, prompt_version: recommendation.prompt.version, model_provider: recommendation.model.provider, model_name: recommendation.model.name, model_version: recommendation.model.version, sql: recommendation.sql, evidence_ids: [...recommendation.evidenceIds], created_at: provenance.timestamp }]);
  }

  async find(recommendationId: string): Promise<ReasoningProvenance | undefined> {
    const rows = await this.store.query<{ provenance_json: string }>(`SELECT provenance_json FROM ${this.database}.reasoning_provenance WHERE recommendation_id = {recommendationId:String} ORDER BY created_at DESC LIMIT 1`, { recommendationId });
    const first = rows[0];
    return first ? JSON.parse(first.provenance_json) as ReasoningProvenance : undefined;
  }
}
