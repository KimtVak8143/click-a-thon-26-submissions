import { ChangeEvent, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  runPipeline,
  type AnalyticsContract,
  type ContractEntity,
  type ContractRelationship,
  type PipelineRunResponse,
} from "./pipeline-api";
import {
  getDashboard,
  type DashboardResponse,
  type EvaluatorScore,
  type ObservableTrace,
} from "./dashboard-api";
import { compilerApiUrl } from "./api-base";

type Check = { status: "loading" | "ok" | "error"; detail: string };
type StageOutcome = "idle" | "running" | "complete" | "blocked" | "error";

const stageNames = [
  "Inputs validated",
  "Events profiled",
  "Context resolved",
  "Contract generated",
  "Schema planned",
  "Context updated",
  "Insights generated",
  "Recommendations evaluated",
];

const STAGE_TICK_MS = 650;
const STAGE_CATCHUP_MS = 180;

function finalStageIndex(status: string) {
  if (status === "completed") return stageNames.length - 1;
  if (status === "contract_blocked") return 3;
  if (status === "error") return 4;
  return 0;
}

function outcomeForStatus(status: string): StageOutcome {
  if (status === "completed") return "complete";
  if (status === "contract_blocked") return "blocked";
  return "error";
}

async function getCheck(path: string): Promise<Check> {
  try {
    const response = await fetch(compilerApiUrl(path));
    const data = (await response.json()) as { status?: string; detail?: unknown };
    if (!response.ok) {
      const detail = typeof data.detail === "string" ? data.detail : `HTTP ${response.status}`;
      throw new Error(detail);
    }
    return { status: "ok", detail: data.status ?? "Connected" };
  } catch (error) {
    return { status: "error", detail: error instanceof Error ? error.message : "Unavailable" };
  }
}

