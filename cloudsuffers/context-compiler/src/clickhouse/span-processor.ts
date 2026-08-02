import type { Context } from "@opentelemetry/api";
import type { ReadableSpan, Span, SpanProcessor } from "@opentelemetry/sdk-trace-base";
import type { Logger } from "../utils/logger.js";
import type { ClickHouseStore } from "./client.js";

function durationMs(span: ReadableSpan): number {
  return span.duration[0] * 1_000 + span.duration[1] / 1_000_000;
}

function details(value: unknown): Record<string, unknown> {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value !== "string") return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch { return {}; }
}

function finiteNumber(value: unknown): number {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number : 0;
}

export class ClickHouseSpanProcessor implements SpanProcessor {
  private readonly pending = new Set<Promise<void>>();
  private readonly totals = new Map<string, { tokens: number; cost: number }>();

  constructor(private readonly store: ClickHouseStore, private readonly database: string, private readonly logger: Logger) {}
  onStart(_span: Span, _parentContext: Context): void {}

  onEnd(span: ReadableSpan): void {
    const spanContext = span.spanContext();
    const parentId = span.parentSpanContext?.spanId ?? "";
    const start = new Date(span.startTime[0] * 1_000 + span.startTime[1] / 1_000_000).toISOString();
    const completed = new Date(Date.parse(start) + durationMs(span)).toISOString();
    const attributes = Object.fromEntries(Object.entries(span.attributes));
    const usage = details(attributes["langfuse.observation.usage_details"]);
    const costs = details(attributes["langfuse.observation.cost_details"]);
    const inputTokens = finiteNumber(usage["input"] ?? usage["inputTokens"] ?? attributes["gen_ai.usage.input_tokens"]);
    const outputTokens = finiteNumber(usage["output"] ?? usage["outputTokens"] ?? attributes["gen_ai.usage.output_tokens"]);
    const cost = finiteNumber(costs["total"] ?? costs["totalCost"]);
    const aggregate = this.totals.get(spanContext.traceId) ?? { tokens: 0, cost: 0 };
    aggregate.tokens += inputTokens + outputTokens;
    aggregate.cost += cost;
    this.totals.set(spanContext.traceId, aggregate);
    const work = Promise.all([
      this.store.insert(`${this.database}.ai_spans`, [{ trace_id: spanContext.traceId, span_id: spanContext.spanId, parent_span_id: parentId, name: span.name, kind: String(attributes["langfuse.observation.type"] ?? "span"), started_at: start, completed_at: completed, latency_ms: durationMs(span), status_code: span.status.code, status_message: span.status.message ?? "", input_json: String(attributes["langfuse.observation.input"] ?? ""), output_json: String(attributes["langfuse.observation.output"] ?? ""), metadata_json: JSON.stringify(attributes), tokens_input: inputTokens, tokens_output: outputTokens, cost_usd: cost, error: span.status.code === 2 ? span.status.message ?? "error" : "" }]),
      ...(parentId === "" ? [this.store.insert(`${this.database}.ai_traces`, [{ trace_id: spanContext.traceId, name: span.name, started_at: start, completed_at: completed, latency_ms: durationMs(span), status_code: span.status.code, input_json: String(attributes["langfuse.observation.input"] ?? ""), output_json: String(attributes["langfuse.observation.output"] ?? ""), metadata_json: JSON.stringify(attributes), total_tokens: aggregate.tokens, total_cost_usd: aggregate.cost, error: span.status.code === 2 ? span.status.message ?? "error" : "" }])] : []),
    ]).then(() => undefined).catch((error: unknown) => { this.logger.error("clickhouse_span_export_failed", { error: error instanceof Error ? error.message : String(error), traceId: spanContext.traceId }); });
    this.pending.add(work);
    void work.finally(() => {
      this.pending.delete(work);
      if (parentId === "") this.totals.delete(spanContext.traceId);
    });
  }

  async forceFlush(): Promise<void> { await Promise.all([...this.pending]); }
  async shutdown(): Promise<void> { await this.forceFlush(); }
}
