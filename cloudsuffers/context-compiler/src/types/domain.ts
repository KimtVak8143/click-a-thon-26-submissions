export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

export interface VersionRef {
  readonly id: string;
  readonly checksum: string;
}

export interface ContextVersions {
  readonly spec: VersionRef;
  readonly schema: VersionRef;
  readonly businessContext: VersionRef;
}

export interface EvidenceRecord {
  readonly id: string;
  readonly sql: string;
  readonly rows: readonly Record<string, unknown>[];
  readonly checksum: string;
  readonly executedAt: string;
  readonly queryId?: string;
  readonly latencyMs?: number;
}

export interface RecommendationCandidate {
  readonly id: string;
  readonly question: string;
  readonly text: string;
  readonly confidence: number;
  readonly featureSpec: string;
  readonly businessContext: string;
  readonly sql: string;
  readonly evidenceIds: readonly string[];
  readonly prompt: { readonly name: string; readonly version: string };
  readonly model: { readonly provider: string; readonly name: string; readonly version: string };
  readonly versions: ContextVersions;
  readonly allowedSchema?: Readonly<Record<string, readonly string[]>>;
  readonly modelUsage?: {
    readonly inputTokens: number;
    readonly outputTokens: number;
    readonly totalCostUsd?: number;
  };
}

export interface EvaluationContext {
  readonly candidate: RecommendationCandidate;
  readonly currentVersions: ContextVersions;
  readonly evidence: readonly EvidenceRecord[];
  readonly now: string;
  readonly maxEvidenceAgeMs: number;
}

export const evaluationNames = [
  "sql-validity",
  "evidence-coverage",
  "freshness",
  "groundedness",
  "spec-alignment",
  "schema-consistency",
  "hallucination-risk",
  "recommendation-confidence",
  "business-impact",
] as const;

export type EvaluationName = (typeof evaluationNames)[number];

export interface EvaluationResult {
  readonly name: EvaluationName;
  /** A normalized metric. All are higher-is-better except hallucination-risk. */
  readonly score: number;
  readonly passed: boolean;
  readonly reason: string;
  readonly metadata: Readonly<Record<string, JsonValue>>;
}

export interface JudgeResult {
  readonly score: number;
  readonly confidence: number;
  readonly reason: string;
  readonly model: string;
  readonly promptName: string;
  readonly promptVersion: string;
  readonly rawOutput: string;
  readonly createdAt: string;
}

export type RecommendationStatus = "APPROVED" | "BLOCKED_STALE_CONTEXT" | "BLOCKED_UNSUPPORTED_EVIDENCE" | "BLOCKED_EVALUATION";

export interface ReasoningProvenance {
  readonly recommendationId: string;
  readonly traceId: string;
  readonly status: RecommendationStatus;
  readonly versions: ContextVersions;
  readonly prompt: RecommendationCandidate["prompt"];
  readonly model: RecommendationCandidate["model"];
  readonly sql: string;
  readonly evidenceIds: readonly string[];
  readonly evaluations: readonly EvaluationResult[];
  readonly judge?: JudgeResult;
  readonly inputChecksum: string;
  readonly outputChecksum: string;
  readonly timestamp: string;
}

export interface RecommendationDecision {
  readonly status: RecommendationStatus;
  readonly traceId: string;
  readonly recommendation: RecommendationCandidate;
  readonly evaluations: readonly EvaluationResult[];
  readonly judge?: JudgeResult;
  readonly provenance: ReasoningProvenance;
}
