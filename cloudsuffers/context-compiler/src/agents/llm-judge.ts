import { z } from "zod";
import type { EvaluationContext, JudgeResult } from "../types/domain.js";
import { recommendationJudgePrompt } from "../prompts/judge.js";

export interface JudgeModelResponse {
  readonly content: string;
  readonly usage: { readonly inputTokens: number; readonly outputTokens: number; readonly totalCostUsd?: number };
}

export interface JudgeModel {
  readonly model: string;
  generate(system: string, input: string): Promise<JudgeModelResponse>;
}

const judgeSchema = z.object({ score: z.number().min(0).max(1), confidence: z.number().min(0).max(1), reason: z.string().min(1).max(2_000) }).strict();

export class LlmJudge {
  constructor(private readonly model: JudgeModel, private readonly clock: () => Date = () => new Date()) {}

  get modelName(): string { return this.model.model; }
  get prompt(): typeof recommendationJudgePrompt { return recommendationJudgePrompt; }

  async evaluate(context: EvaluationContext): Promise<JudgeResult & { readonly usage: JudgeModelResponse["usage"] }> {
    const input = JSON.stringify({
      question: context.candidate.question,
      recommendation: context.candidate.text,
      featureSpec: context.candidate.featureSpec,
      sql: context.candidate.sql,
      sqlResult: context.evidence.map(({ id, rows }) => ({ id, rows })),
      evidenceIds: context.candidate.evidenceIds,
    });
    const response = await this.model.generate(recommendationJudgePrompt.template, input);
    const parsed = judgeSchema.parse(JSON.parse(response.content));
    return { ...parsed, model: this.model.model, promptName: recommendationJudgePrompt.name, promptVersion: recommendationJudgePrompt.version, rawOutput: response.content, createdAt: this.clock().toISOString(), usage: response.usage };
  }
}
