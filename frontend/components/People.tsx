"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { Members } from "@/lib/api";
import { send } from "@/lib/client";
import { localTime } from "@/lib/format";

const ROLE_HELP: Record<string, string> = {
  owner: "Everything, including billing-level actions: SSO, export, delete",
  admin: "Connect data, invite people, change settings",
  member: "See everything, own tasks, create documents",
};

export function People({ initial }: { initial: Members }) {
  const router = useRouter();
  const m = initial;
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string; link?: string } | null>(null);
  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setMsg(null);
    try { await fn(); if (ok) setMsg({ ok: true, text: ok }); router.refresh(); }
    catch (e) { setMsg({ ok: false, text: e instanceof Error ? e.message : String(e) }); }
    finally { setBusy(false); }
  };

  return (
    <div className="stack">
      {msg && (
        <div className={`banner ${msg.ok ? "ok" : "error"}`}>
          {msg.text}
          {msg.link && (
            <div className="invite-link">
              <input readOnly value={msg.link} onFocus={(e) => e.currentTarget.select()} />
              <button className="btn small" onClick={() => navigator.clipboard?.writeText(msg.link!)}>Copy</button>
            </div>
          )}
        </div>
      )}

      {m.can_manage && (
        <section className="card src-card">
          <b>Invite someone</b>
          <form className="inline-form" onSubmit={(e) => {
            e.preventDefault();
            setBusy(true); setMsg(null);
            send<{ link: string; emailed: boolean }>("/members/invite", "POST", { email, role })
              .then((r) => {
                setMsg({ ok: true, text: r.emailed ? `Invite emailed to ${email}. You can also share this link:` : `Invite created. Email isn't set up on this server, so send ${email} this link yourself (shown once):`, link: r.link });
                setEmail(""); router.refresh();
              })
              .catch((err) => setMsg({ ok: false, text: err.message }))
              .finally(() => setBusy(false));
          }}>
            <label className="grow">Work email<input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></label>
            <label>Role
              <select value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="member">Member</option>
                {m.is_owner && <option value="admin">Admin</option>}
              </select>
            </label>
            <button className="btn primary small" disabled={busy}>Send invite</button>
          </form>
          <p className="muted small">{ROLE_HELP[role]}. Links work once and expire in 7 days.</p>
        </section>
      )}

      <section className="card">
        <table className="table">
          <thead><tr><th>Person</th><th>Role</th><th>Last sign-in</th><th /></tr></thead>
          <tbody>
            {m.members.map((p) => (
              <tr key={p.id}>
                <td><b>{p.name}</b>{p.you && <span className="muted"> (you)</span>}<br /><span className="muted small">{p.email}{p.sso_only ? " · SSO" : ""}</span></td>
                <td>
                  {m.can_manage && !p.you && (m.is_owner || p.role === "member") ? (
                    <select value={p.role} disabled={busy} title={ROLE_HELP[p.role]}
                      onChange={(e) => act(() => send(`/members/${p.id}/role`, "PUT", { role: e.target.value }), `${p.name} is now ${e.target.value}.`)}>
                      {m.is_owner && <option value="owner">Owner</option>}
                      {m.is_owner && <option value="admin">Admin</option>}
                      <option value="member">Member</option>
                    </select>
                  ) : <span className="pill neutral-pill" title={ROLE_HELP[p.role]}>{p.role}</span>}
                </td>
                <td className="small" suppressHydrationWarning>{p.last_login_at ? localTime(p.last_login_at) : <span className="muted">Never</span>}</td>
                <td className="r">
                  {(p.you || (m.can_manage && (m.is_owner || p.role === "member"))) && (
                    <button className="linklike" disabled={busy}
                      onClick={() => act(async () => {
                        await send(`/members/${p.id}`, "DELETE");
                        if (p.you) { router.push("/login?session=expired"); }
                      }, p.you ? undefined : `${p.name} was removed and signed out of this workspace.`)}>
                      {p.you ? "Leave" : "Remove"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {m.invites.length > 0 && (
        <section className="card">
          <h3>Pending invites</h3>
          <table className="table compact">
            <tbody>{m.invites.map((i) => (
              <tr key={i.id}>
                <td>{i.email}</td><td>{i.role}</td>
                <td className="small muted" suppressHydrationWarning>expires {localTime(i.expires_at, false)}{i.invited_by ? ` · from ${i.invited_by}` : ""}</td>
                <td className="r">{m.can_manage && <button className="linklike" disabled={busy} onClick={() => act(() => send(`/members/invites/${i.id}`, "DELETE"), "Invite cancelled.")}>Cancel</button>}</td>
              </tr>))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}
