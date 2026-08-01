import type { EvaluationContext, EvaluationName, EvaluationResult } from "../types/domain.js";

export interface Evaluator {
  readonly name: EvaluationName;
  evaluate(context: EvaluationContext): Promise<EvaluationResult> | EvaluationResult;
}

export function result(
  name: EvaluationName,
  score: number,
  passed: boolean,
  reason: string,
  metadata: EvaluationResult["metadata"] = {},
): EvaluationResult {
  return { name, score: Math.max(0, Math.min(1, score)), passed, reason, metadata };
}
