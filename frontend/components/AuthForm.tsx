"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

type Mode = "login" | "signup";

export function AuthForm({ mode }: { mode: Mode }) {
  const router = useRouter();
  const params = useSearchParams();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(params.get("sso_error"));
  const [sso, setSso] = useState(false);

  const startSso = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    const email = new FormData(e.currentTarget).get("email");
    try {
      const r = await fetch("/api/auth/sso/start", { method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ email }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(j.detail ?? "Single sign-on isn't available."); setBusy(false); return; }
      window.location.href = j.redirect; // off to the company's identity provider
    } catch { setErr("Can't reach the server."); setBusy(false); }
  };

  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    const f = new FormData(e.currentTarget);
    const body = mode === "login"
      ? { email: f.get("email"), password: f.get("password") }
      : { name: f.get("name"), email: f.get("email"), password: f.get("password"), company: f.get("company"),
          demo_data: f.get("demo") === "on" };
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 45_000);
      const res = await fetch(`/api/auth/${mode}`, {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body), signal: ctl.signal,
      }).finally(() => clearTimeout(timer));
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        const d = j.detail;
        if (res.status === 403 && mode === "login") setSso(true);
        setErr(typeof d === "string" ? d : Array.isArray(d) ? "Check the highlighted fields: " + d.map((x: { loc: string[] }) => x.loc.at(-1)).join(", ") : "Something went wrong.");
        return;
      }
      const next = params.get("next");
      if (mode === "signup") {
        // roles picked on the landing page: add them straight away
        const hire = (params.get("hire") ?? "").split(",").filter((x) => /^[a-z_]+$/.test(x));
        for (const id of hire) await fetch(`/api/team/${id}/hire`, { method: "POST" }).catch(() => undefined);
      }
      router.push(mode === "signup" ? "/team?welcome=1" : next && next.startsWith("/") ? next : "/");
      router.refresh();
    } catch (e) {
      setErr(e instanceof DOMException && e.name === "AbortError"
        ? "The server is taking too long to answer. Wait a minute, then try signing in. Your account may already exist."
        : "Can't reach the server. Check your connection.");
    } finally {
      setBusy(false);
    }
  };

  if (mode === "login" && sso) {
    return (
      <form onSubmit={startSso} className="auth-form">
        <h1>Sign in with SSO</h1>
        <p className="muted">Enter your work email. You&apos;ll continue at your company&apos;s sign-in page.</p>
        <label>Work email<input name="email" type="email" required autoComplete="email" /></label>
        {err && <div className="banner error">{err}</div>}
        <button className="btn primary wide" disabled={busy}>{busy ? "Redirecting…" : "Continue"}</button>
        <p className="muted small"><button type="button" className="linklike" onClick={() => { setSso(false); setErr(null); }}>Use a password instead</button></p>
      </form>
    );
  }
  return (
    <form onSubmit={submit} className="auth-form">
      <h1>{mode === "login" ? "Welcome back" : "Create your workspace"}</h1>
      <p className="muted">{mode === "login" ? "Sign in to see what your team found today." : "Takes a minute. No card needed."}</p>
      {mode === "signup" && (
        <>
          <label>Your name<input name="name" required autoComplete="name" /></label>
          <label>Company<input name="company" required autoComplete="organization" /></label>
        </>
      )}
      <label>Work email<input name="email" type="email" required autoComplete="email" /></label>
      <label>Password
        <input name="password" type="password" required minLength={mode === "signup" ? 10 : 1}
          autoComplete={mode === "login" ? "current-password" : "new-password"} />
        {mode === "signup" && <small className="muted">At least 10 characters.</small>}
      </label>
      {mode === "signup" && (
        <label className="check">
          <input type="checkbox" name="demo" defaultChecked />
          <span>Start with a demo company so you can see the team at work before connecting your own tools</span>
        </label>
      )}
      {err && <div className="banner error">{err}</div>}
      <button className="btn primary wide" disabled={busy}>
        {busy ? (mode === "signup" ? "Setting up your workspace…" : "Signing in…") : mode === "login" ? "Sign in" : "Create workspace"}
      </button>
      {mode === "login" && (
        <button type="button" className="btn wide" onClick={() => { setSso(true); setErr(null); }}>Sign in with SSO</button>
      )}
      <p className="muted small">
        {mode === "login" ? <>New here? <Link href="/signup">Create a workspace</Link></> : <>Already have one? <Link href="/login">Sign in</Link></>}
      </p>
    </form>
  );
}
