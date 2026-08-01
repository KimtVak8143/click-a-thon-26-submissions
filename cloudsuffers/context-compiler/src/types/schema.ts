import { z } from "zod";

const versionRefSchema = z.object({ id: z.string().min(1), checksum: z.string().regex(/^[a-f0-9]{64}$/i) }).strict();
const contextVersionsSchema = z.object({ spec: versionRefSchema, schema: versionRefSchema, businessContext: versionRefSchema }).strict();
const evidenceSchema = z.object({
  id: z.string().min(1), sql: z.string().min(1), rows: z.array(z.record(z.string(), z.unknown())),
  checksum: z.string().regex(/^[a-f0-9]{64}$/i), executedAt: z.iso.datetime(),
  queryId: z.string().min(1).optional(), latencyMs: z.number().nonnegative().optional(),
}).strict();

const candidateSchema = z.object({
  id: z.string().min(1), question: z.string().min(1), text: z.string().min(1), confidence: z.number().min(0).max(1),
  featureSpec: z.string().min(1), businessContext: z.string().min(1), sql: z.string().min(1), evidenceIds: z.array(z.string().min(1)).min(1),
  prompt: z.object({ name: z.string().min(1), version: z.string().min(1) }).strict(),
  model: z.object({ provider: z.string().min(1), name: z.string().min(1), version: z.string().min(1) }).strict(),
  versions: contextVersionsSchema,
  allowedSchema: z.record(z.string(), z.array(z.string())).optional(),
  modelUsage: z.object({ inputTokens: z.number().int().nonnegative(), outputTokens: z.number().int().nonnegative(), totalCostUsd: z.number().nonnegative().optional() }).strict().optional(),
}).strict();

export const evaluationContextSchema = z.object({
  candidate: candidateSchema, currentVersions: contextVersionsSchema, evidence: z.array(evidenceSchema),
  now: z.iso.datetime(), maxEvidenceAgeMs: z.number().int().positive(),
}).strict();
