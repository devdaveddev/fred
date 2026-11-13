"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Muted, Task } from "@/lib/api";
import { send } from "@/lib/client";
import { Avatar } from "./team";

const STATE: Record<string, string> = { resolved: "good", improving: "good", worse: "bad", flat: "neutral-pill", waiting: "neutral-pill", open: "watch" };
const STATE_LABEL: Record<string, string> = { resolved: "Resolved", improving: "Improving", worse: "Worse", flat: "No change yet", waiting: "Measuring", open: "Still flagged" };

export function TaskList({ tasks, muted }: { tasks: Task[]; muted: Muted[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const act = async (id: string, body: object, path = `/tasks/${id}`, method: "PATCH" | "POST" = "PATCH") => {
    setBusy(id);
    try { await send(path, method, body); router.refresh(); } finally { setBusy(null); }
  };
  const open = tasks.filter((t) => t.status === "open" || t.status === "in_progress");
  const closed = tasks.filter((t) => !open.includes(t));
  const due = (d: string) => new Date(d + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });

  const row = (t: Task) => (
    <li key={t.id} className={`task ${t.overdue ? "late" : ""}`}>
      <Avatar id={t.module} size={30} />
      <div className="task-main">
        <b>{t.title}</b>
        <span className="muted small">{t.owner} · due {due(t.due_date)}{t.overdue ? " · overdue" : ""}</span>
        <span className="small"><span className={`pill ${STATE[t.progress.state]}`}>{STATE_LABEL[t.progress.state]}</span> {t.progress.text}</span>
      </div>
      <div className="task-actions">
        {t.status === "open" && <button className="btn small" disabled={busy === t.id} onClick={() => act(t.id, { status: "in_progress" })}>Start</button>}
        {(t.status === "open" || t.status === "in_progress") && (
          <>
            <button className="btn small primary" disabled={busy === t.id} onClick={() => act(t.id, { status: "done" })}>Done</button>
            <button className="linklike" disabled={busy === t.id} onClick={() => act(t.id, { status: "cancelled" })}>Drop</button>
          </>
        )}
        {(t.status === "done" || t.status === "cancelled") && <span className="muted small">{t.status === "done" ? "Done" : "Dropped"}</span>}
      </div>
    </li>
  );

  return (
    <>
      {open.length === 0 && closed.length === 0 && (
        <div className="empty">
          <div className="empty-icon">◌</div>
          <h3>No follow-ups yet</h3>
          <p>Open any item on Today and choose Assign. Fred keeps track of it and tells you whether the fix worked.</p>
        </div>
      )}
      {open.length > 0 && (<section><div className="eyebrow">In progress</div><ul className="card tasks">{open.map(row)}</ul></section>)}
      {closed.length > 0 && (<section><div className="eyebrow">Closed</div><ul className="card tasks">{closed.map(row)}</ul></section>)}
      {muted.length > 0 && (
        <section>
          <div className="eyebrow">Marked as expected (not flagged)</div>
          <ul className="card tasks">
            {muted.map((m) => (
              <li key={m.finding_id} className="task">
                <div className="task-main">
                  <b>{m.label}</b>
                  <span className="muted small">&ldquo;{m.note}&rdquo; · until {due(m.suppress_until)}</span>
                </div>
                <div className="task-actions">
                  <button className="linklike" disabled={busy === m.finding_id}
                    onClick={() => act(m.finding_id, {}, `/findings/${m.finding_id}/unmute`, "POST")}>Flag again</button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
