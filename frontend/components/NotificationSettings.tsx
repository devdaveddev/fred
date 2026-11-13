"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { DigestItem, Notifications } from "@/lib/api";
import { send } from "@/lib/client";
import { localTime } from "@/lib/format";

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hourLabel = (h: number) => `${h % 12 === 0 ? 12 : h % 12}:00 ${h < 12 ? "am" : "pm"}`;
/** "Fri, Nov 14 · 8:00 am Asia/Kolkata", from the ISO string's own local date: same on server and browser. */
function nextLabel(iso: string, hour: number, tz: string) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const day = new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric", timeZone: "UTC" });
  return `${day} · ${hourLabel(hour)} ${tz.replace(/_/g, " ")}`;
}
/** Aliases like Asia/Calcutta and Asia/Kolkata are the same clock. */
function sameZone(a: string, b: string) {
  try {
    const t = new Date();
    const f = (z: string) => t.toLocaleString("en-US", { timeZone: z });
    return f(a) === f(b);
  } catch { return a === b; }
}
const STATE_LABEL: Record<string, string> = { improving: "Improving", worse: "Getting worse", resolved: "Resolved" };

function useAction(onDone: () => void) {
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async (key: string, fn: () => Promise<unknown>, ok?: (r: unknown) => string) => {
    setBusy(key); setMsg(null);
    try {
      const r = await fn();
      if (ok) setMsg({ ok: true, text: ok(r) });
      onDone();
    } catch (e) {
      setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) });
    } finally { setBusy(null); }
  };
  return { busy, msg, run };
}

function Preview({ p }: { p: NonNullable<Notifications["preview"]> }) {
  if (p.empty) {
    return <p className="muted">Nothing new since the last message, so tomorrow Fred would stay quiet.</p>;
  }
  const list = (title: string, items: DigestItem[], line: (i: DigestItem) => React.ReactNode) =>
    items.length > 0 && (
      <div className="digest-group">
        <div className="digest-h">{title}</div>
        <ul>{items.map((i) => <li key={i.id}>{line(i)}</li>)}</ul>
      </div>
    );
  return (
    <>
      {list("New", p.findings, (f) => (
        <><a href={new URL(f.url).pathname}>{f.title}</a>{f.impact && <span className="muted"> · {f.impact}</span>}
          <span className="muted small"> · {f.role}</span></>
      ))}
      {p.more_findings > 0 && <p className="muted small">…and {p.more_findings} more on Today.</p>}
      {list("Is the fix working?", p.task_updates, (t) => (
        <><b>{STATE_LABEL[t.state ?? ""] ?? t.state}</b>: {t.title} <span className="muted">({t.owner})</span></>
      ))}
      {list("Overdue", p.overdue, (t) => <>{t.title} <span className="muted">({t.owner}, {t.days} days late)</span></>)}
      {p.sync_problems.length > 0 && (
        <div className="digest-group">
          <div className="digest-h">Data problems</div>
          <ul>{p.sync_problems.map((s) => <li key={s.source}><b>{s.source}</b>: <span className="err">{s.error}</span></li>)}</ul>
        </div>
      )}
    </>
  );
}

