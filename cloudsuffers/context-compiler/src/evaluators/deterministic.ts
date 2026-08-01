import type { EvaluationContext, EvaluationName, EvaluationResult } from "../types/domain.js";
import { NumericEvidenceVerifier } from "./evidence-verifier.js";
import { result, type Evaluator } from "./types.js";

const forbiddenSql = /\b(INSERT|ALTER|DROP|TRUNCATE|DELETE|UPDATE|CREATE|ATTACH|DETACH|RENAME|OPTIMIZE|SYSTEM|GRANT|REVOKE)\b/i;
const identifier = /\b(?:FROM|JOIN)\s+([`\w.]+)/gi;

function versionMatches(left: EvaluationContext["candidate"]["versions"], right: EvaluationContext["currentVersions"]): boolean {
  return (["spec", "schema", "businessContext"] as const).every(
    (key) => left[key].id === right[key].id && left[key].checksum === right[key].checksum,
  );
}

function tokens(text: string): Set<string> {
  return new Set(text.toLowerCase().match(/[a-z][a-z0-9_]{2,}/g) ?? []);
}

function overlap(left: string, right: string): number {
  const a = tokens(left);
  const b = tokens(right);
  if (a.size === 0) return 0;
  return [...a].filter((token) => b.has(token)).length / a.size;
}

abstract class BaseEvaluator implements Evaluator {
  abstract readonly name: EvaluationName;
  abstract evaluate(context: EvaluationContext): EvaluationResult;
}

export class SQLValidityEvaluator extends BaseEvaluator {
  readonly name = "sql-validity" as const;
  evaluate({ candidate }: EvaluationContext): EvaluationResult {
    const sql = candidate.sql.trim().replace(/;$/, "");
    const valid = /^(SELECT|WITH)\b/i.test(sql) && !forbiddenSql.test(sql) && !sql.includes(";");
    return result(this.name, valid ? 1 : 0, valid, valid ? "Read-only single-statement SQL." : "SQL must be one read-only SELECT/WITH statement.");
  }
}

export class EvidenceCoverageEvaluator extends BaseEvaluator {
  readonly name = "evidence-coverage" as const;
  evaluate({ candidate, evidence }: EvaluationContext): EvaluationResult {
    const available = new Set(evidence.map(({ id }) => id));
    const missing = candidate.evidenceIds.filter((id) => !available.has(id));
    const score = candidate.evidenceIds.length === 0 ? 0 : 1 - missing.length / candidate.evidenceIds.length;
    return result(this.name, score, score === 1, missing.length === 0 && score === 1 ? "All cited evidence is present." : "Recommendation has missing or no evidence.", { missing });
  }
}

export class FreshnessEvaluator extends BaseEvaluator {
  readonly name = "freshness" as const;
  evaluate(context: EvaluationContext): EvaluationResult {
    const versionsFresh = versionMatches(context.candidate.versions, context.currentVersions);
    const cutoff = Date.parse(context.now) - context.maxEvidenceAgeMs;
    const staleEvidence = context.evidence.filter(({ executedAt }) => Date.parse(executedAt) < cutoff).map(({ id }) => id);
    const passed = versionsFresh && staleEvidence.length === 0;
    return result(this.name, passed ? 1 : 0, passed, passed ? "Context versions and evidence are current." : "STALE_CONTEXT", { versionsFresh, staleEvidence });
  }
}

export class GroundednessEvaluator extends BaseEvaluator {
  readonly name = "groundedness" as const;
  constructor(private readonly verifier: NumericEvidenceVerifier) { super(); }
  evaluate({ candidate, evidence }: EvaluationContext): EvaluationResult {
    const cited = new Set(candidate.evidenceIds);
    const verification = this.verifier.verify(candidate.text, evidence.filter(({ id }) => cited.has(id)));
    return result(this.name, verification.coverage, verification.unsupported.length === 0, verification.unsupported.length === 0 ? "Every numerical claim is grounded in SQL output." : "Unsupported numerical claims detected.", { unsupportedClaims: verification.unsupported.map(({ raw }) => raw), claimCount: verification.claims.length });
  }
}

export class SpecAlignmentEvaluator extends BaseEvaluator {
  readonly name = "spec-alignment" as const;
  evaluate({ candidate }: EvaluationContext): EvaluationResult {
    const score = overlap(candidate.text, candidate.featureSpec);
    return result(this.name, score, score >= 0.08, score >= 0.08 ? "Recommendation references feature-spec concepts." : "Recommendation is weakly aligned with the feature spec.");
  }
}

export class SchemaConsistencyEvaluator extends BaseEvaluator {
  readonly name = "schema-consistency" as const;
  evaluate({ candidate }: EvaluationContext): EvaluationResult {
    const allowed = new Set(Object.keys(candidate.allowedSchema ?? {}).map((name) => name.toLowerCase()));
    const referenced = [...candidate.sql.matchAll(identifier)].map((match) => (match[1] ?? "").replaceAll("`", "").toLowerCase());
    const unknown = allowed.size === 0 ? [] : referenced.filter((name) => !allowed.has(name) && !allowed.has(name.split(".").at(-1) ?? name));
    const passed = referenced.length > 0 && unknown.length === 0;
    return result(this.name, passed ? 1 : 0, passed, passed ? "SQL only references declared schema tables." : "SQL references no table or an undeclared table.", { referenced, unknown });
  }
}

