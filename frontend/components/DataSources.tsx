"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { GlAccount, Sources } from "@/lib/api";
import { send } from "@/lib/client";
import { fmt, localTime } from "@/lib/format";

const READINESS_ROLE: Record<string, string> = {
  metrics: "Business analyst · Founder's office", forecast: "Founder's office", lead_model: "Lead generator",
  churn_model: "Business analyst", marketing: "Marketing analyst", segments: "Market researcher", voice: "Business analyst",
};
const READINESS_LABEL: Record<string, string> = {
  metrics: "Revenue metrics", forecast: "Forecast and planning", lead_model: "Lead scoring", churn_model: "Churn prediction",
  marketing: "Channel analysis", segments: "Segment discovery", voice: "Voice of customer",
};
const GROUPS = [["revenue", "Revenue"], ["cogs", "Cost of revenue"], ["sm", "Sales & marketing"], ["rnd", "R&D"], ["ga", "G&A"]];
const KINDS = [["revenue", "Revenue"], ["cogs", "Cost of revenue"], ["payroll", "Payroll"], ["nonpayroll", "Other expense"]];

type CatalogItem = Sources["catalog"][number];
const CATEGORY: [string, string][] = [["billing", "Billing"], ["accounting", "Accounting"], ["crm", "CRM"],
  ["payroll", "Payroll"], ["ads", "Ad platforms"], ["support", "Support & feedback"]];

function ApiCard({ s, src, busy, onDone }: { s: CatalogItem; src: Sources; busy: boolean; onDone: () => void }) {
  const conn = src.connections.find((c) => c.kind === s.kind);
  const lastRun = src.runs.find((r) => r.source === s.kind);
  const lastPush = src.runs.find((r) => r.source === `writeback:${s.kind}`);
  const [key, setKey] = useState("");
  const [cfg, setCfg] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const go = async (fn: () => Promise<unknown>, ok?: string) => {
    setWorking(true); setErr(null); setNote(null);
    try { await fn(); setKey(""); if (ok) setNote(ok); onDone(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setWorking(false); }
  };
  const missing = conn ? s.fields.filter((f) => f.required && !conn.config?.[f.name]) : [];
  const fieldInputs = (fields: CatalogItem["fields"]) => fields.map((f) => (
    <label key={f.name} className="grow">{f.label}
      <input value={cfg[f.name] ?? ""} required={f.required} onChange={(e) => setCfg({ ...cfg, [f.name]: e.target.value })} />
    </label>
  ));
  return (
    <div className="card src-card">
      <div className="src-head"><b>{s.name}</b>{conn ? <span className="pill good">Connected {conn.secret_hint === "OAuth" ? "" : conn.secret_hint}</span>
        : s.available ? <span className="pill neutral-pill">Not connected</span> : <span className="pill neutral-pill">Not enabled here</span>}</div>
      <p className="muted small">{s.gives}</p>
      {lastRun && (
        <p className="small">
          {lastRun.status === "running" ? "Syncing now…" : lastRun.status === "ok"
            ? <span suppressHydrationWarning>Last sync {localTime(lastRun.finished_at!)}</span> : <span className="err">Last sync failed: {lastRun.error}</span>}
        </p>
      )}
      {conn && missing.length > 0 && (
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); go(() => send(`/sources/${s.kind}/config`, "PUT", { config: cfg }), "Saved. Syncing now."); }}>
          {fieldInputs(s.fields)}
          <button className="btn primary small" disabled={working}>Save</button>
        </form>
      )}
      {conn ? (
        <>
          <div className="row">
            <button className="btn small" disabled={working || busy || missing.length > 0} onClick={() => go(() => send(`/sources/${s.kind}/sync`, "POST"))}>Sync now</button>
            <button className="linklike" disabled={working} onClick={() => go(() => send(`/sources/${s.kind}`, "DELETE"))}>Disconnect</button>
          </div>
          {s.writeback && (
            <div className="writeback">
              <label className="check">
                <input type="checkbox" checked={!!conn.config?.writeback} disabled={working}
                  onChange={(e) => go(() => send(`/sources/${s.kind}/config`, "PUT", { config: { writeback: e.target.checked } }))} />
                Write lead scores and churn risk back to {s.name} after each sync
              </label>
              {conn.config?.writeback && (
                <div className="row">
                  <button className="btn small" disabled={working}
                    onClick={() => go(async () => {
                      const r = await send<{ ok: boolean; error?: string; stats?: Record<string, number> }>(`/sources/${s.kind}/writeback`, "POST");
                      if (!r.ok) throw new Error(r.error);
                    }, "Pushed to " + s.name + ".")}>Push now</button>
                  {lastPush && <span className="small muted" suppressHydrationWarning>
                    {lastPush.status === "ok" ? `Last push ${localTime(lastPush.finished_at!)}` : <span className="err">Last push failed: {lastPush.error}</span>}</span>}
                </div>
              )}
            </div>
          )}
        </>
      ) : !s.available ? (
        <p className="small muted">This server&apos;s operator needs to register a {s.name} app first (set {s.kind.toUpperCase()}_CLIENT_ID and _CLIENT_SECRET). Until then, upload a CSV export below.</p>
      ) : s.auth === "oauth" ? (
        <div className="row">
          <button className="btn primary small" disabled={working || !src.can_store_keys}
            onClick={() => go(async () => {
              const r = await send<{ redirect: string }>(`/sources/${s.kind}/oauth/start`, "POST");
              window.location.href = r.redirect;
            })}>{working ? "Opening…" : `Sign in with ${s.name}`}</button>
        </div>
      ) : (
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); go(() => send(`/sources/${s.kind}/connect`, "POST", { key, config: cfg })); }}>
          {fieldInputs(s.fields)}
          <label className="grow">{s.key_label ?? "API key"}
            <input type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} required minLength={8} />
          </label>
          <button className="btn primary small" disabled={working || !src.can_store_keys}>{working ? "Checking…" : "Connect"}</button>
        </form>
      )}
      {!conn && <details className="small muted"><summary>How to connect</summary>{s.how}</details>}
      {err && <p className="err small">{err}</p>}
      {note && <p className="small good-text">{note}</p>}
    </div>
  );
}

