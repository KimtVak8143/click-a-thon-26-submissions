import { ChangeEvent, useCallback, useEffect, useState } from "react";

type Check = { status: "loading" | "ok" | "error"; detail: string };
type Profile = {
  file: { sha256: string; total_line_count: number; valid_row_count: number };
  event_profile?: { events?: Array<{ event_name: string; count: number }> };
  fields?: unknown[];
};
type AnalyticsContract = {
  feature?: { slug?: string; name?: string; objective?: string };
  events?: unknown[];
  fields?: unknown[];
  metrics?: unknown[];
  funnels?: unknown[];
  dimensions?: unknown[];
};
type ContractIssue = { code?: string; message?: string; path?: string };
type ContractResult = {
  run_id: string;
  source_profile: Profile;
  analytics_contract: AnalyticsContract | null;
  validation_status: "valid" | "blocked";
  warnings: string[];
  errors: ContractIssue[];
  attempts: number;
  context_version_id?: string | null;
};

const stages = [
  "Spec received",
  "Contract generated",
  "DDL validated",
  "Data loaded",
  "Context published",
  "Freshness passed",
  "Insights verified",
];

async function getCheck(path: string): Promise<Check> {
  try {
    const response = await fetch(`/compiler-api${path}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail ?? `HTTP ${response.status}`);
    return { status: "ok", detail: data.status ?? "Connected" };
  } catch (error) {
    return { status: "error", detail: error instanceof Error ? error.message : "Unavailable" };
  }
}

export default function App() {
  const [api, setApi] = useState<Check>({ status: "loading", detail: "Checking" });
  const [clickhouse, setClickhouse] = useState<Check>({ status: "loading", detail: "Checking" });
  const [specFile, setSpecFile] = useState<File | null>(null);
  const [eventsFile, setEventsFile] = useState<File | null>(null);
  const [result, setResult] = useState<ContractResult | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [generating, setGenerating] = useState(false);

  const refresh = useCallback(async () => {
    const [apiCheck, clickhouseCheck] = await Promise.all([
      getCheck("/health"),
      getCheck("/health/clickhouse"),
    ]);
    setApi(apiCheck);
    setClickhouse(clickhouseCheck);
  }, []);

  useEffect(() => void refresh(), [refresh]);

  const selectSpecFile = (event: ChangeEvent<HTMLInputElement>) => {
    setSpecFile(event.target.files?.[0] ?? null);
    setResult(null);
    setUploadError("");
  };

  const selectEventsFile = (event: ChangeEvent<HTMLInputElement>) => {
    setEventsFile(event.target.files?.[0] ?? null);
    setResult(null);
    setUploadError("");
  };

  const generateContract = async () => {
    if (!specFile || !eventsFile) return;
    setGenerating(true);
    setUploadError("");
    const body = new FormData();
    body.append("spec", specFile);
    body.append("events", eventsFile);
    try {
      const response = await fetch("/compiler-api/contracts/generate", { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail?.message ?? `HTTP ${response.status}`);
      setResult(data);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Contract generation failed");
    } finally {
      setGenerating(false);
    }
  };

  const profile = result?.source_profile ?? null;
  const contract = result?.analytics_contract ?? null;
  const eventTypeCount = profile?.event_profile?.events?.length ?? "-";
  const fieldCount = profile?.fields?.length ?? "-";

  return (
    <main>
      <header>
        <a className="brand" href="/">CC<span>Context Compiler</span></a>
        <nav>
          <a href="#profile">New run</a>
          <a href="#pipeline">Pipeline</a>
          <a href="#insights">Insights</a>
          <a className="chat" href="http://localhost:3080" target="_blank" rel="noreferrer">Open LibreChat</a>
        </nav>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">FEATURE SPEC / TRUSTED INSIGHT</p>
          <h1>Compile product context.<br /><em>Verify every claim.</em></h1>
          <p className="lede">A thin run inspector for contracts, schema decisions, context changes, and ClickHouse-backed evidence.</p>
        </div>
        <div className="status-panel">
          <Status name="Backend API" check={api} />
          <Status name="ClickHouse" check={clickhouse} />
          <button className="text-button" onClick={refresh}>Refresh connections</button>
        </div>
      </section>

      <section className="grid" id="profile">
        <article className="card upload-card">
          <div className="section-heading"><span>01</span><div><h2>Upload feature inputs</h2><p>Use paired files from the specs folder: spec.md and events.ndjson.</p></div></div>
          <label className="dropzone">
            <input type="file" accept=".md,.markdown,text/markdown,text/plain" onChange={selectSpecFile} />
            <small>Feature specification</small>
            <strong>{specFile ? specFile.name : "Choose spec.md"}</strong>
            <small>{specFile ? `${(specFile.size / 1024).toFixed(1)} KB` : "Markdown describing events, questions, and goals"}</small>
          </label>
          <label className="dropzone">
            <input type="file" accept=".ndjson,application/x-ndjson" onChange={selectEventsFile} />
            <small>Observed event sample</small>
            <strong>{eventsFile ? eventsFile.name : "Choose events.ndjson"}</strong>
            <small>{eventsFile ? `${(eventsFile.size / 1024).toFixed(1)} KB` : "Newline-delimited JSON emitted by the feature"}</small>
          </label>
          <button className="primary" disabled={!specFile || !eventsFile || generating || api.status !== "ok"} onClick={generateContract}>
            {generating ? "Generating..." : "Generate contract"}
          </button>
          {uploadError && <p className="error">{uploadError}</p>}
        </article>

        <article className="card result-card">
          <div className="section-heading"><span>02</span><div><h2>Contract result</h2><p>Generated from the feature spec and profiled event sample.</p></div></div>
          {result && profile ? (
            <>
              <div className={`result-banner ${result.validation_status}`}>
                <span>{result.validation_status}</span>
                <strong>{contract?.feature?.name ?? contract?.feature?.slug ?? "Contract blocked"}</strong>
                <small>{result.run_id}</small>
              </div>
              <div className="metrics">
                <Metric label="Valid rows" value={profile.file.valid_row_count.toLocaleString()} />
                <Metric label="Event types" value={String(eventTypeCount)} />
                <Metric label="Fields" value={String(fieldCount)} />
                <Metric label="Attempts" value={String(result.attempts)} />
                {contract && (
                  <>
                    <Metric label="Metrics" value={String(contract.metrics?.length ?? 0)} />
                    <Metric label="Funnels" value={String(contract.funnels?.length ?? 0)} />
                  </>
                )}
                <div className="hash"><span>Events SHA-256</span><code>{profile.file.sha256}</code></div>
              </div>
              {(result.errors.length > 0 || result.warnings.length > 0) && (
                <div className="messages">
                  {result.errors.map((issue, index) => (
                    <p className="error" key={`error-${index}`}>{issue.message ?? issue.code ?? "Generation error"}</p>
                  ))}
                  {result.warnings.map((warning) => <p className="warning" key={warning}>{warning}</p>)}
                </div>
              )}
            </>
          ) : <Empty text="Upload spec.md and events.ndjson to generate the contract preview." />}
        </article>
      </section>

      <section className="card pipeline" id="pipeline">
        <div className="section-heading"><span>03</span><div><h2>Pipeline timeline</h2><p>Ready to bind to run orchestration endpoints when they land.</p></div></div>
        <div className="stage-list">
          {stages.map((stage, index) => <div className="stage pending" key={stage}><b>{String(index + 1).padStart(2, "0")}</b><span>{stage}</span><small>Waiting for run API</small></div>)}
        </div>
      </section>

      <section className="insights" id="insights">
        <div><p className="eyebrow">ANALYTICS COPILOT</p><h2>Discuss the evidence in LibreChat</h2><p>Use LibreChat for exploration while the dashboard remains the source of truth for verified run artifacts and SQL evidence.</p></div>
        <a className="primary link" href="http://localhost:3080" target="_blank" rel="noreferrer">Start a conversation</a>
      </section>
      <footer>Context Compiler / UI integration baseline</footer>
    </main>
  );
}

function Status({ name, check }: { name: string; check: Check }) {
  return <div className="status-row"><i className={check.status} /><span><strong>{name}</strong><small>{check.detail}</small></span></div>;
}
function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}
function Empty({ text }: { text: string }) {
  return <div className="empty"><span></span><p>{text}</p></div>;
}
