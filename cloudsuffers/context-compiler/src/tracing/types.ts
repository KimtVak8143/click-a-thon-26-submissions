import type { JsonValue } from "../types/domain.js";

export type ObservationKind = "span" | "agent" | "tool" | "chain" | "evaluator" | "guardrail" | "generation";

export interface ObservationOptions {
  readonly kind: ObservationKind;
  readonly input?: unknown;
  readonly metadata?: Readonly<Record<string, JsonValue>>;
  readonly model?: string;
  readonly modelParameters?: Readonly<Record<string, string | number>>;
  readonly prompt?: { readonly name: string; readonly version: number; readonly isFallback: boolean };
}

export interface TraceService {
  run<T>(name: string, options: ObservationOptions, operation: () => Promise<T>): Promise<T>;
  activeTraceId(): string | undefined;
  flush(): Promise<void>;
  shutdown(): Promise<void>;
}