export function NotificationSettings({ initial }: { initial: Notifications }) {
  const router = useRouter();
  const [n, setN] = useState(initial);
  useEffect(() => setN(initial), [initial]);
  const refresh = () => router.refresh();
  const { busy, msg, run } = useAction(refresh);
  const [hook, setHook] = useState("");
  const [browserTz, setBrowserTz] = useState<string | null>(null);
  useEffect(() => setBrowserTz(Intl.DateTimeFormat().resolvedOptions().timeZone), []);
  const edit = n.can_edit;
  const put = (body: object) => run("settings", () => send("/notifications", "PUT", body));
  const nothingConfigured = !n.slack.connected && !(n.email.available && n.email.enabled);

  return (
    <div className="stack">
      {msg && <div className={`banner ${msg.ok ? "ok" : "error"}`}>{msg.text}</div>}

      <section className="card digest-card">
        <div className="src-head">
          <div>
            <h3>Tomorrow&apos;s message</h3>
            <p className="muted small">
              {n.daily_sync ? "Data refresh and message" : "Message"}: {nextLabel(n.next_run, n.digest_hour, n.timezone)} · {n.preview?.summary ?? "preview unavailable"}
            </p>
          </div>
          {edit && (
            <button className="btn small" disabled={!!busy || nothingConfigured}
              title={nothingConfigured ? "Connect Slack or email first" : undefined}
              onClick={() => run("now", () => send<{ sent: string[]; failed: { to: string; error: string }[] }>("/notifications/send-now", "POST"),
                (r) => {
                  const x = r as { sent: string[]; failed: { to: string; error: string }[] };
                  return x.sent.length ? `Sent to ${x.sent.join(", ")}.` + (x.failed.length ? ` ${x.failed.length} failed; see history.` : "")
                    : `Nothing was delivered. ${x.failed[0]?.error ?? ""}`;
                })}>
              {busy === "now" ? "Sending…" : "Send it now"}
            </button>
          )}
        </div>
        {n.preview ? <Preview p={n.preview} /> : <p className="muted">Connect data to see a preview.</p>}
        {nothingConfigured && <p className="small watch-text">Nobody will receive this yet. Connect Slack or set up email below.</p>}
      </section>

      <div className="src-grid">
        <section className="card src-card">
          <div className="src-head"><b>Slack</b>{n.slack.connected
            ? <span className="pill good">Connected …{n.slack.hint}</span> : <span className="pill neutral-pill">Not connected</span>}</div>
          <p className="muted small">One message a day in the channel you choose, with links back to Fred.</p>
          {n.slack.connected ? (
            <div className="row">
              <button className="btn small" disabled={!!busy} onClick={() => run("slack-test", () => send("/notifications/test", "POST", { channel: "slack" }), () => "Test message sent to Slack.")}>Send a test</button>
              {edit && <button className="linklike" disabled={!!busy} onClick={() => run("slack-off", () => send("/notifications/slack", "DELETE"), () => "Slack disconnected.")}>Disconnect</button>}
            </div>
          ) : edit ? (
            <form className="inline-form" onSubmit={(e) => { e.preventDefault(); run("slack", () => send("/notifications/slack", "POST", { url: hook }), () => "Slack connected. Check the channel for a hello from Fred."); setHook(""); }}>
              <label className="grow">Incoming webhook URL
                <input type="password" autoComplete="off" placeholder="https://hooks.slack.com/services/…" value={hook} onChange={(e) => setHook(e.target.value)} required minLength={20} />
              </label>
              <button className="btn primary small" disabled={!!busy || !n.can_store_keys}>{busy === "slack" ? "Checking…" : "Connect"}</button>
            </form>
          ) : <p className="small muted">Ask a workspace admin to connect Slack.</p>}
          {!n.slack.connected && edit && (
            <details className="small muted"><summary>Where do I get this?</summary>
              In Slack: Apps → search &ldquo;Incoming WebHooks&rdquo; (or create an app at api.slack.com/apps with Incoming Webhooks on) → Add to a channel → copy the URL. Fred stores it encrypted and only shows the last four characters.
            </details>
          )}
        </section>

        <section className="card src-card">
          <div className="src-head"><b>Email</b>{!n.email.available
            ? <span className="pill neutral-pill">Not set up on this server</span>
            : n.email.enabled ? <span className="pill good">On</span> : <span className="pill neutral-pill">Off for workspace</span>}</div>
          {n.email.available ? (
            <>
              <p className="muted small">Each person gets the same news, with their own tasks first.</p>
              <label className="check">
                <input type="checkbox" checked={n.email.mine} disabled={!!busy || !n.email.enabled}
                  onChange={(e) => { setN({ ...n, email: { ...n.email, mine: e.target.checked } }); run("mine", () => send("/notifications/me", "PUT", { email_digest: e.target.checked })); }} />
                Send it to me ({n.email.address})
              </label>
              {edit && (
                <label className="check">
                  <input type="checkbox" checked={n.email.enabled} disabled={!!busy}
                    onChange={(e) => { setN({ ...n, email: { ...n.email, enabled: e.target.checked } }); put({ email_enabled: e.target.checked }); }} />
                  Email the workspace
                </label>
              )}
              <div className="row"><button className="btn small" disabled={!!busy} onClick={() => run("mail-test", () => send("/notifications/test", "POST", { channel: "email" }), () => `Test email sent to ${n.email.address}.`)}>Send me a test</button></div>
            </>
          ) : (
            <p className="muted small">The server needs an SMTP provider (SMTP_HOST in .env), for example Postmark, SES or Google Workspace. Slack works without it.</p>
          )}
        </section>

        <section className="card src-card">
          <div className="src-head"><b>Schedule</b></div>
          <label className="check">
            <input type="checkbox" checked={n.daily_sync} disabled={!edit || !!busy}
              onChange={(e) => { setN({ ...n, daily_sync: e.target.checked }); put({ daily_sync: e.target.checked }); }} />
            Refresh Stripe and HubSpot every day first
          </label>
          <div className="inline-form">
            <label>Time
              <select value={n.digest_hour} disabled={!edit || !!busy}
                onChange={(e) => { const h = Number(e.target.value); setN({ ...n, digest_hour: h }); put({ digest_hour: h }); }}>
                {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
              </select>
            </label>
            <label className="grow">Time zone<input value={n.timezone} readOnly /></label>
          </div>
          {edit && browserTz && !sameZone(browserTz, n.timezone) && (
            <button className="linklike" onClick={() => { setN({ ...n, timezone: browserTz }); put({ timezone: browserTz }); }}>Use my time zone ({browserTz})</button>
          )}
        </section>
      </div>

      <section className="card">
        <h3>History</h3>
        {n.log.length === 0 ? <p className="muted small">Nothing sent yet.</p> : (
          <table className="table compact">
            <thead><tr><th>When</th><th>To</th><th>What</th><th>Result</th></tr></thead>
            <tbody>{n.log.map((l, i) => (
              <tr key={i}>
                <td suppressHydrationWarning>{localTime(l.created_at)}</td>
                <td>{l.channel === "slack" ? "Slack" : l.recipient}</td>
                <td>{l.kind === "test" ? "Test" : l.subject.replace(/^Slack: /, "")}</td>
                <td>{l.status === "sent" ? <span className="pill good">Sent</span> : <span className="err small">{l.error}</span>}</td>
              </tr>))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
