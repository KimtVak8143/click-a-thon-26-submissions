import { LangfuseSpanProcessor } from "@langfuse/otel";
import {
  getActiveTraceId,
  propagateAttributes,
  startActiveObservation,
  type LangfuseAgent,
  type LangfuseChain,
  type LangfuseEvaluator,
  type LangfuseGeneration,
  type LangfuseGuardrail,
  type LangfuseSpan,
  type LangfuseTool,
} from "@langfuse/tracing";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import type { SpanProcessor } from "@opentelemetry/sdk-trace-base";
import type { Logger } from "../utils/logger.js";
import type { ObservationOptions, TraceService } from "./types.js";

export interface LangfuseRuntimeConfig {
  readonly publicKey: string;
  readonly secretKey: string;
  readonly baseUrl: string;
  readonly environment: string;
  readonly release: string;
  readonly serviceVersion: string;
  readonly mask?: (params: { data: unknown }) => unknown | Promise<unknown>;
  readonly additionalSpanProcessors?: readonly SpanProcessor[];
}

export interface TraceAttributes {
  readonly userId?: string;
  readonly sessionId?: string;
  readonly tags?: readonly string[];
  readonly metadata?: Readonly<Record<string, string>>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export class LangfuseTraceService implements TraceService {
  constructor(
    private readonly provider: NodeTracerProvider,
    private readonly processor: LangfuseSpanProcessor,
    private readonly logger: Logger,
  ) {}

  async run<T>(name: string, options: ObservationOptions, operation: () => Promise<T>): Promise<T> {
    if (options.kind === "generation") {
      return startActiveObservation(name, (observation) => this.executeGeneration(observation, options, operation, name), { asType: "generation" });
    }
    const execute = (observation: UpdatableObservation) => this.execute(observation, options, operation, name);
    switch (options.kind) {
      case "agent": return startActiveObservation(name, execute, { asType: "agent" });
      case "tool": return startActiveObservation(name, execute, { asType: "tool" });
      case "chain": return startActiveObservation(name, execute, { asType: "chain" });
      case "evaluator": return startActiveObservation(name, execute, { asType: "evaluator" });
      case "guardrail": return startActiveObservation(name, execute, { asType: "guardrail" });
      case "span": return startActiveObservation(name, execute, { asType: "span" });
    }
  }

  private async execute<T>(observation: UpdatableObservation, options: ObservationOptions, operation: () => Promise<T>, name: string): Promise<T> {
    observation.update({ input: options.input, ...(options.metadata ? { metadata: { ...options.metadata } } : {}) });
    const startedAt = performance.now();
    try {
      const output = await operation();
      observation.update({ output, metadata: { ...options.metadata, latencyMs: performance.now() - startedAt } });
      return output;
    } catch (error) {
      observation.update({ level: "ERROR", statusMessage: errorMessage(error), output: { error: errorMessage(error) } });
      this.logger.error("observation_failed", { observation: name, error: errorMessage(error) });
      throw error;
    }
  }

  private async executeGeneration<T>(observation: LangfuseGeneration, options: ObservationOptions, operation: () => Promise<T>, name: string): Promise<T> {
    observation.update({
      input: options.input,
      ...(options.metadata ? { metadata: { ...options.metadata } } : {}),
      ...(options.model ? { model: options.model } : {}),
      ...(options.modelParameters ? { modelParameters: { ...options.modelParameters } } : {}),
      ...(options.prompt ? { prompt: options.prompt } : {}),
    });
      const startedAt = performance.now();
      try {
        const output = await operation();
        const usage = options.kind === "generation" && isUsageCarrier(output) ? output.usage : undefined;
        observation.update({
          output,
          metadata: { ...options.metadata, latencyMs: performance.now() - startedAt },
          ...(usage ? { usageDetails: { input: usage.inputTokens, output: usage.outputTokens }, costDetails: usage.totalCostUsd === undefined ? {} : { total: usage.totalCostUsd } } : {}),
        });
        return output;
      } catch (error) {
        observation.update({ level: "ERROR", statusMessage: errorMessage(error), output: { error: errorMessage(error) } });
        this.logger.error("observation_failed", { observation: name, error: errorMessage(error) });
        throw error;
      }
  }

  activeTraceId(): string | undefined { return getActiveTraceId(); }
  async flush(): Promise<void> { await Promise.all([this.processor.forceFlush(), this.provider.forceFlush()]); }
  async shutdown(): Promise<void> { await this.provider.shutdown(); }
}

type UpdatableObservation = LangfuseSpan | LangfuseAgent | LangfuseTool | LangfuseChain | LangfuseEvaluator | LangfuseGuardrail;

interface UsageCarrier { readonly usage: { readonly inputTokens: number; readonly outputTokens: number; readonly totalCostUsd?: number } }

function isUsageCarrier(value: unknown): value is UsageCarrier {
  return value !== null && typeof value === "object" && "usage" in value;
}

export function createLangfuseTracing(config: LangfuseRuntimeConfig, logger: Logger): LangfuseTraceService {
  const processor = new LangfuseSpanProcessor({
    publicKey: config.publicKey,
    secretKey: config.secretKey,
    baseUrl: config.baseUrl,
    environment: config.environment,
    release: config.release,
    ...(config.mask ? { mask: config.mask } : {}),
  });
  const provider = new NodeTracerProvider({ spanProcessors: [processor, ...(config.additionalSpanProcessors ?? [])] });
  provider.register();
  return new LangfuseTraceService(provider, processor, logger);
}

export function withTraceAttributes<T>(
  serviceVersion: string,
  attributes: TraceAttributes,
  operation: () => T,
): T {
  return propagateAttributes({
    ...(attributes.userId ? { userId: attributes.userId } : {}),
    ...(attributes.sessionId ? { sessionId: attributes.sessionId } : {}),
    ...(attributes.tags ? { tags: [...attributes.tags] } : {}),
    ...(attributes.metadata ? { metadata: { ...attributes.metadata } } : {}),
    version: serviceVersion,
    traceName: "context-compiler.recommendation",
  }, operation);
}
