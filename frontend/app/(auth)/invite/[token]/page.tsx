"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

interface Info { workspace: string; email: string; role: string; invited_by: string | null; has_account: boolean }

export default function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const [info, setInfo] = useState<Info | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`/api/auth/invite/${token}`).then(async (r) => {
      const j = await r.json().catch(() => ({}));
      if (r.ok) setInfo(j); else setErr(j.detail ?? "This invite can't be opened.");
    }).catch(() => setErr("Can't reach the server."));
  }, [token]);

  const accept = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    setBusy(true); setErr(null);
    const r = await fetch(`/api/auth/invite/${token}/accept`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ name: f.get("name") || null, password: f.get("password") || null }),
    });
    const j = await r.json().catch(() => ({}));
    setBusy(false);
    if (!r.ok) { setErr(typeof j.detail === "string" ? j.detail : "Check the fields and try again."); return; }
    router.push("/"); router.refresh();
  };

  if (!info) {
    return (
      <div className="auth-form">
        <h1>Join a workspace</h1>
        {err ? <div className="banner error">{err}</div> : <p className="muted">Checking your invite…</p>}
      </div>
    );
  }
  return (
    <form className="auth-form" onSubmit={accept}>
      <h1>Join {info.workspace}</h1>
      <p className="muted">{info.invited_by ? `${info.invited_by} invited` : "You're invited"} <b>{info.email}</b> as {info.role === "admin" ? "an admin" : "a member"}.</p>
      {info.has_account ? (
        <label>Password for {info.email}
          <input name="password" type="password" autoComplete="current-password" />
          <small className="muted">Not needed if you're already signed in as this address.</small>
        </label>
      ) : (
        <>
          <label>Your name<input name="name" required autoComplete="name" /></label>
          <label>Choose a password<input name="password" type="password" required minLength={10} autoComplete="new-password" />
            <small className="muted">At least 10 characters.</small></label>
        </>
      )}
      {err && <div className="banner error">{err}</div>}
      <button className="btn primary wide" disabled={busy}>{busy ? "Joining…" : `Join ${info.workspace}`}</button>
    </form>
  );
}
