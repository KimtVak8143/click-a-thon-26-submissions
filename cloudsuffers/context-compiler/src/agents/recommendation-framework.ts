import type { EvaluationContext, EvaluationResult, RecommendationDecision, RecommendationStatus } from "../types/domain.js";
import type { EvaluationEngine } from "../evaluators/engine.js";
import type { Evaluator } from "../evaluators/types.js";
import type { LlmJudge } from "./llm-judge.js";
import type { ScorePublisher } from "../langfuse/score-publisher.js";
import type { ProvenanceRepository } from "../provenance/repository.js";
import type { ProvenanceService } from "../provenance/service.js";
import type { TraceService } from "../tracing/types.js";
import type { Logger } from "../utils/logger.js";
import { evaluationContextSchema } from "../types/schema.js";

export class RecommendationFramework {
  constructor(
    private readonly tracing: TraceService,
    private readonly evaluationEngine: EvaluationEngine,
    private readonly provenanceService: ProvenanceService,
    private readonly provenanceRepository: ProvenanceRepository,
    private readonly scores: ScorePublisher,
    private readonly logger: Logger,
    private readonly judge?: LlmJudge,
  ) {}

  async evaluate(context: EvaluationContext): Promise<RecommendationDecision> {
    const validated = evaluationContextSchema.parse(context) as EvaluationContext;
    return this.tracing.run("recommendation-lifecycle", { kind: "chain", input: validated.candidate, metadata: { recommendationId: validated.candidate.id } }, async () => {
      const traceId = this.tracing.activeTraceId();
      if (!traceId) throw new Error("Tracing provider did not create a trace ID; refusing an untraceable recommendation");

      const evaluations = await this.tracing.run("evaluations", { kind: "chain", input: { recommendationId: validated.candidate.id } }, async () =>
        this.evaluationEngine.run(validated, (evaluator, input) => this.runEvaluator(evaluator, input)),
      );

      const deterministicStatus = releaseStatus(evaluations);
      const judge = deterministicStatus === "APPROVED" && this.judge
        ? await this.tracing.run("llm-judge", {
          kind: "generation", input: { question: validated.candidate.question, recommendation: validated.candidate.text },
          model: this.judge.modelName, metadata: { promptVersion: this.judge.prompt.version },
          prompt: { name: this.judge.prompt.name, version: Number(this.judge.prompt.version), isFallback: false },
        }, () => this.judge!.evaluate(validated))
        : undefined;
      const status: RecommendationStatus = judge && (judge.score < 0.6 || judge.confidence < 0.5) ? "BLOCKED_EVALUATION" : deterministicStatus;
      const provenance = this.provenanceService.build(validated.candidate, traceId, status, evaluations, judge);
      const decision: RecommendationDecision = { status, traceId, recommendation: validated.candidate, evaluations, provenance, ...(judge ? { judge } : {}) };

      await this.tracing.run("publish-langfuse-scores", { kind: "tool", input: evaluations.map(({ name, score }) => ({ name, score })) }, () => this.scores.publish(traceId, validated.candidate.id, evaluations, judge));
      await this.tracing.run("persist-provenance", { kind: "tool", input: provenance }, () => this.provenanceRepository.persist(decision));
      this.logger.info("recommendation_evaluated", { recommendationId: validated.candidate.id, traceId, status });
      return decision;
    });
  }

  private async runEvaluator(evaluator: Evaluator, context: EvaluationContext): Promise<EvaluationResult> {
    const kind = evaluator.name === "freshness" || evaluator.name === "groundedness" ? "guardrail" : "evaluator";
    return this.tracing.run(`evaluate.${evaluator.name}`, { kind, input: { recommendationId: context.candidate.id } }, async () => evaluator.evaluate(context));
  }
}

function releaseStatus(evaluations: readonly EvaluationResult[]): RecommendationStatus {
  const freshness = evaluations.find(({ name }) => name === "freshness");
  if (!freshness?.passed) return "BLOCKED_STALE_CONTEXT";
  const groundedness = evaluations.find(({ name }) => name === "groundedness");
  if (!groundedness?.passed) return "BLOCKED_UNSUPPORTED_EVIDENCE";
  return evaluations.every(({ passed }) => passed) ? "APPROVED" : "BLOCKED_EVALUATION";
}
