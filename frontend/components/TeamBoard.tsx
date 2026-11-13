"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Role } from "@/lib/api";
import { Avatar } from "./team";

const usd = (v: number) => `$${Math.round(v / 1000)}K`;

export function TeamBoard({ initial, welcome }: { initial: Role[]; welcome: boolean }) {
  const [roles, setRoles] = useState(initial);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const router = useRouter();
  const hired = roles.filter((r) => r.hired);
  const covered = hired.reduce((a, r) => a + r.salary_equivalent, 0);

  const toggle = async (r: Role) => {
    setBusy(r.id);
    setErr(null);
    const res = await fetch(`/api/team/${r.id}/${r.hired ? "release" : "hire"}`, { method: "POST" });
    if (res.ok) {
      setRoles((await res.json()).roles);
      router.refresh(); // sidebar updates with the new role's pages
    } else {
      const j = await res.json().catch(() => ({}));
      setErr(typeof j.detail === "string" ? j.detail : "Could not update your team.");
    }
    setBusy(null);
  };

  return (
    <>
      {welcome && (
        <div className="welcome">
          <h2>Your workspace is ready.</h2>
          <p>Pick who you want on your team. Each role starts working on your data as soon as you add it, and you can change your mind anytime.</p>
        </div>
      )}
      <div className="team-summary">
        <span><b>{hired.length}</b> of {roles.length} roles on your team</span>
        {covered > 0 && <span className="muted">Covers work that usually takes about {usd(covered)} a year in salaries</span>}
        {hired.length > 0 && <Link href="/" className="btn primary">See today&apos;s briefing →</Link>}
      </div>
      {err && <div className="banner error">{err}</div>}
      <div className="role-grid">
        {roles.map((r) => (
          <article key={r.id} className={`card role-card ${r.hired ? "is-hired" : ""}`}>
            <header>
              <Avatar id={r.id} size={44} />
              <div>
                <h3>{r.title}</h3>
                <span className="muted small">Does the work of: {r.replaces}</span>
              </div>
            </header>
            <p className="tagline">{r.tagline}</p>
            <ul className="delivers">{r.delivers.map((d) => <li key={d}>{d}</li>)}</ul>
            <div className="role-meta">
              <span>Works with: {r.needs.join(", ")}</span>
              <span>How: {r.methods.join(", ")}</span>
            </div>
            <footer>
              {r.hired ? (
                <>
                  <span className="hired-badge">On your team</span>
                  <button className="linklike" disabled={busy === r.id} onClick={() => toggle(r)}>Remove</button>
                </>
              ) : (
                <button className="btn primary" disabled={busy === r.id} onClick={() => toggle(r)}>
                  {busy === r.id ? "Adding…" : `Add ${r.title.toLowerCase()}`}
                </button>
              )}
            </footer>
          </article>
        ))}
      </div>
    </>
  );
}
