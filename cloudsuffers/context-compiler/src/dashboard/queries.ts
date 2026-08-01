export const dashboardQueries = {
  recommendationAccuracy: `SELECT avg(score) FROM {database}.ai_judge_results WHERE created_at >= now() - INTERVAL 30 DAY`,
  averageConfidence: `SELECT avg(confidence) FROM {database}.ai_recommendations WHERE created_at >= now() - INTERVAL 30 DAY`,
  averageEvidenceCoverage: `SELECT avg(score) FROM {database}.ai_evaluations WHERE evaluator = 'evidence-coverage' AND created_at >= now() - INTERVAL 30 DAY`,
  groundedness: `SELECT avg(score) FROM {database}.ai_evaluations WHERE evaluator = 'groundedness' AND created_at >= now() - INTERVAL 30 DAY`,
  latency: `SELECT quantile(0.5)(latency_ms) AS p50, quantile(0.95)(latency_ms) AS p95, quantile(0.99)(latency_ms) AS p99 FROM {database}.ai_traces WHERE started_at >= now() - INTERVAL 30 DAY`,
  cost: `SELECT sum(total_cost_usd) AS cost_usd FROM {database}.ai_traces WHERE started_at >= now() - INTERVAL 30 DAY`,
  tokens: `SELECT sum(total_tokens) AS tokens FROM {database}.ai_traces WHERE started_at >= now() - INTERVAL 30 DAY`,
  failureRate: `SELECT countIf(status != 'APPROVED') / count() AS failure_rate FROM {database}.ai_recommendations WHERE created_at >= now() - INTERVAL 30 DAY`,
  hallucinationPercent: `SELECT 100 * avg(score) AS hallucination_percent FROM {database}.ai_evaluations WHERE evaluator = 'hallucination-risk' AND created_at >= now() - INTERVAL 30 DAY`,
  contextFreshness: `SELECT avg(passed) AS context_freshness FROM {database}.ai_evaluations WHERE evaluator = 'freshness' AND created_at >= now() - INTERVAL 30 DAY`,
  evaluatorHeatmap: `SELECT toStartOfDay(created_at) AS day, evaluator, avg(score) AS score, avg(passed) AS pass_rate FROM {database}.ai_evaluations WHERE created_at >= now() - INTERVAL 30 DAY GROUP BY day, evaluator ORDER BY day, evaluator`,
} as const;
