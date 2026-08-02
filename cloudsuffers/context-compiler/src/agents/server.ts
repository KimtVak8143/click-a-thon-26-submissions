import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { pathToFileURL } from "node:url";
import { createClient } from "@clickhouse/client";
import { LlmJudge } from "./llm-judge.js";
import { OpenAICompatibleJudgeModel } from "./openai-compatible-judge.js";
import { createProductionFramework } from "./factory.js";
import type { ObservabilityConfig } from "../types/config.js";
import type { EvaluationContext } from "../types/domain.js";
import { JsonConsoleLogger } from "../utils/logger.js";
import { maskSensitive } from "../utils/mask.js";
import { withTraceAttributes } from "../tracing/langfuse.js";

function required(name: string): string {
  const value = process.env[name] ?? process.env[`CONTEXT_COMPILER_${name}`];
  if (!value) throw new Error(`Missing required environment variable: ${name}`);
  return value;
}

function optional(name: string, fallback: string): string {
  return process.env[name] ?? process.env[`CONTEXT_COMPILER_${name}`] ?? fallback;
}

async function jsonBody(request: IncomingMessage, maximumBytes = 5 * 1024 * 1024): Promise<unknown> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk as Uint8Array);
    size += buffer.length;
    if (size > maximumBytes) throw new Error("Request body exceeds 5 MiB");
    chunks.push(buffer);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function respond(response: ServerResponse, status: number, body: unknown): void {
  const encoded = JSON.stringify(body);
  response.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(encoded) });
  response.end(encoded);
}

export async function startObservabilityServer(): Promise<void> {
  const logger = new JsonConsoleLogger("recommendation-observability");
  const secure = optional("CLICKHOUSE_SECURE", "false").toLowerCase() === "true";
  const clickhouse = createClient({
    url: `${secure ? "https" : "http"}://${optional("CLICKHOUSE_HOST", "localhost")}:${optional("CLICKHOUSE_PORT", secure ? "8443" : "8123")}`,
    username: optional("CLICKHOUSE_USERNAME", "default"), password: optional("CLICKHOUSE_PASSWORD", ""),
    database: optional("CLICKHOUSE_DATABASE", "default"), request_timeout: Number(optional("CLICKHOUSE_QUERY_TIMEOUT_SECONDS", "10")) * 1_000,
  });
  const config: ObservabilityConfig = {
    serviceVersion: optional("SERVICE_VERSION", "0.1.0"), environment: optional("APP_ENV", "development").toLowerCase(), release: optional("RELEASE", "local"),
    clickhouseDatabase: optional("CLICKHOUSE_METADATA_DATABASE", "compiler_meta"), maxEvidenceAgeMs: Number(optional("MAX_EVIDENCE_AGE_MS", "86400000")),
    langfuse: { publicKey: required("LANGFUSE_PUBLIC_KEY"), secretKey: required("LANGFUSE_SECRET_KEY"), baseUrl: optional("LANGFUSE_BASE_URL", "https://cloud.langfuse.com") },
  };
  const judge = new LlmJudge(new OpenAICompatibleJudgeModel({ baseUrl: required("LLM_BASE_URL"), apiKey: required("LLM_API_KEY"), model: required("LLM_MODEL"), timeoutMs: Number(optional("LLM_TIMEOUT_SECONDS", "30")) * 1_000 }));
  const runtime = createProductionFramework(config, { clickhouse, logger, judge });
  const authToken = process.env["OBSERVABILITY_AUTH_TOKEN"];
  const server = createServer(async (request, response) => {
    try {
      if (request.method === "GET" && request.url === "/health") return respond(response, 200, { status: "ok" });
      if (request.method !== "POST" || request.url !== "/v1/recommendations/evaluate") return respond(response, 404, { error: "not_found" });
      if (authToken && request.headers.authorization !== `Bearer ${authToken}`) return respond(response, 401, { error: "unauthorized" });
      const input = await jsonBody(request) as EvaluationContext;
      const decision = await withTraceAttributes(config.serviceVersion, {
        sessionId: input.candidate?.id,
        tags: ["context-compiler", "recommendation-release"],
        metadata: { boundary: "recommendation-observability" },
      }, () => runtime.framework.evaluate(input));
      return respond(response, decision.status === "APPROVED" ? 200 : 422, decision);
    } catch (error) {
      logger.error("request_failed", { error: error instanceof Error ? error.message : String(error) });
      return respond(response, 400, { error: "invalid_request", message: error instanceof Error ? error.message : "Unknown error" });
    }
  });
  const port = Number(optional("OBSERVABILITY_PORT", "4319"));
  server.listen(port, "127.0.0.1", () => logger.info("server_started", { port }));
  const shutdown = async () => { server.close(); await runtime.shutdown(); await clickhouse.close(); };
  process.once("SIGINT", () => void shutdown());
  process.once("SIGTERM", () => void shutdown());
}

const entrypoint = process.argv[1];
if (entrypoint && import.meta.url === pathToFileURL(entrypoint).href) {
  await startObservabilityServer().catch((error: unknown) => {
    console.error(JSON.stringify({ level: "error", message: "startup_failed", error: maskSensitive(error instanceof Error ? error.message : String(error)) }));
    process.exitCode = 1;
  });
}
