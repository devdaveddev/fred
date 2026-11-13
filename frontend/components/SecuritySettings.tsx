"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { AuditEvent, SessionRow, SsoInfo } from "@/lib/api";
import { send } from "@/lib/client";
import { localTime } from "@/lib/format";

const ACTION_LABEL: Record<string, string> = {
  login: "Signed in", login_failed: "Failed sign-in", logout: "Signed out", signup: "Created the workspace",
  sso_login: "Signed in with SSO", session_revoked: "Revoked a session", password_login_blocked: "Password sign-in blocked (SSO enforced)",
  invite_sent: "Invited", invite_revoked: "Cancelled invite for", invite_accepted: "Joined", role_changed: "Changed role of",
  member_removed: "Removed", source_connected: "Connected", source_disconnected: "Disconnected", file_imported: "Imported a file",
  mapping_changed: "Changed account mapping", writeback_changed: "Changed CRM write-back", workspace_exported: "Exported all data",
  workspace_deleted: "Deleted the workspace", role_hired: "Hired role", role_released: "Released role",
  notifications_changed: "Changed notifications", slack_connected: "Connected Slack", slack_disconnected: "Disconnected Slack",
  sso_configured: "Configured SSO", sso_removed: "Removed SSO", document_created: "Created a document",
  budget_round_opened: "Opened a budget round", budget_submitted: "Submitted a budget", budget_approved: "Approved a budget",
  budget_returned: "Sent a budget back", budget_published: "Published the plan",
};
const browser = (ua: string | null) => !ua ? "Unknown device"
  : `${/Firefox/.test(ua) ? "Firefox" : /Edg\//.test(ua) ? "Edge" : /Chrome/.test(ua) ? "Chrome" : /Safari/.test(ua) ? "Safari" : /curl|python|httpx/i.test(ua) ? "Script" : "Browser"}`
    + ` on ${/Mac OS/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Android/.test(ua) ? "Android" : /iPhone|iPad/.test(ua) ? "iOS" : /Linux/.test(ua) ? "Linux" : "unknown OS"}`;

function Sso({ info, onDone }: { info: SsoInfo; onDone: () => void }) {
  const c = info.config;
  const [f, setF] = useState({ issuer: c?.issuer ?? "", client_id: c?.client_id ?? "", client_secret: "",
    domains: (c?.domains ?? [info.your_domain]).join(", "), default_role: c?.default_role ?? "member",
    enforce: c?.enforce ?? false, enabled: c?.enabled ?? true });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const save = async (e: React.FormEvent) => {
    e.preventDefault(); setBusy(true); setMsg(null);
    try {
      const r = await send<{ verified: boolean }>("/sso", "PUT", { ...f, client_secret: f.client_secret || null,
        domains: f.domains.split(/[\s,]+/).filter(Boolean) });
      setMsg({ ok: true, text: r.verified ? "Saved." : "Saved. Sign out and use “Sign in with SSO” once to confirm it works; then you can enforce it." });
      setF({ ...f, client_secret: "" }); onDone();
    } catch (err) { setMsg({ ok: false, text: err instanceof Error ? err.message : String(err) }); }
    finally { setBusy(false); }
  };
  const ro = !info.can_edit;
  return (
    <section className="card src-card">
      <div className="src-head"><b>Single sign-on</b>
        {c ? (c.verified_at ? <span className="pill good">{c.enforce ? "Required" : "On"} · verified</span> : <span className="pill watch">Not yet verified</span>)
          : <span className="pill neutral-pill">Off</span>}</div>
      <p className="muted small">Any OpenID Connect provider: Google Workspace, Okta, Microsoft Entra ID, OneLogin. People with your email domain sign in through it, and new people get an account automatically.</p>
      <form className="sso-form" onSubmit={save}>
        <label>Issuer URL<input value={f.issuer} disabled={ro} required placeholder="https://accounts.google.com" onChange={(e) => setF({ ...f, issuer: e.target.value })} /></label>
        <label>Client ID<input value={f.client_id} disabled={ro} required onChange={(e) => setF({ ...f, client_id: e.target.value })} /></label>
        <label>Client secret<input type="password" autoComplete="off" value={f.client_secret} disabled={ro} placeholder={c ? "Unchanged (stored encrypted)" : ""} onChange={(e) => setF({ ...f, client_secret: e.target.value })} /></label>
        <label>Email domains<input value={f.domains} disabled={ro} required onChange={(e) => setF({ ...f, domains: e.target.value })} /></label>
        <label>New people join as
          <select value={f.default_role} disabled={ro} onChange={(e) => setF({ ...f, default_role: e.target.value })}>
            <option value="member">Member</option><option value="admin">Admin</option>
          </select>
        </label>
        <label className="check"><input type="checkbox" checked={f.enabled} disabled={ro} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> Enabled</label>
        <label className="check"><input type="checkbox" checked={f.enforce} disabled={ro || !c?.verified_at} onChange={(e) => setF({ ...f, enforce: e.target.checked })} />
          Require SSO for these domains (turns off password sign-in){!c?.verified_at && <span className="muted"> · available after one successful SSO sign-in</span>}</label>
        <p className="small muted">Redirect URI to register with your provider: <code>{info.callback_url}</code></p>
        {msg && <div className={`banner ${msg.ok ? "ok" : "error"}`}>{msg.text}</div>}
        {info.can_edit && (
          <div className="row">
            <button className="btn primary small" disabled={busy || !info.can_store_keys}>{busy ? "Checking provider…" : "Save"}</button>
            {c && <button type="button" className="linklike" disabled={busy} onClick={async () => { await send("/sso", "DELETE"); onDone(); }}>Remove SSO</button>}
          </div>
        )}
        {ro && <p className="small muted">Only the workspace owner can change single sign-on.</p>}
      </form>
    </section>
  );
}

