import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  runPipeline,
  type AnalyticsContract,
  type PipelineRunResponse,
} from "./pipeline-api";

type Check = { status: "loading" | "ok" | "error"; detail: string };

const stageNames = [
  "Inputs validated",
  "Events profiled",
  "Context resolved",
  "Contract generated",
  "Schema planned",
  "Context updated",
  "Insights generated",
];

async function getCheck(path: string): Promise<Check> {
  try {
    const response = await fetch(`/compiler-api${path}`);
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
  const controller = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    const [apiCheck, clickhouseCheck, llmCheck] = await Promise.all([
      getCheck("/health"),
      getCheck("/health/clickhouse"),
      getCheck("/health/llm"),
    ]);
    setApi(apiCheck);
    setClickhouse(clickhouseCheck);
    setLlm(llmCheck);
  }, []);

  useEffect(() => void refresh(), [refresh]);
  useEffect(() => () => controller.current?.abort(), []);

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
    setRunning(true);
    setRequestError("");
    setResult(null);
    try {
      setResult(await runPipeline(specFile, eventsFile, dryRun, abortController.signal));
    } catch (error) {
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
          <a className="chat" href="http://localhost:3080" target="_blank" rel="noreferrer">Open LibreChat</a>
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
          ) : <Empty text="Upload a spec and event sample to run the complete compiler in dry-run mode." />}
        </article>
      </section>

      <section className="card pipeline" id="pipeline">
        <div className="section-heading"><span>03</span><div><h2>Pipeline timeline</h2><p>{pipelineSummary(result, running)}</p></div></div>
        <div className="stage-list">
          {stageNames.map((stage, index) => {
            const state = stageState(index, result, running);
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

      <section className="insights" id="insights">
        <div className="insight-heading"><p className="eyebrow">VERIFIED INSIGHTS</p><h2>Evidence-backed analysis</h2><p>Insights are generated only after contract and schema planning complete.</p></div>
        {result?.insights && result.insights.length > 0 ? (
          <div className="insight-list">{result.insights.map((insight) => (
            <article key={`${insight.category}:${insight.title}`}><span>{insight.category}</span><h3>{insight.title}</h3><p>{insight.summary}</p><small>{Math.round(insight.confidence * 100)}% confidence</small></article>
          ))}</div>
        ) : <p className="insight-empty">No insights available for this run.</p>}
      </section>
      <footer>Context Compiler / end-to-end pipeline integration</footer>
    </main>
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

function stageState(index: number, result: PipelineRunResponse | null, running: boolean) {
  if (running) return index === 0 ? "active" : "pending";
  if (!result) return "pending";
  if (result.status === "completed") return "complete";
  const completedStages = result.status === "contract_blocked" ? 3 : result.status === "error" ? 4 : 0;
  if (index < completedStages) return "complete";
  if (index === completedStages) return "failed";
  return "pending";
}

function stageLabel(state: string) {
  if (state === "complete") return "Complete";
  if (state === "active") return "In progress";
  if (state === "failed") return "Stopped here";
  return "Waiting";
}

function pipelineSummary(result: PipelineRunResponse | null, running: boolean) {
  if (running) return "The request is running; stages update when the synchronous response returns.";
  if (!result) return "Waiting for a pipeline run.";
  if (result.status === "completed") return "All orchestration stages completed.";
  return `The pipeline finished with status ${result.status}.`;
}
