import { type ModelsResponse } from "@/lib/api";
import { sapi } from "@/lib/server-api";
import { PageError } from "@/components/PageError";
import { MethodBadge } from "@/components/team";
import { ROLE_LABEL } from "@/lib/roles";

export const dynamic = "force-dynamic";
export const metadata = { title: "Models & knowledge · Fred" };

const MODEL_NAME: Record<string, string> = { lead_score: "Lead conversion", churn_gravity: "Churn gravity" };

export default async function ModelsPage() {
  let d: ModelsResponse;
  try {
    d = await sapi.models();
  } catch (e) {
    return <PageError title="Models & knowledge" error={e} />;
  }
  const kb = d.knowledge;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Models & knowledge</h1>
          <p>How the team works: which method does which job, how good each model is, and the knowledge it reasons with.</p>
        </div>
      </div>

      <div className="section-title">Method policy: cheapest method that does the job</div>
      <div className="card table-wrap">
        <table className="table">
          <thead><tr><th>Tier</th><th>Method</th><th>Used for</th><th>Cost</th></tr></thead>
          <tbody>
            {d.methods_policy.map((m) => (
              <tr key={m.tier}><td>{m.tier}</td><td><MethodBadge kind={m.kind} /></td><td>{m.used_for}</td><td className="muted">{m.cost}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="section-title">Model cards</div>
      <div className="two">
        {d.models.map((m) => (
          <div key={m.version} className="card pad">
            <h3>{MODEL_NAME[m.kind] ?? m.kind} <MethodBadge kind="ml" /></h3>
            <dl className="kv">
              <dt>Algorithm</dt><dd>{m.algorithm}</dd>
              <dt>Version</dt><dd className="mono">{m.version}</dd>
              <dt>Holdout AUC</dt><dd className="num">{m.metrics.auc.toFixed(3)}</dd>
              <dt>Brier score</dt><dd className="num">{m.metrics.brier.toFixed(3)}</dd>
              <dt>Calibration error</dt><dd className="num">{(m.metrics.calibration_error * 100).toFixed(1)} pts</dd>
              <dt>Base rate</dt><dd className="num">{(m.metrics.base_rate * 100).toFixed(1)}%</dd>
              <dt>Top-decile rate</dt><dd className="num">{(m.metrics.top_decile_rate * 100).toFixed(1)}% ({m.metrics.top_decile_lift.toFixed(1)}x)</dd>
              <dt>Train / test rows</dt><dd className="num">{m.n_train.toLocaleString("en-US")} / {m.n_test.toLocaleString("en-US")}</dd>
              <dt>Features</dt><dd className="note">{m.features.length}: {m.features.slice(0, 8).join(", ")}{m.features.length > 8 ? "…" : ""}</dd>
            </dl>
            <ul className="note" style={{ paddingLeft: 18 }}>{m.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          </div>
        ))}
      </div>
      <p className="note">
        Models retrain automatically whenever the data snapshot changes. Compute for this snapshot:{" "}
        {Object.entries(d.compute_ms).map(([k, v]) => `${k.replace("_", " ")} ${(v / 1000).toFixed(1)}s`).join(", ")}.
      </p>

      <div className="section-title">Knowledge base</div>
      <div className="stats-strip">
        {Object.entries(kb.status).map(([name, s]) => (
          <span key={name} className="chip">
            {name} v{s.version} · as of {s.as_of} · {s.stale ? <b style={{ color: "var(--bad)" }}>review overdue</b> : `review in ${s.review_every_days - s.age_days} days`}
          </span>
        ))}
      </div>
      <div className="two">
        <div className="card pad">
          <h3>Benchmarks</h3>
          {Object.entries(kb.benchmarks.metrics).map(([k, m]) => (
            <div key={k} className="bench-row">
              <span><b>{k.replace(/_/g, " ")}</b><span className="sub note">{m.basis}</span></span>
              <span className="num note">growth: {m.direction === "up" ? "≥" : "≤"} {m.bands.growth.target}</span>
            </div>
          ))}
        </div>
        <div className="card pad">
          <h3>Playbooks</h3>
          {kb.playbooks.map((p) => (
            <div key={p.id} className="bench-row">
              <span>
                <b>{p.id.replace(/_/g, " ")}</b> <span className="note">{ROLE_LABEL[p.role]}</span>
                <span className="sub note">When {p.when.map(([f, op, v]) => `${f} ${op} ${v}`).join(" and ")}</span>
                <span className="sub note">{p.frameworks.join(" · ")}</span>
              </span>
              <span className={`pill ${p.severity === "critical" || p.severity === "high" ? "bad" : p.severity === "medium" ? "watch" : "good"}`}>{p.severity}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