export function SecuritySettings({ sso, sessions, audit, isOwner }: {
  sso: SsoInfo; sessions: SessionRow[]; audit: { events: AuditEvent[]; groups: string[]; next: number | null } | null; isOwner: boolean;
}) {
  const router = useRouter();
  const refresh = () => router.refresh();
  const [events, setEvents] = useState(audit?.events ?? []);
  const [next, setNext] = useState(audit?.next ?? null);
  const [group, setGroup] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const loadAudit = async (g: string, before?: number) => {
    const q = new URLSearchParams(); if (g) q.set("group", g); if (before) q.set("before", String(before));
    const r = await fetch(`/api/security/audit?${q}`); const j = await r.json();
    setEvents(before ? [...events, ...j.events] : j.events); setNext(j.next);
  };

  return (
    <div className="stack">
      <Sso info={sso} onDone={refresh} />

      <section className="card">
        <div className="src-head"><h3>Your sessions</h3>
          {sessions.length > 1 && <button className="btn small" onClick={async () => { await send("/security/sessions/revoke-others", "POST"); refresh(); }}>Sign out everywhere else</button>}</div>
        <table className="table compact">
          <thead><tr><th>Device</th><th>Workspace</th><th>Last active</th><th>Signed in</th><th /></tr></thead>
          <tbody>{sessions.map((s) => (
            <tr key={s.id}>
              <td>{browser(s.user_agent)}{s.current && <span className="pill good"> This device</span>}<br /><span className="muted small">{s.ip ?? ""}{s.auth_method === "sso" ? " · SSO" : ""}</span></td>
              <td>{s.workspace}</td>
              <td suppressHydrationWarning className="small">{s.last_seen_at ? localTime(s.last_seen_at) : "—"}</td>
              <td suppressHydrationWarning className="small">{localTime(s.created_at, false)}</td>
              <td className="r">{!s.current && <button className="linklike" onClick={async () => { await send(`/security/sessions/${s.id}`, "DELETE"); refresh(); }}>Sign out</button>}</td>
            </tr>))}
          </tbody>
        </table>
      </section>

      {audit && (
        <section className="card">
          <div className="src-head"><h3>Audit log</h3>
            <select value={group} onChange={(e) => { setGroup(e.target.value); loadAudit(e.target.value); }}>
              <option value="">Everything</option>
              {audit.groups.map((g) => <option key={g} value={g}>{g[0].toUpperCase() + g.slice(1)}</option>)}
            </select></div>
          <p className="muted small">Every sign-in, permission change, data connection and export. Kept for the life of the workspace.</p>
          <table className="table compact">
            <thead><tr><th>When</th><th>Who</th><th>What</th><th>From</th></tr></thead>
            <tbody>{events.map((e) => (
              <tr key={e.id}>
                <td suppressHydrationWarning className="small">{localTime(e.created_at)}</td>
                <td className="small">{e.actor ?? "system"}</td>
                <td className={e.action.includes("failed") || e.action.includes("blocked") ? "err" : ""}>{ACTION_LABEL[e.action] ?? e.action}{e.target ? <span className="muted"> {e.target}</span> : null}</td>
                <td className="small muted">{e.ip ?? ""}</td>
              </tr>))}
            </tbody>
          </table>
          {next && <button className="btn small" onClick={() => loadAudit(group, next)}>Load more</button>}
        </section>
      )}

      {isOwner && (
        <section className="card src-card danger-zone">
          <b>Your data</b>
          <p className="muted small">Download everything this workspace holds as CSV files (stored credentials are never included).</p>
          <div className="row"><a className="btn small" href="/api/security/export">Download export (.zip)</a></div>
          <hr />
          <b className="err">Delete this workspace</b>
          <p className="muted small">Permanently deletes all data, documents, connections and settings. People keep their Fred account only if they belong to another workspace. This cannot be undone.</p>
          <form className="inline-form" onSubmit={async (e) => {
            e.preventDefault(); setErr(null);
            try { await send("/security/delete-workspace", "POST", { confirm }); router.push("/login"); router.refresh(); }
            catch (x) { setErr(x instanceof Error ? x.message : String(x)); }
          }}>
            <label className="grow">Type the workspace name to confirm<input value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
            <button className="btn small danger" disabled={!confirm}>Delete permanently</button>
          </form>
          {err && <p className="err small">{err}</p>}
        </section>
      )}
    </div>
  );
}
