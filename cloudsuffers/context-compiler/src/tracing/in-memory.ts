import { randomUUID } from "node:crypto";
import type { ObservationOptions, TraceService } from "./types.js";

export interface RecordedObservation {
  readonly name: string;
  readonly options: ObservationOptions;
  readonly output?: unknown;
  readonly error?: string;
}

export class InMemoryTraceService implements TraceService {
  readonly observations: RecordedObservation[] = [];
  private traceId: string | undefined;

  async run<T>(name: string, options: ObservationOptions, operation: () => Promise<T>): Promise<T> {
    const wasRoot = this.traceId === undefined;
    if (wasRoot) this.traceId = randomUUID().replaceAll("-", "");
    try {
      const output = await operation();
      this.observations.push({ name, options, output });
      return output;
    } catch (error) {
      this.observations.push({ name, options, error: error instanceof Error ? error.message : String(error) });
      throw error;
    } finally {
      if (wasRoot) this.traceId = undefined;
    }
  }

  activeTraceId(): string | undefined { return this.traceId; }
  async flush(): Promise<void> {}
  async shutdown(): Promise<void> {}
}
