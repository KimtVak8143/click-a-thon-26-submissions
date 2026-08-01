import { ChangeEvent, useCallback, useEffect, useState } from "react";

type Check = { status: "loading" | "ok" | "error"; detail: string };
type Profile = {
  file: { sha256: string; total_line_count: number; valid_row_count: number };
  observed_window?: { start?: string; end?: string };
  events?: Array<{ event_name?: string; row_count?: number }>;
  fields?: unknown[];
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
  const [file, setFile] = useState<File | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [profiling, setProfiling] = useState(false);

  const refresh = useCallback(async () => {
    const [apiCheck, clickhouseCheck] = await Promise.all([
      getCheck("/health"),
      getCheck("/health/clickhouse"),
    ]);
    setApi(apiCheck);
    setClickhouse(clickhouseCheck);
  }, []);

  useEffect(() => void refresh(), [refresh]);

  const selectFile = (event: ChangeEvent<HTMLInputElement>) => {
    setFile(event.target.files?.[0] ?? null);
    setProfile(null);
    setUploadError("");
  };

  const createProfile = async () => {
    if (!file) return;
    setProfiling(true);
    setUploadError("");
    const body = new FormData();
    body.append("events", file);
    try {
      const response = await fetch("/compiler-api/profiles", { method: "POST", body });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail?.message ?? `HTTP ${response.status}`);
      setProfile(data);
    } catch (error) {
      setUploadError(error instanceof Error ? error.message : "Profiling failed");
    } finally {
      setProfiling(false);
    }
  };

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
          <div className="section-heading"><span>01</span><div><h2>Profile event sample</h2><p>Uses the existing deterministic NDJSON profiler.</p></div></div>
          <label className="dropzone">
            <input type="file" accept=".ndjson,application/x-ndjson" onChange={selectFile} />
            <strong>{file ? file.name : "Choose an NDJSON file"}</strong>
            <small>{file ? `${(file.size / 1024).toFixed(1)} KB` : "Maximum size follows backend configuration"}</small>
          </label>
          <button className="primary" disabled={!file || profiling || api.status !== "ok"} onClick={createProfile}>
            {profiling ? "Profiling..." : "Generate source profile"}
          </button>
          {uploadError && <p className="error">{uploadError}</p>}
        </article>

        <article className="card result-card">
          <div className="section-heading"><span>02</span><div><h2>Source profile</h2><p>Bounded, deterministic, and safe for agent context.</p></div></div>
          {profile ? (
            <div className="metrics">
              <Metric label="Valid rows" value={profile.file.valid_row_count.toLocaleString()} />
              <Metric label="Lines scanned" value={profile.file.total_line_count.toLocaleString()} />
              <Metric label="Event types" value={String(profile.events?.length ?? "-")} />
              <Metric label="Fields" value={String(profile.fields?.length ?? "-")} />
              <div className="hash"><span>SHA-256</span><code>{profile.file.sha256}</code></div>
            </div>
          ) : <Empty text="Upload a sample to inspect its event and field profile." />}
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