export default function App() {
  const [api, setApi] = useState<Check>({ status: "loading", detail: "Checking" });
  const [clickhouse, setClickhouse] = useState<Check>({ status: "loading", detail: "Checking" });
  const [llm, setLlm] = useState<Check>({ status: "loading", detail: "Checking" });
  const [specFile, setSpecFile] = useState<File | null>(null);
  const [eventsFile, setEventsFile] = useState<File | null>(null);
  const [result, setResult] = useState<PipelineRunResponse | null>(null);
  const [requestError, setRequestError] = useState("");
  const [running, setRunning] = useState(false);
  const [dryRun, setDryRun] = useState(true);
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [dashboardLoading, setDashboardLoading] = useState(true);
  const [dashboardError, setDashboardError] = useState("");
  const [stageProgress, setStageProgressState] = useState(0);
  const [stageOutcome, setStageOutcome] = useState<StageOutcome>("idle");
  const controller = useRef<AbortController | null>(null);
  const stageProgressRef = useRef(0);
  const stageTimers = useRef<number[]>([]);

  const setStageProgress = useCallback((value: number) => {
    stageProgressRef.current = value;
    setStageProgressState(value);
  }, []);

  const clearStageTimers = useCallback(() => {
    stageTimers.current.forEach((id) => window.clearTimeout(id));
    stageTimers.current = [];
  }, []);

  const runStageTicks = useCallback((cap: number) => {
    const step = (current: number) => {
      if (current >= cap) return;
      const id = window.setTimeout(() => {
        setStageProgress(current + 1);
        step(current + 1);
      }, STAGE_TICK_MS);
      stageTimers.current.push(id);
    };
    step(stageProgressRef.current);
  }, [setStageProgress]);

  const animateStagesTo = useCallback((target: number) => new Promise<void>((resolve) => {
    if (stageProgressRef.current >= target) {
      setStageProgress(target);
      resolve();
      return;
    }
    const step = (current: number) => {
      if (current >= target) {
        resolve();
        return;
      }
      const id = window.setTimeout(() => {
        setStageProgress(current + 1);
        step(current + 1);
      }, STAGE_CATCHUP_MS);
      stageTimers.current.push(id);
    };
    step(stageProgressRef.current);
  }), [setStageProgress]);

  const refreshDashboard = useCallback(async () => {
    setDashboardLoading(true);
    setDashboardError("");
    try {
      setDashboard(await getDashboard());
    } catch (error) {
      setDashboardError(error instanceof Error ? error.message : "Observability data unavailable");
    } finally {
      setDashboardLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    const [apiCheck, clickhouseCheck, llmCheck] = await Promise.all([
      getCheck("/health"),
      getCheck("/health/clickhouse"),
      getCheck("/health/llm"),
    ]);
    setApi(apiCheck);
    setClickhouse(clickhouseCheck);
    setLlm(llmCheck);
    await refreshDashboard();
  }, [refreshDashboard]);

  useEffect(() => void refresh(), [refresh]);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => () => clearStageTimers(), [clearStageTimers]);

  const selectSpecFile = (event: ChangeEvent<HTMLInputElement>) => {
    setSpecFile(event.target.files?.[0] ?? null);
    clearResult();
  };

  const selectEventsFile = (event: ChangeEvent<HTMLInputElement>) => {
    setEventsFile(event.target.files?.[0] ?? null);
    clearResult();
  };

  const clearResult = () => {
    setResult(null);
    setRequestError("");
  };

  const startPipeline = async () => {
    if (!specFile || !eventsFile) return;
    const abortController = new AbortController();
    controller.current = abortController;
    clearStageTimers();
    setRunning(true);
    setRequestError("");
    setResult(null);
    setStageOutcome("running");
    setStageProgress(0);
    runStageTicks(stageNames.length - 2);
    try {
      const runResult = await runPipeline(specFile, eventsFile, dryRun, abortController.signal);
      clearStageTimers();
      await animateStagesTo(finalStageIndex(runResult.status));
      setStageOutcome(outcomeForStatus(runResult.status));
      setResult(runResult);
      // Refreshing the observability panel is an independent concern from the pipeline
      // run itself, so it must not be awaited here — doing so would keep the button
      // disabled until the dashboard fetch finishes, well after the result is visible.
      void refreshDashboard().then(() => {
        window.setTimeout(() => void refreshDashboard(), 5_000);
      });
    } catch (error) {
      clearStageTimers();
      setStageOutcome("error");
      if (error instanceof DOMException && error.name === "AbortError") {
        setRequestError("Pipeline run cancelled.");
      } else {
        setRequestError(error instanceof Error ? error.message : "Pipeline run failed");
      }
    } finally {
      if (controller.current === abortController) controller.current = null;
      setRunning(false);
    }
  };

  const contract = result?.contract ?? null;

  return (
    <main>
      <header>
        <a className="brand" href="/">CC<span>Context Compiler</span></a>
        <nav>
          <a href="#run">New run</a>
          <a href="#pipeline">Pipeline</a>
          <a href="#artifacts">Artifacts</a>
          <a href="#er-diagram">ER diagram</a>
          <a href="#observability">Observability</a>
          <a className="chat" href={import.meta.env.VITE_LIBRECHAT_URL || "https://ai.clickhouse.cloud/c/new"} target="_blank" rel="noreferrer">Open LibreChat</a>
        </nav>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">FEATURE SPEC / TRUSTED INSIGHT</p>
          <h1>Compile product context.<br /><em>Verify every claim.</em></h1>
          <p className="lede">Run the complete compiler from a feature specification and observed events, then inspect its contract, schema plan, and evidence-backed insights.</p>
        </div>
        <div className="status-panel">
          <Status name="Backend API" check={api} />
          <Status name="ClickHouse" check={clickhouse} />
          <Status name="LLM provider" check={llm} />
          <button className="text-button" onClick={refresh}>Refresh connections</button>
        </div>
      </section>

      <section className="grid" id="run">
        <article className="card upload-card">
          <div className="section-heading"><span>01</span><div><h2>Upload feature inputs</h2><p>Select a Markdown spec and its NDJSON event sample.</p></div></div>
          <label className="dropzone">
            <input type="file" accept=".md,.markdown,text/markdown,text/plain" onChange={selectSpecFile} />
            <small>Feature specification</small>
            <strong>{specFile ? specFile.name : "Choose spec.md"}</strong>
            <small>{specFile ? formatBytes(specFile.size) : "Markdown describing events, questions, and goals"}</small>
          </label>
          <label className="dropzone">
            <input type="file" accept=".ndjson,application/x-ndjson" onChange={selectEventsFile} />
            <small>Observed event sample</small>
            <strong>{eventsFile ? eventsFile.name : "Choose events.ndjson"}</strong>
            <small>{eventsFile ? formatBytes(eventsFile.size) : "Newline-delimited JSON emitted by the feature"}</small>
          </label>
          <label className="dry-run-control">
            <input type="checkbox" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} />
            <span><strong>Dry run</strong><small>Plan and display DDL without deploying the feature table.</small></span>
          </label>
          <div className="run-actions">
            <button className="primary" disabled={!specFile || !eventsFile || running || api.status !== "ok"} onClick={startPipeline}>
              {running ? "Running pipeline…" : "Run compiler"}
            </button>
            {running && <button className="secondary" onClick={() => controller.current?.abort()}>Cancel</button>}
          </div>
          {running && <p className="running-note">Generation is synchronous and can take several minutes. You can safely cancel this browser request.</p>}
          {requestError && <p className="error" role="alert">{requestError}</p>}
        </article>

        <article className="card result-card">
          <div className="section-heading"><span>02</span><div><h2>Run result</h2><p>Compiler status and the most important generated metadata.</p></div></div>
          {result ? (
            <>
              <div className={`result-banner ${statusClass(result.status)}`}>
                <span>{result.status}</span>
                <strong>{contract?.feature?.name ?? result.feature_slug}</strong>
                <small>{result.run_id}</small>
              </div>
              <div className="metrics">
                <Metric label="Duration" value={formatDuration(result.duration_ms)} />
                <Metric label="Entities" value={String(contract?.entities?.length ?? 0)} />
                <Metric label="Events" value={String(contract?.events?.length ?? 0)} />
                <Metric label="Fields" value={String(contract?.fields?.length ?? 0)} />
                <Metric label="Metrics" value={String(contract?.metrics?.length ?? 0)} />
                <Metric label="Insights" value={String(result.insights?.length ?? 0)} />
                <div className="hash"><span>Context version</span><code>{result.context_version_id ?? "Not available"}</code></div>
              </div>
              {result.errors.length > 0 && (
                <div className="messages">
                  <strong>{result.status === "completed" ? "Non-fatal warnings" : "Pipeline errors"}</strong>
                  {result.errors.map((error, index) => <p className={result.status === "completed" ? "warning" : "error"} key={`${error}-${index}`}>{error}</p>)}
                </div>
              )}
            </>
          ) : running ? <ResultSkeleton /> : <Empty text="Upload a spec and event sample to run the complete compiler in dry-run mode." />}
        </article>
      </section>

      <section className="card pipeline" id="pipeline">
        <div className="section-heading"><span>03</span><div><h2>Pipeline timeline</h2><p>{pipelineSummary(stageOutcome, stageProgress)}</p></div></div>
        <div className="stage-list">
          {stageNames.map((stage, index) => {
            const state = stageState(index, stageProgress, stageOutcome);
            return <div className={`stage ${state}`} key={stage}><b>{String(index + 1).padStart(2, "0")}</b><span>{stage}</span><small>{stageLabel(state)}</small></div>;
          })}
        </div>
      </section>

      <section className="artifact-section" id="artifacts">
        <div className="section-heading"><span>04</span><div><h2>Generated artifacts</h2><p>Review compiler output as escaped text before deploying any schema.</p></div></div>
        {contract || result?.schema_plan ? (
          <div className="artifact-grid">
            <article className="card artifact-card">
              <h3>Contract summary</h3>
              <ContractSummary contract={contract} />
              <details><summary>Complete contract JSON</summary><pre><code>{JSON.stringify(contract, null, 2)}</code></pre></details>
            </article>
            <article className="card artifact-card">
              <div className="artifact-title"><h3>ClickHouse schema plan</h3>{result?.schema_plan && <span>{result.schema_plan.deployed ? "deployed" : "dry run"}</span>}</div>
              {result?.schema_plan ? (
                <><p className="artifact-meta">{result.schema_plan.strategy} / {result.schema_plan.table_name}</p><pre><code>{result.schema_plan.ddl}</code></pre></>
              ) : <Empty text="No schema plan was produced for this run." />}
            </article>
          </div>
        ) : <div className="card"><Empty text="Contract JSON and generated DDL will appear here after a successful run." /></div>}
      </section>

      <section className="er-section" id="er-diagram">
        <article className="card">
          <div className="section-heading"><span>05</span><div><h2>Entity relationships</h2><p>Entities and relationships resolved for this contract, validated against the shared context layer.</p></div></div>
          <ErDiagram contract={contract} contextVersionId={contract?.context_version_id ?? result?.context_version_id ?? null} />
        </article>
      </section>

      <section className="insights" id="insights">
        <div className="insight-heading"><p className="eyebrow">VERIFIED INSIGHTS</p><h2>Evidence-backed analysis</h2><p>Insights are generated only after contract and schema planning complete.</p></div>
        {result?.insights && result.insights.length > 0 ? (
          <div className="insight-list">{result.insights.map((insight) => (
            <article key={`${insight.category}:${insight.title}`}><span>{insight.category}</span><h3>{insight.title}</h3><p>{insight.summary}</p><small>{Math.round(insight.confidence * 100)}% confidence</small></article>
          ))}</div>
        ) : <p className="insight-empty">No insights available for this run.</p>}
      </section>
      <ObservabilityDashboard
        dashboard={dashboard}
        loading={dashboardLoading}
        error={dashboardError}
        onRefresh={refreshDashboard}
      />
      <footer>Context Compiler / end-to-end pipeline integration</footer>
    </main>
  );
}

