import type { EvaluationContext, EvaluationResult } from "../types/domain.js";
import type { Evaluator } from "./types.js";

export class EvaluationEngine {
  constructor(private readonly evaluators: readonly Evaluator[]) {
    const names = evaluators.map(({ name }) => name);
    if (new Set(names).size !== names.length) throw new Error("Evaluator names must be unique");
  }

  async run(
    context: EvaluationContext,
    execute: (evaluator: Evaluator, context: EvaluationContext) => Promise<EvaluationResult> = async (evaluator, input) => evaluator.evaluate(input),
  ): Promise<readonly EvaluationResult[]> {
    const output: EvaluationResult[] = [];
    for (const evaluator of this.evaluators) output.push(await execute(evaluator, context));
    return output;
  }

  async runOne(name: Evaluator["name"], context: EvaluationContext): Promise<EvaluationResult> {
    const evaluator = this.evaluators.find((item) => item.name === name);
    if (!evaluator) throw new Error(`Evaluator is not registered: ${name}`);
    return evaluator.evaluate(context);
  }
}
