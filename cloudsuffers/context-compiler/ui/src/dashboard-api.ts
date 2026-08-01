export interface ObservabilitySummary {
  source?: string;
  has_data?: boolean;
  langfuse_available?: boolean;
  clickhouse_available?: boolean;
  trace_count?: number;
  recommendation_count?: number;
  approval_rate?: number | null;
  average_confidence?: number | null;
  failure_rate?: number | null;
  average_latency_ms?: number | null;
  total_cost_usd?: number;
  total_tokens?: number;
  error_count?: number;
}

export interface EvaluatorScore {
  name: string;
  value: number;
  pass_rate: number | null;
  count: number;
  source: "langfuse" | "clickhouse" | string;
}

export interface ObservableRecommendation {
  recommendation_id: string;
  trace_id: string;
  status: string;
  recommendation: string;
  confidence: number;
  model: string;
  prompt_version: string;
  created_at: string | null;
}

export interface ObservableTrace {
  trace_id: string;
  name: string;
  timestamp: string | null;
  latency_ms: number;
  cost_usd: number;
  tokens: number | null;
  status: string;
  source: string;
  url?: string;
  observations?: number;
  score_count?: number;
  environment?: string;
}

export interface DashboardResponse {
  generated_at: string;
  observability: ObservabilitySummary;
  evaluator_scores: EvaluatorScore[];
  recommendations: ObservableRecommendation[];
  recent_traces: ObservableTrace[];
}

export async function getDashboard(signal?: AbortSignal): Promise<DashboardResponse> {
  const response = await fetch("/compiler-api/dashboard", { signal });
  if (!response.ok) throw new Error(`Dashboard request failed (HTTP ${response.status}).`);
  return response.json() as Promise<DashboardResponse>;
}
