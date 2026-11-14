"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { CrewActivity, CrewOverview } from "@/lib/api";
import { send } from "@/lib/client";
import { localTime } from "@/lib/format";
import { Avatar } from "./team";

const KIND: Record<CrewActivity["kind"], string> = { new_task: "New follow-up", due_soon: "Due soon", overdue: "Overdue",
  worse: "Getting worse", weekly: "Weekly note" };
const STATUS: Record<CrewActivity["status"], [string, string]> = { draft: ["Waiting for you", "watch"], sent: ["Sent", "good"],
  done: ["Done", "good"], failed: ["Not sent", "bad"], dismissed: ["Dismissed", "neutral-pill"] };
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function Item({ a, agents, canEdit, onDone }: { a: CrewActivity; agents: CrewOverview["agents"]; canEdit: boolean; onDone: () => void }) {
  const [open, setOpen] = useState(a.status === "draft");
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState(a.subject);
  const [body, setBody] = useState(a.body);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const agent = agents.find((x) => x.id === a.role)?.title ?? a.role;
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setNote(null);
    try { await fn(); onDone(); } catch (e) { setNote(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const [label, cls] = STATUS[a.status];
  return (
    <li className={`crew-item ${a.status}`}>
      <button className="crew-row" onClick={() => setOpen(!open)} aria-expanded={open}>
        <Avatar id={a.role} size={26} />
        <span className="crew-main">
          <b>{a.subject}</b>
          <span className="muted small">{agent} agent → {a.recipient ?? a.recipient_email ?? "nobody"} · {KIND[a.kind]} · <span suppressHydrationWarning>{localTime(a.created_at)}</span></span>
        </span>
        <span className={`pill ${cls}`}>{label}</span>
      </button>
      {open && (
        <div className="crew-body">
          {editing ? (
            <>
              <input value={subject} onChange={(e) => setSubject(e.target.value)} />
              <textarea rows={9} value={body} onChange={(e) => setBody(e.target.value)} />
            </>
          ) : <pre>{a.body}</pre>}
          <div className="crew-meta muted small">
            Written by {a.writer.startsWith("crewai:") ? `the agent (CrewAI · ${a.writer.split(":")[1].replace("anthropic/", "")})` : "a template"}{a.writer.includes("edited") ? ", edited" : ""}.
            {a.error && <span className="err"> {a.error}</span>}
          </div>
          {a.status === "draft" && canEdit && (
            <div className="row">
              {editing
                ? <button className="btn small" disabled={busy} onClick={() => act(async () => { await send(`/crew/activity/${a.id}`, "PUT", { subject, body }); setEditing(false); })}>Save</button>
                : <button className="btn small" disabled={busy} onClick={() => setEditing(true)}>Edit</button>}
              <button className="btn primary small" disabled={busy} onClick={() => act(async () => {
                const r = await send<{ ok: boolean; note: string | null }>(`/crew/activity/${a.id}/approve`, "POST");
                if (r.note) setNote(r.note);
              })}>{a.kind === "new_task" ? "Approve: create & send" : "Approve & send"}</button>
              <button className="linklike" disabled={busy} onClick={() => act(() => send(`/crew/activity/${a.id}/dismiss`, "POST"))}>Dismiss</button>
            </div>
          )}
          {note && <p className="small watch-text">{note}</p>}
        </div>
      )}
    </li>
  );
}

export function CrewView({ initial: c }: { initial: CrewOverview }) {
  const router = useRouter();
  const refresh = () => router.refresh();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const save = async (body: object) => {
    setBusy(true); setMsg(null);
    try { await send("/crew", "PUT", body); refresh(); } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const drafts = c.activity.filter((a) => a.status === "draft");
  const rest = c.activity.filter((a) => a.status !== "draft");
  const name = (id: string | null) => c.members.find((m) => m.id === id)?.name ?? "Nobody";

  return (
    <div className="stack">
      {msg && <div className="banner error">{msg}</div>}
      <section className="card crew-controls">
        <div className="seg-toggle" role="radiogroup" aria-label="How the crew works">
          {([["off", "Off"], ["draft", "Ask me first"], ["auto", "Send automatically"]] as const).map(([v, l]) => (
            <button key={v} role="radio" aria-checked={c.mode === v} className={c.mode === v ? "on" : ""}
              disabled={!c.can_edit || busy} onClick={() => save({ mode: v })}>{l}</button>
          ))}
        </div>
        <div className="crew-status small muted">
          <span>{c.writer.llm ? `Agents write with ${c.writer.model.replace("anthropic/", "")} via CrewAI` : "Agents write from templates. Add ANTHROPIC_API_KEY on the server for CrewAI agents to write with Claude"}</span>
          <span>{c.email_available ? "Email: on" : "Email isn't set up on this server: messages stay here"}</span>
          <span suppressHydrationWarning>{c.last_run_at ? `Last run ${localTime(c.last_run_at)}` : "Hasn't run yet"} · runs every morning</span>
        </div>
        {c.can_edit && (
          <div className="row">
            <button className="btn primary small" disabled={busy || c.mode === "off"}
              onClick={async () => { await send("/crew/run", "POST"); setMsg(null); setTimeout(refresh, 4000); }}>Run the crew now</button>
            <label className="small muted">Weekly note on&nbsp;
              <select value={c.weekly_day} disabled={busy} onChange={(e) => save({ weekly_day: Number(e.target.value) })}>
                {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </select>
            </label>
          </div>
        )}
      </section>

      <div className="crew-grid">
        {c.agents.length === 0 && <p className="muted">Hire a role on <a href="/team">Your team</a> to give it an agent.</p>}
        {c.agents.map((a) => (
          <div key={a.id} className="card crew-agent">
            <div className="crew-agent-head"><Avatar id={a.id} size={34} /><div><b>{a.title} agent</b><span className="muted small">Does the work of a {a.replaces.toLowerCase()}</span></div></div>
            <p className="small">{a.tagline}</p>
            <label className="small muted">Reports to
              <select value={a.owner ?? ""} disabled={!c.can_edit || busy} onChange={(e) => save({ owners: { [a.id]: e.target.value || null } })}>
                {c.members.map((m) => <option key={m.id} value={m.id}>{m.name}{a.owner_is_default && m.id === a.owner ? " (workspace owner)" : ""}</option>)}
              </select>
            </label>
            <div className="crew-stats small"><span><b>{a.week}</b> done this week</span><span><b>{a.drafts}</b> waiting</span></div>
            <span className="muted small">{a.owner ? `Sends follow-ups and reminders to ${name(a.owner)}` : "Pick someone to report to"}</span>
          </div>
        ))}
      </div>

      {drafts.length > 0 && (
        <section>
          <div className="eyebrow">Waiting for you ({drafts.length})</div>
          <ul className="crew-list card">{drafts.map((a) => <Item key={a.id} a={a} agents={c.agents} canEdit={c.can_edit} onDone={refresh} />)}</ul>
        </section>
      )}
      <section>
        <div className="eyebrow">What the crew did</div>
        {rest.length === 0 ? <p className="muted small">Nothing yet. The crew runs every morning, or press “Run the crew now”.</p>
          : <ul className="crew-list card">{rest.map((a) => <Item key={a.id} a={a} agents={c.agents} canEdit={c.can_edit} onDone={refresh} />)}</ul>}
      </section>
    </div>
  );
}