export class HallucinationRiskEvaluator extends BaseEvaluator {
  readonly name = "hallucination-risk" as const;
  constructor(private readonly verifier: NumericEvidenceVerifier) { super(); }
  evaluate({ candidate, evidence }: EvaluationContext): EvaluationResult {
    const cited = new Set(candidate.evidenceIds);
    const verification = this.verifier.verify(candidate.text, evidence.filter(({ id }) => cited.has(id)));
    const citationPenalty = candidate.evidenceIds.length === 0 ? 0.25 : 0;
    const risk = Math.min(1, 1 - verification.coverage + citationPenalty);
    return result(this.name, risk, risk <= 0.2, risk <= 0.2 ? "Low deterministic hallucination risk." : "Hallucination risk exceeds the release threshold.", { unsupportedClaims: verification.unsupported.map(({ raw }) => raw) });
  }
}

export class RecommendationConfidenceEvaluator extends BaseEvaluator {
  readonly name = "recommendation-confidence" as const;
  evaluate({ candidate, evidence }: EvaluationContext): EvaluationResult {
    const declared = Math.max(0, Math.min(1, candidate.confidence));
    const evidenceFactor = candidate.evidenceIds.length === 0 ? 0 : Math.min(1, evidence.length / candidate.evidenceIds.length);
    const calibrated = Math.min(declared, evidenceFactor);
    return result(this.name, calibrated, calibrated >= 0.6, calibrated >= 0.6 ? "Confidence is supported by available evidence." : "Confidence is too low or insufficiently supported.", { declaredConfidence: declared });
  }
}

export class BusinessImpactEvaluator extends BaseEvaluator {
  readonly name = "business-impact" as const;
  evaluate({ candidate }: EvaluationContext): EvaluationResult {
    const action = /\b(recommend|should|increase|decrease|prioriti[sz]e|launch|remove|test|optimi[sz]e)\b/i.test(candidate.text);
    const alignment = overlap(candidate.text, candidate.businessContext);
    const score = Math.min(1, (action ? 0.5 : 0) + alignment);
    return result(this.name, score, score >= 0.55, score >= 0.55 ? "Recommendation is actionable and aligned to business context." : "Business impact is not sufficiently actionable or aligned.");
  }
}

export function defaultEvaluators(verifier = new NumericEvidenceVerifier()): readonly Evaluator[] {
  return [
    new SQLValidityEvaluator(), new EvidenceCoverageEvaluator(), new FreshnessEvaluator(),
    new GroundednessEvaluator(verifier), new SpecAlignmentEvaluator(), new SchemaConsistencyEvaluator(),
    new HallucinationRiskEvaluator(verifier), new RecommendationConfidenceEvaluator(), new BusinessImpactEvaluator(),
  ];
}