function ObservabilityDashboard({
  dashboard,
  loading,
  error,
  onRefresh,
}: {
  dashboard: DashboardResponse | null;
  loading: boolean;
  error: string;
  onRefresh: () => Promise<void>;
}) {
  const summary = dashboard?.observability;
  const hasData = Boolean(summary?.has_data);
  return <section className="observability" id="observability">
    <div className="observability-heading">
      <div>
        <p className="eyebrow">AI OBSERVABILITY</p>
        <h2>Recommendation quality & traces</h2>
        <p>Langfuse scores, model economics, evaluator health, and reproducible recommendation decisions.</p>
      </div>
      <div className="observability-actions">
        <span className={`source-badge ${summary?.langfuse_available ? "connected" : ""}`}>
          {summary?.source ?? "Checking sources"}
        </span>
        <button className="secondary compact" onClick={() => void onRefresh()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh metrics"}
        </button>
      </div>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    {!error && !loading && !hasData ? <div className="observability-empty">
      <strong>No observability records found yet</strong>
      <p>Enable Langfuse, apply migrations 090–095, start the recommendation service, and run another journey.</p>
    </div> : <>
      <div className="observability-kpis">
        <ObservabilityMetric label="Recent traces" value={formatNumber(summary?.trace_count)} detail={summary?.langfuse_available ? "Langfuse connected" : "ClickHouse export"} />
        <ObservabilityMetric label="Approval rate" value={formatPercent(summary?.approval_rate)} detail={`${formatNumber(summary?.recommendation_count)} governed recommendations`} />
        <ObservabilityMetric label="Avg confidence" value={formatPercent(summary?.average_confidence)} detail={`${formatPercent(summary?.failure_rate)} blocked`} />
        <ObservabilityMetric label="Avg latency" value={formatMetricDuration(summary?.average_latency_ms)} detail={`${formatNumber(summary?.error_count)} traced errors`} />
        <ObservabilityMetric label="Total tokens" value={formatNumber(summary?.total_tokens)} detail="Last 30 days" />
        <ObservabilityMetric label="Total cost" value={formatMoney(summary?.total_cost_usd)} detail="Recent traced workload" />
      </div>
      <div className="observability-grid">
        <article className="observability-panel score-panel">
          <div className="panel-title"><div><h3>Evaluator scorecard</h3><p>Average score and sample size by evaluator.</p></div><span>{dashboard?.evaluator_scores.length ?? 0} metrics</span></div>
          {dashboard?.evaluator_scores.length ? <div className="score-list">
            {dashboard.evaluator_scores.map((score) => <ScoreRow score={score} key={`${score.source}:${score.name}`} />)}
          </div> : <SmallEmpty text="No Langfuse or ClickHouse scores have been recorded." />}
        </article>
        <article className="observability-panel trace-panel">
          <div className="panel-title"><div><h3>Recent traces</h3><p>Open a trace in Langfuse to inspect its span tree.</p></div><span>live</span></div>
          {dashboard?.recent_traces.length ? <div className="trace-list">
            {dashboard.recent_traces.slice(0, 8).map((trace) => <TraceRow trace={trace} key={trace.trace_id} />)}
          </div> : <SmallEmpty text="No traces were returned by Langfuse or ClickHouse." />}
        </article>
      </div>
      <article className="observability-panel recommendation-panel">
        <div className="panel-title"><div><h3>Governed recommendations</h3><p>Publication status, model, prompt version, confidence, and trace correlation.</p></div><span>ClickHouse</span></div>
        {dashboard?.recommendations.length ? <div className="recommendation-table-wrap"><table className="recommendation-table">
          <thead><tr><th>Status</th><th>Recommendation</th><th>Confidence</th><th>Model / prompt</th><th>Created</th></tr></thead>
          <tbody>{dashboard.recommendations.map((item) => <tr key={item.recommendation_id}>
            <td><span className={`decision ${item.status === "APPROVED" ? "approved" : "blocked"}`}>{item.status.replaceAll("_", " ")}</span></td>
            <td><strong>{item.recommendation}</strong><code>{shortId(item.trace_id)}</code></td>
            <td>{formatPercent(item.confidence)}</td>
            <td>{item.model}<small>prompt v{item.prompt_version}</small></td>
            <td>{formatTimestamp(item.created_at)}</td>
          </tr>)}</tbody>
        </table></div> : <SmallEmpty text="No governed recommendation rows yet. Existing Langfuse traces can still be inspected above." />}
      </article>
    </>}
  </section>;
}

function ObservabilityMetric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="observability-metric"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function ScoreRow({ score }: { score: EvaluatorScore }) {
  const bounded = Math.max(0, Math.min(1, score.value));
  const risk = score.name.toLowerCase().includes("hallucination");
  return <div className="score-row">
    <div><strong>{prettyScore(score.name)}</strong><small>{score.count} samples · {score.source}</small></div>
    <div className="score-bar" aria-label={`${prettyScore(score.name)} ${Math.round(bounded * 100)} percent`}><i className={risk ? "risk" : ""} style={{ width: `${bounded * 100}%` }} /></div>
    <b>{Math.round(bounded * 100)}%</b>
  </div>;
}

function TraceRow({ trace }: { trace: ObservableTrace }) {
  const content = <>
    <i className={trace.status === "error" ? "error" : "ok"} />
    <span><strong>{trace.name}</strong><small>{formatTimestamp(trace.timestamp)} · {formatMetricDuration(trace.latency_ms)} · {trace.observations ?? "—"} spans</small></span>
    <code>{formatMoney(trace.cost_usd)}</code>
  </>;
  return trace.url ? <a className="trace-row" href={trace.url} target="_blank" rel="noreferrer">{content}</a> : <div className="trace-row">{content}</div>;
}

function SmallEmpty({ text }: { text: string }) {
  return <p className="small-empty">{text}</p>;
}

interface ErLink {
  id: string;
  path: string;
  labelX: number;
  labelY: number;
  cardinality: string;
  title: string;
}

function cardinalityLabel(value?: string) {
  switch (value) {
    case "one_to_one": return "1 – 1";
    case "one_to_many": return "1 – N";
    case "many_to_one": return "N – 1";
    case "many_to_many": return "N – N";
    default: return "—";
  }
}

const EMPTY_ENTITIES: ContractEntity[] = [];
const EMPTY_RELATIONSHIPS: ContractRelationship[] = [];

function ErDiagram({ contract, contextVersionId }: { contract: AnalyticsContract | null; contextVersionId: string | null }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const nodeRefs = useRef(new Map<string, HTMLDivElement>());
  const [links, setLinks] = useState<ErLink[]>([]);

  const entities = contract?.entities ?? EMPTY_ENTITIES;
  const relationships = contract?.relationships ?? EMPTY_RELATIONSHIPS;

  const measure = useCallback(() => {
    const container = containerRef.current;
    if (!container || relationships.length === 0) {
      setLinks([]);
      return;
    }
    const containerRect = container.getBoundingClientRect();
    const next: ErLink[] = [];
    relationships.forEach((rel) => {
      const fromEl = rel.from_entity ? nodeRefs.current.get(rel.from_entity) : undefined;
      const toEl = rel.to_entity ? nodeRefs.current.get(rel.to_entity) : undefined;
      if (!fromEl || !toEl || fromEl === toEl) return;
      const fromRect = fromEl.getBoundingClientRect();
      const toRect = toEl.getBoundingClientRect();
      const x1 = fromRect.left + fromRect.width / 2 - containerRect.left;
      const y1 = fromRect.top + fromRect.height / 2 - containerRect.top;
      const x2 = toRect.left + toRect.width / 2 - containerRect.left;
      const y2 = toRect.top + toRect.height / 2 - containerRect.top;
      const sameRow = Math.abs(y1 - y2) < 6;
      const bow = sameRow ? -Math.min(48, Math.abs(x2 - x1) / 4) : 0;
      const midX = (x1 + x2) / 2;
      const midY = (y1 + y2) / 2 + bow;
      next.push({
        id: rel.name ?? `${rel.from_entity}->${rel.to_entity}`,
        path: `M ${x1} ${y1} Q ${midX} ${midY} ${x2} ${y2}`,
        labelX: midX,
        labelY: midY,
        cardinality: cardinalityLabel(rel.cardinality),
        title: `${rel.name ?? "relationship"}: ${rel.description ?? ""}`,
      });
    });
    setLinks(next);
  }, [relationships]);

  useLayoutEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure, entities.length]);

  if (entities.length === 0) {
    return <Empty text="Entity relationships will render once a contract is generated." />;
  }

  return (
    <div className="er-diagram">
      <div className="er-context-note">
        <span>Context layer</span>
        <code>{contextVersionId ? shortId(contextVersionId) : "not persisted for this run"}</code>
      </div>
      <div className="er-canvas" ref={containerRef}>
        <svg className="er-links">
          <defs>
            <marker id="er-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" />
            </marker>
          </defs>
          {links.map((link) => (
            <g key={link.id}>
              <path d={link.path} markerEnd="url(#er-arrow)"><title>{link.title}</title></path>
              <text x={link.labelX} y={link.labelY - 6}>{link.cardinality}</text>
            </g>
          ))}
        </svg>
        {entities.map((entity: ContractEntity, index) => (
          <div
            className={`er-node ${entity.role === "primary" ? "primary" : "secondary"}`}
            key={entity.name ?? `entity-${index}`}
            ref={(el) => {
              const key = entity.name ?? `entity-${index}`;
              if (el) nodeRefs.current.set(key, el);
              else nodeRefs.current.delete(key);
            }}
          >
            <span className="er-role">{entity.role ?? "entity"}</span>
            <strong>{entity.name}</strong>
            <small>{entity.field_path}</small>
          </div>
        ))}
      </div>
      {relationships.length === 0 && <p className="er-hint">No relationships were declared between entities for this contract.</p>}
    </div>
  );
}

