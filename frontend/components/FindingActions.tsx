"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Finding } from "@/lib/api";
import { inDays, send } from "@/lib/client";

export function FindingActions({ f, me }: { f: Finding; me: string }) {
  const router = useRouter();
  const [mode, setMode] = useState<"idle" | "assign" | "mute">("idle");
  const [owner, setOwner] = useState(me);
  const [due, setDue] = useState(inDays(7));
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [people, setPeople] = useState<{ name: string; email: string }[]>([]);
  useEffect(() => {
    if (mode !== "assign" || people.length) return;
    fetch("/api/members").then((r) => r.ok ? r.json() : { members: [] }).then((j) => setPeople(j.members ?? [])).catch(() => {});
  }, [mode, people.length]);

  const go = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      setMode("idle");
      router.refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (f.tasked) return <span className="assigned">✓ Assigned. Track it in Follow-ups</span>;
  if (mode === "assign") {
    return (
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); go(() => send("/tasks", "POST", { finding_id: f.id, owner, due_date: due })); }}>
        <label>Owner<input value={owner} list={`people-${f.id}`} onChange={(e) => setOwner(e.target.value)} required /></label>
        <datalist id={`people-${f.id}`}>{people.map((p) => <option key={p.email} value={p.name}>{p.email}</option>)}</datalist>
        <label>Due<input type="date" value={due} onChange={(e) => setDue(e.target.value)} required /></label>
        <button className="btn primary small" disabled={busy}>Assign</button>
        <button type="button" className="linklike" onClick={() => setMode("idle")}>Cancel</button>
        {err && <span className="err">{err}</span>}
      </form>
    );
  }
  if (mode === "mute") {
    return (
      <form className="inline-form" onSubmit={(e) => { e.preventDefault(); go(() => send("/findings/mute", "POST", { finding_id: f.id, note, days: 90 })); }}>
        <label className="grow">Why is this expected?
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. We chose to front-load events spend this quarter" required minLength={3} />
        </label>
        <button className="btn small" disabled={busy}>Don&apos;t flag for 90 days</button>
        <button type="button" className="linklike" onClick={() => setMode("idle")}>Cancel</button>
        {err && <span className="err">{err}</span>}
      </form>
    );
  }
  return (
    <span className="row">
      <button className="btn small primary" onClick={() => setMode("assign")}>Assign</button>
      <button className="btn small" onClick={() => setMode("mute")}>It&apos;s expected</button>
    </span>
  );
}
