import type { ClickHouseClient } from "@clickhouse/client";
import { ClickHouseSpanProcessor } from "../clickhouse/span-processor.js";
import { OfficialClickHouseStore } from "../clickhouse/client.js";
import { ClickHouseProvenanceRepository } from "../clickhouse/provenance-repository.js";
import { defaultEvaluators } from "../evaluators/deterministic.js";
import { EvaluationEngine } from "../evaluators/engine.js";
import { createLangfuseScorePublisher } from "../langfuse/score-publisher.js";
import { ProvenanceService } from "../provenance/service.js";
import { createLangfuseTracing } from "../tracing/langfuse.js";
import { observabilityConfigSchema, type ObservabilityConfig } from "../types/config.js";
import type { Logger } from "../utils/logger.js";
import { maskSensitive } from "../utils/mask.js";
import type { LlmJudge } from "./llm-judge.js";
import { RecommendationFramework } from "./recommendation-framework.js";

export interface FrameworkRuntime {
  readonly framework: RecommendationFramework;
  shutdown(): Promise<void>;
}

export function createProductionFramework(
  rawConfig: ObservabilityConfig,
  dependencies: { readonly clickhouse: ClickHouseClient; readonly logger: Logger; readonly judge: LlmJudge },
): FrameworkRuntime {
  const config = observabilityConfigSchema.parse(rawConfig);
  const store = new OfficialClickHouseStore(dependencies.clickhouse);
  const clickhouseProcessor = new ClickHouseSpanProcessor(store, config.clickhouseDatabase, dependencies.logger);
  const tracing = createLangfuseTracing({ ...config.langfuse, environment: config.environment, release: config.release, serviceVersion: config.serviceVersion, additionalSpanProcessors: [clickhouseProcessor], mask: ({ data }) => maskSensitive(data) }, dependencies.logger);
  const scores = createLangfuseScorePublisher({ ...config.langfuse, environment: config.environment });
  const repository = new ClickHouseProvenanceRepository(store, config.clickhouseDatabase);
  const framework = new RecommendationFramework(tracing, new EvaluationEngine(defaultEvaluators()), new ProvenanceService(), repository, scores, dependencies.logger, dependencies.judge);
  return {
    framework,
    async shutdown() { await tracing.flush(); await scores.shutdown(); await tracing.shutdown(); },
  };
}