function ContractSummary({ contract }: { contract: AnalyticsContract | null }) {
  if (!contract) return <Empty text="No validated contract was produced." />;
  return <div className="contract-summary">
    <p><span>Primary entity</span><strong>{contract.primary_entity ?? "—"}</strong></p>
    <p><span>Grain</span><strong>{contract.grain ?? "—"}</strong></p>
    {(contract.funnels ?? []).map((funnel) => <div className="funnel" key={funnel.name}>
      <h4>{funnel.name ?? "Funnel"}<small>{funnel.workflow_grain ?? funnel.entity_key}</small></h4>
      <ol>{(funnel.steps ?? []).map((step) => <li key={`${step.order}:${step.event_name}`}>{step.label ?? step.event_name}</li>)}</ol>
    </div>)}
    {(contract.metrics ?? []).length > 0 && <div className="chip-list">{contract.metrics?.map((metric) => <span key={metric.name}>{metric.name}</span>)}</div>}
  </div>;
}

function Status({ name, check }: { name: string; check: Check }) {
  return <div className="status-row"><i className={check.status} /><span><strong>{name}</strong><small title={check.detail}>{check.detail}</small></span></div>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function Empty({ text }: { text: string }) {
  return <div className="empty"><span /><p>{text}</p></div>;
}

function ResultSkeleton() {
  return (
    <div className="skeleton-result" aria-hidden="true">
      <div className="skeleton-block skeleton-banner" />
      <div className="metrics">
        {Array.from({ length: 6 }).map((_, index) => <div className="skeleton-block skeleton-metric" key={index} />)}
        <div className="skeleton-block skeleton-hash" />
      </div>
    </div>
  );
}

function formatBytes(bytes: number) {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KB`;
}

function formatDuration(milliseconds: number) {
  return milliseconds < 1000 ? `${milliseconds} ms` : `${(milliseconds / 1000).toFixed(1)} s`;
}

function statusClass(status: string) {
  if (status === "completed") return "valid";
  if (status === "contract_blocked") return "blocked";
  return "failed";
}

function stageState(index: number, progress: number, outcome: StageOutcome) {
  if (outcome === "idle") return "pending";
  if (index < progress) return "complete";
  if (index > progress) return "pending";
  if (outcome === "running") return "active";
  if (outcome === "complete") return "complete";
  return "failed";
}

function stageLabel(state: string) {
  if (state === "complete") return "Complete";
  if (state === "active") return "In progress";
  if (state === "failed") return "Stopped here";
  return "Waiting";
}

function pipelineSummary(outcome: StageOutcome, progress: number) {
  if (outcome === "idle") return "Waiting for a pipeline run.";
  if (outcome === "running") return `Running stage ${progress + 1} of ${stageNames.length}…`;
  if (outcome === "complete") return "All orchestration stages completed.";
  if (outcome === "blocked") return "The pipeline finished with status contract_blocked.";
  return "The pipeline finished with status error.";
}

function formatNumber(value: number | null | undefined) {
  return value == null ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
}

function formatPercent(value: number | null | undefined) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

function formatMetricDuration(value: number | null | undefined) {
  if (value == null) return "—";
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(2)} s`;
}

function formatMoney(value: number | null | undefined) {
  if (value == null) return "—";
  return value < 0.01 && value > 0 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function prettyScore(value: string) {
  return value.replaceAll("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function shortId(value: string) {
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}
