import type { ClickHouseClient } from "@clickhouse/client";

export interface ClickHouseStore {
  insert(table: string, values: readonly Record<string, unknown>[]): Promise<void>;
  query<T extends Record<string, unknown>>(query: string, parameters: Readonly<Record<string, unknown>>): Promise<readonly T[]>;
}

export class OfficialClickHouseStore implements ClickHouseStore {
  constructor(private readonly client: ClickHouseClient) {}

  async insert(table: string, values: readonly Record<string, unknown>[]): Promise<void> {
    if (values.length === 0) return;
    await this.client.insert({ table, values: [...values], format: "JSONEachRow" });
  }

  async query<T extends Record<string, unknown>>(query: string, parameters: Readonly<Record<string, unknown>>): Promise<readonly T[]> {
    const result = await this.client.query({ query, query_params: { ...parameters }, format: "JSONEachRow" });
    return result.json<T>();
  }
}
