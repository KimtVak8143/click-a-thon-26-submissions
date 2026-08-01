import { z } from "zod";

export const observabilityConfigSchema = z.object({
  serviceVersion: z.string().min(1),
  environment: z.string().regex(/^[a-z0-9_-]{1,40}$/),
  release: z.string().min(1),
  clickhouseDatabase: z.string().regex(/^[A-Za-z_][A-Za-z0-9_]*$/),
  maxEvidenceAgeMs: z.number().int().positive(),
  langfuse: z.object({ publicKey: z.string().min(1), secretKey: z.string().min(1), baseUrl: z.string().url() }),
}).strict();

export type ObservabilityConfig = z.infer<typeof observabilityConfigSchema>;