function UploadCard({ kind, t, onDone }: { kind: string; t: Sources["templates"][string]; onDone: () => void }) {
  const [state, setState] = useState<{ busy?: boolean; ok?: string; err?: string }>({});
  const upload = async (file: File) => {
    setState({ busy: true });
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/imports/${kind}`, { method: "POST", body: fd });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) { setState({ err: typeof j.detail === "string" ? j.detail : "Upload failed." }); return; }
    const stats = Object.entries(j.stats ?? {}).filter(([, v]) => typeof v === "number").map(([k, v]) => `${(v as number).toLocaleString()} ${k.replace(/_/g, " ")}`).join(" · ");
    setState({ ok: stats + (j.warnings?.length ? ` · ${j.warnings.join(" ")}` : "") });
    onDone();
  };
  return (
    <div className="card src-card">
      <b>{t.label}</b>
      <p className="muted small">{t.help}</p>
      <p className="mono small muted">{t.columns.join(", ")}</p>
      <div className="row">
        <label className="btn small primary file-btn">
          {state.busy ? "Importing…" : "Upload CSV"}
          <input type="file" accept=".csv,text/csv" hidden disabled={state.busy}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        </label>
        <a className="small" href={`/api/imports/${kind}/sample`}>Download a sample</a>
      </div>
      {state.ok && <p className="small ok">✓ {state.ok}</p>}
      {state.err && <p className="small err">{state.err}</p>}
    </div>
  );
}

function Mapping({ onChange }: { onChange: () => void }) {
  const [rows, setRows] = useState<GlAccount[] | null>(null);
  const [depts, setDepts] = useState<string[]>([]);
  const load = async () => {
    const r = await fetch("/api/mapping/accounts");
    if (r.ok) { const j = await r.json(); setRows(j.accounts); setDepts(j.departments); }
  };
  useEffect(() => { load(); }, []);
  if (!rows || rows.length === 0) return null;
  const save = async (a: GlAccount, patch: Partial<GlAccount>) => {
    const next = { ...a, ...patch };
    if (next.kind === "payroll" && !next.department) next.department = depts[0];
    const r = await fetch(`/api/mapping/accounts/${encodeURIComponent(a.code)}`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ pl_group: next.pl_group, kind: next.kind, department: next.department }),
    });
    if (r.ok) { await load(); onChange(); }
  };
  return (
    <section>
      <div className="eyebrow">Chart of accounts mapping</div>
      <p className="muted small">Fred mapped each account by rule or with its language model. Check the ones marked &ldquo;guess&rdquo;; your changes are kept on every re-import.</p>
      <div className="card table-wrap">
        <table className="table">
          <thead><tr><th>Account</th><th>Line</th><th>Type</th><th>Department</th><th>How</th><th className="r">Total</th></tr></thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.code} className={a.mapped_by === "model" || a.mapped_by === "default" ? "review" : ""}>
                <td><b>{a.code !== a.name ? `${a.code} ` : ""}{a.name}</b>{a.section && <span className="sub">{a.section}</span>}</td>
                <td><select value={a.pl_group} onChange={(e) => save(a, { pl_group: e.target.value })}>{GROUPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
                <td><select value={a.kind} onChange={(e) => save(a, { kind: e.target.value })}>{KINDS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></td>
                <td>{a.kind === "payroll" ? <select value={a.department ?? ""} onChange={(e) => save(a, { department: e.target.value })}>{depts.map((d) => <option key={d}>{d}</option>)}</select> : <span className="muted">—</span>}</td>
                <td><span className={`pill ${a.mapped_by === "user" ? "good" : a.mapped_by === "rule" || a.mapped_by === "seed" ? "neutral-pill" : "watch"}`}>{a.mapped_by === "user" ? "you" : a.mapped_by === "rule" || a.mapped_by === "seed" ? "rule" : "guess"}</span></td>
                <td className="r num">{fmt(a.total, "currency")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function DataSources({ initial }: { initial: Sources }) {
  const router = useRouter();
  const params = useSearchParams();
  const [src, setSrc] = useState(initial);
  const refresh = async () => {
    const r = await fetch("/api/sources");
    if (r.ok) setSrc(await r.json());
    router.refresh();
  };
  const running = src.runs.some((r) => r.status === "running");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(refresh, 3000);
    return () => clearInterval(t);
  }, [running]); // eslint-disable-line react-hooks/exhaustive-deps

  const ready = Object.entries(src.readiness);
  return (
    <>
      {src.data_source === "demo" && (
        <div className="banner">You&apos;re looking at a demo company. The first time you connect or upload your own data, the demo is removed and your team starts on your numbers.</div>
      )}
      {!src.can_store_keys && <div className="banner error">This server has no encryption key configured, so API connections are disabled. CSV uploads still work.</div>}

      {ready.length > 0 && (
        <section>
          <div className="eyebrow">What your team can do with the data you have</div>
          <div className="ready-grid">
            {ready.map(([k, r]) => (
              <div key={k} className={`card ready ${r.ok ? "is-ok" : ""}`}>
                <b>{r.ok ? "✓" : "○"} {READINESS_LABEL[k] ?? k}</b>
                <span className="muted small">{READINESS_ROLE[k]}</span>
                {!r.ok && <span className="small">Needs: {r.needs} · have {r.have}</span>}
              </div>
            ))}
          </div>
        </section>
      )}

      {(params.get("connected") || params.get("source_error")) && (
        <div className={`banner ${params.get("source_error") ? "error" : "ok"}`}>
          {params.get("source_error") ?? `${src.catalog.find((c) => c.kind === params.get("connected"))?.name ?? "Source"} connected. The first sync is running.`}
        </div>
      )}
      {CATEGORY.map(([cat, label]) => {
        const items = src.catalog.filter((c) => c.category === cat);
        return items.length ? (
          <section key={cat}>
            <div className="eyebrow">{label}</div>
            <div className="src-grid">{items.map((s) => <ApiCard key={s.kind} s={s} src={src} busy={running} onDone={refresh} />)}</div>
          </section>
        ) : null;
      })}

      <section>
        <div className="eyebrow">Upload files (QuickBooks, Xero, Gusto, Rippling, ad platforms, surveys…)</div>
        <div className="src-grid">{Object.entries(src.templates).map(([k, t]) => <UploadCard key={k} kind={k} t={t} onDone={refresh} />)}</div>
      </section>

      <Mapping onChange={refresh} />

      {src.runs.length > 0 && (
        <section>
          <div className="eyebrow">History</div>
          <ul className="card plain-list">
            {src.runs.slice(0, 12).map((r) => (
              <li key={r.id} className="doc-row">
                <b>{r.source.startsWith("writeback:") ? `Write-back to ${src.catalog.find((c) => c.kind === r.source.slice(10))?.name ?? r.source.slice(10)}`
                  : src.catalog.find((c) => c.kind === r.source)?.name ?? r.source.replace("csv:", "").replace(/_/g, " ") + " upload"}</b>
                <span className="muted small" suppressHydrationWarning>{localTime(r.started_at)}</span>
                <span className={`pill ${r.status === "ok" ? "good" : r.status === "error" ? "bad" : "watch"}`}>{r.status === "ok" ? "Done" : r.status === "error" ? "Failed" : "Running"}</span>
                {r.error && <span className="err small">{r.error}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
