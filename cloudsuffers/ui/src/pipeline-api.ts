import { compilerApiUrl } from "./api-base";

export type PipelineStatus = "completed" | "contract_blocked" | "error" | string;

export interface ContractFeature {
  slug?: string;
  name?: string;
  objective?: string;
}

export interface ContractFunnelStep {
  order?: number;
  event_name?: string;
  label?: string | null;
}

export interface ContractFunnel {
  name?: string;
  entity_key?: string;
  workflow_grain?: string | null;
  steps?: ContractFunnelStep[];
}

export interface ContractMetric {
  name?: string;
  description?: string;
  value_type?: string;
  computability?: string;
}

export interface ContractDimension {
  name?: string;
  field_path?: string;
  description?: string;
}

export interface ContractEntity {
  name?: string;
  field_path?: string;
  description?: string;
  role?: "primary" | "secondary" | string;
  stable?: boolean;
}

export interface ContractRelationship {
  name?: string;
  from_entity?: string;
  to_entity?: string;
  from_field?: string;
  to_field?: string;
  cardinality?: string;
  description?: string;
}

export interface AnalyticsContract extends Record<string, unknown> {
  feature?: ContractFeature;
  grain?: string;
  primary_entity?: string;
  secondary_entities?: string[];
  entities?: ContractEntity[];
  events?: unknown[];
  fields?: unknown[];
  funnels?: ContractFunnel[];
  metrics?: ContractMetric[];
  dimensions?: ContractDimension[];
  relationships?: ContractRelationship[];
  context_version_id?: string | null;
  context_content_sha256?: string | null;
}

export interface PipelineSchemaPlan {
  strategy: string;
  table_name: string;
  ddl: string;
  deployed: boolean;
}

export interface PipelineInsight {
  title: string;
  summary: string;
  confidence: number;
  category: string;
}

export interface PipelineRunResponse {
  run_id: string;
  feature_slug: string;
  status: PipelineStatus;
  contract: AnalyticsContract | null;
  schema_plan: PipelineSchemaPlan | null;
  context_version_id: string | null;
  insights: PipelineInsight[] | null;
  errors: string[];
  duration_ms: number;
}

interface PipelineHttpError {
  detail?: { code?: string; message?: string } | string;
}

export async function runPipeline(
  spec: File,
  events: File,
  dryRun = true,
  signal?: AbortSignal,
): Promise<PipelineRunResponse> {
  const form = new FormData();
  form.append("spec", spec);
  form.append("events", events);
  form.append("dry_run", String(dryRun));

  const response = await fetch(compilerApiUrl("/pipeline/run"), {
    method: "POST",
    body: form,
    signal,
  });

  let body: PipelineRunResponse | PipelineHttpError;
  try {
    body = (await response.json()) as PipelineRunResponse | PipelineHttpError;
  } catch {
    throw new Error(`The compiler returned an unreadable response (HTTP ${response.status}).`);
  }

  if (!response.ok) {
    const detail = (body as PipelineHttpError).detail;
    if (typeof detail === "string") throw new Error(detail);
    const code = detail?.code ? `${detail.code}: ` : "";
    throw new Error(`${code}${detail?.message ?? `HTTP ${response.status}`}`);
  }

  return body as PipelineRunResponse;
}
