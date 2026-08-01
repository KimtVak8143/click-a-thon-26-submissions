export const analyticsQueries = {
  averageConfidenceByModel: `SELECT model_provider, model_name, model_version, avg(confidence) AS average_confidence, count() AS recommendations FROM {database}.ai_recommendations WHERE created_at >= now() - INTERVAL 30 DAY GROUP BY ALL ORDER BY average_confidence DESC`,
  hallucinationTrend: `SELECT toStartOfDay(created_at) AS day, avg(score) AS hallucination_risk FROM {database}.ai_evaluations WHERE evaluator = 'hallucination-risk' GROUP BY day ORDER BY day`,
  promptVersionComparison: `SELECT prompt_name, prompt_version, avgIf(score, evaluator = 'groundedness') AS groundedness, avgIf(score, evaluator = 'spec-alignment') AS spec_alignment, countDistinct(recommendation_id) AS samples FROM {database}.ai_recommendations r INNER JOIN {database}.ai_evaluations e USING (recommendation_id, trace_id) GROUP BY prompt_name, prompt_version ORDER BY groundedness DESC`,
  evaluatorFailures: `SELECT evaluator, countIf(passed = 0) AS failures, count() AS total, failures / total AS failure_rate FROM {database}.ai_evaluations GROUP BY evaluator ORDER BY failure_rate DESC`,
  averageLatency: `SELECT name, avg(latency_ms) AS average_latency_ms, quantile(0.95)(latency_ms) AS p95_latency_ms FROM {database}.ai_spans GROUP BY name ORDER BY p95_latency_ms DESC`,
  averageCost: `SELECT toStartOfDay(started_at) AS day, avg(total_cost_usd) AS average_cost_usd, sum(total_cost_usd) AS total_cost_usd FROM {database}.ai_traces GROUP BY day ORDER BY day`,
  topFailingPrompts: `SELECT prompt_name, prompt_version, countIf(status != 'APPROVED') AS failures, count() AS total, failures / total AS failure_rate FROM {database}.ai_recommendations GROUP BY prompt_name, prompt_version HAVING total >= 3 ORDER BY failure_rate DESC LIMIT 20`,
  specAlignmentOverTime: `SELECT toStartOfDay(created_at) AS day, avg(score) AS spec_alignment FROM {database}.ai_evaluations WHERE evaluator = 'spec-alignment' GROUP BY day ORDER BY day`,
  freshnessViolations: `SELECT toStartOfDay(created_at) AS day, countIf(passed = 0) AS violations FROM {database}.ai_evaluations WHERE evaluator = 'freshness' GROUP BY day ORDER BY day`,
} as const;
