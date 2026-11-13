"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import type { BudgetHire, BudgetOverview, BudgetRoundView, BudgetSubmission } from "@/lib/api";
import { send } from "@/lib/client";
import { fmt, localTime } from "@/lib/format";

const STATUS: Record<string, [string, string]> = {
  draft: ["Not submitted", "neutral-pill"], submitted: ["Waiting for review", "watch"],
  approved: ["Approved", "good"], returned: ["Changes requested", "bad"],
};
const ACTION: Record<string, string> = { opened: "opened the round", submitted: "submitted", approved: "approved",
  returned: "asked for changes to", published: "published the plan", cancelled: "cancelled the round", revenue_changed: "changed the revenue plan" };
const money = (v: number | null | undefined) => fmt(v ?? null, "currency");
const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0);

function useAct() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr(null);
    try { await fn(); router.refresh(); return true; }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); return false; }
    finally { setBusy(false); }
  };
  return { busy, err, run };
}

function OpenRound({ data }: { data: BudgetOverview }) {
  const { busy, err, run } = useAct();
  const [fy, setFy] = useState(data.next_fiscal_year);
  const [due, setDue] = useState("");
  const [owners, setOwners] = useState<Record<string, string>>({});
  if (!data.has_books) {
    return <div className="empty"><div className="empty-icon">◌</div><h3>Connect your books first</h3>
      <p>The budget starts from your actual spend and team. Connect QuickBooks or Xero, or upload a P&amp;L, under Data sources.</p>
      <a className="btn primary" href="/settings/data">Go to data sources</a></div>;
  }
  if (!data.can_admin) return <div className="empty"><div className="empty-icon">◌</div><h3>No budget round is open</h3><p>An admin starts one when it&apos;s planning season.</p></div>;
  return (
    <section className="card pad">
      <h3>Start the FY{fy} budget</h3>
      <p className="muted small">Each department gets a draft prefilled from current spend and its current team. Owners adjust it, add hires and submit; you review and publish.</p>
      <form className="budget-open" onSubmit={(e) => { e.preventDefault(); run(() => send("/budget/rounds", "POST", { fiscal_year: fy, due_date: due || null, owners })); }}>
        <div className="inline-form">
          <label>Fiscal year<input type="number" value={fy} onChange={(e) => setFy(Number(e.target.value))} /></label>
          <label>Due<input type="date" value={due} onChange={(e) => setDue(e.target.value)} /></label>
        </div>
        <div className="owner-grid">
          {data.departments.map((d) => (
            <label key={d}>{d}
              <select value={owners[d] ?? ""} onChange={(e) => setOwners({ ...owners, [d]: e.target.value })}>
                <option value="">You (admins)</option>
                {data.members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              </select>
            </label>
          ))}
        </div>
        {err && <p className="err small">{err}</p>}
        <button className="btn primary" disabled={busy}>{busy ? "Preparing drafts…" : "Open the budget round"}</button>
      </form>
    </section>
  );
}

function Department({ s, r, accounts, admin, members }: { s: BudgetSubmission; r: BudgetRoundView; accounts: BudgetRoundView["accounts"]; admin: boolean; members: BudgetOverview["members"] }) {
  const { busy, err, run } = useAct();
  const [lines, setLines] = useState(s.lines.map((l) => ({ ...l, annual: Math.round(sum(l.monthly)) })));
  const [hires, setHires] = useState<BudgetHire[]>(s.hires);
  const [note, setNote] = useState(s.note ?? "");
  const [reply, setReply] = useState("");
  const [add, setAdd] = useState("");
  const fy = r.round.fiscal_year;
  const ed = s.can_edit;
  const payload = () => ({ lines: lines.map((l) => ({ account: l.account, annual: l.annual })), hires, note });
  const save = () => send(`/budget/rounds/${r.round.id}/departments/${encodeURIComponent(s.department)}`, "PUT", payload());
  const d = r.plan.departments[s.department];
  const unused = accounts.filter((a) => !lines.some((l) => l.account === a.code));

  return (
    <div className="dept-editor">
      {s.status === "returned" && <div className="banner error">Changes requested. See the notes below.</div>}
      <div className="dept-cols">
        <div>
          <h4>Spend</h4>
          {lines.length === 0 && <p className="muted small">No spend lines. Add one if this team buys anything.</p>}
          <table className="table compact">
            <thead><tr><th>Account</th><th className="r">Last 12 months</th><th className="r">FY{fy} plan</th><th /></tr></thead>
            <tbody>{lines.map((l, i) => (
              <tr key={l.account}>
                <td>{l.name ?? accounts.find((a) => a.code === l.account)?.name ?? l.account}</td>
                <td className="r muted">{money(l.last12 ?? 0)}</td>
                <td className="r">{ed ? <input className="num" type="number" min={0} step={1000} value={l.annual}
                  onChange={(e) => setLines(lines.map((x, j) => j === i ? { ...x, annual: Number(e.target.value) } : x))} /> : money(l.annual)}</td>
                <td className="r">{ed && <button className="linklike" onClick={() => setLines(lines.filter((_, j) => j !== i))}>Remove</button>}</td>
              </tr>))}
            </tbody>
          </table>
          {ed && unused.length > 0 && (
            <div className="row">
              <select value={add} onChange={(e) => setAdd(e.target.value)}>
                <option value="">Add an account…</option>
                {unused.map((a) => <option key={a.code} value={a.code}>{a.name}</option>)}
              </select>
              <button className="btn small" disabled={!add} onClick={() => { setLines([...lines, { account: add, monthly: [], annual: 0 }]); setAdd(""); }}>Add</button>
            </div>
          )}
        </div>
        <div>
          <h4>People</h4>
          <p className="muted small">{s.staff.length} on the team today. Leavers drop off on their end date.{d ? ` Payroll next year, including saved hires: ${money(d.payroll)}.` : ""}</p>
          <ul className="staff">{s.staff.map((p) => <li key={p.name + p.title}>{p.name} · {p.title}{p.end_date ? <span className="muted"> · leaves {p.end_date.slice(0, 7)}</span> : ""}</li>)}</ul>
          <h4>New hires</h4>
          {hires.map((h, i) => (
            <div key={i} className="hire-row">
              <input placeholder="Role" value={h.role} disabled={!ed} onChange={(e) => setHires(hires.map((x, j) => j === i ? { ...x, role: e.target.value } : x))} />
              <input type="month" value={h.planned_start.slice(0, 7)} min={`${fy}-01`} max={`${fy}-12`} disabled={!ed}
                onChange={(e) => setHires(hires.map((x, j) => j === i ? { ...x, planned_start: `${e.target.value}-01` } : x))} />
              <input className="num" type="number" placeholder="Salary" value={h.salary_annual || ""} disabled={!ed} step={1000}
                onChange={(e) => setHires(hires.map((x, j) => j === i ? { ...x, salary_annual: Number(e.target.value) } : x))} />
              {ed && <button className="linklike" onClick={() => setHires(hires.filter((_, j) => j !== i))}>Remove</button>}
            </div>
          ))}
          {ed && <button className="btn small" onClick={() => setHires([...hires, { role: "", planned_start: `${fy}-04-01`, salary_annual: 0 }])}>Add a hire</button>}
        </div>
      </div>
      <label className="note">Notes for the reviewer<textarea value={note} disabled={!ed} onChange={(e) => setNote(e.target.value)} rows={2} /></label>
      {admin && r.round.status === "open" && (
        <label className="note">Owner
          <select value={s.owner_user_id ?? ""} disabled={busy}
            onChange={(e) => run(() => send(`/budget/rounds/${r.round.id}/departments/${encodeURIComponent(s.department)}`, "PUT", { ...payload(), owner_user_id: e.target.value }))}>
            <option value="">Admins</option>
            {members.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </label>
      )}
      {err && <p className="err small">{err}</p>}
      <div className="row">
        {ed && <button className="btn small" disabled={busy} onClick={() => run(save)}>Save draft</button>}
        {ed && <button className="btn primary small" disabled={busy} onClick={() => run(async () => { await save(); await send(`/budget/rounds/${r.round.id}/departments/${encodeURIComponent(s.department)}/submit`, "POST"); })}>Submit for review</button>}
        {admin && r.round.status === "open" && s.status === "submitted" && (
          <>
            <button className="btn primary small" disabled={busy} onClick={() => run(() => send(`/budget/rounds/${r.round.id}/departments/${encodeURIComponent(s.department)}/approve`, "POST", { note: reply || null }))}>Approve</button>
            <input className="grow" placeholder="What should change?" value={reply} onChange={(e) => setReply(e.target.value)} />
            <button className="btn small" disabled={busy || !reply.trim()} onClick={() => run(() => send(`/budget/rounds/${r.round.id}/departments/${encodeURIComponent(s.department)}/return`, "POST", { note: reply }))}>Send back</button>
          </>
        )}
      </div>
    </div>
  );
}

function Round({ data, r }: { data: BudgetOverview; r: BudgetRoundView }) {
  const { busy, err, run } = useAct();
  const [open, setOpen] = useState<string | null>(r.submissions.find((s) => s.mine && s.can_edit)?.department ?? null);
  const [rev, setRev] = useState(r.round.revenue);
  const s = r.plan.summary;
  const base = r.round.baseline.groups;
  const opex = s.sm + s.rnd + s.ga;
  const baseOpex = (base.sm ?? 0) + (base.rnd ?? 0) + (base.ga ?? 0);
  const done = r.submissions.filter((x) => x.status === "approved").length;
  const live = r.round.status === "open";
  return (
    <div className="stack">
      <div className="src-head">
        <div><h2 className="h2">{r.round.name}</h2>
          <p className="muted small">{live ? `${done} of ${r.submissions.length} departments approved${r.round.due_date ? ` · due ${r.round.due_date}` : ""}` :
            r.round.status === "published" ? <span suppressHydrationWarning>Published {localTime(r.round.published_at!, false)}. Budget vs actual measures against it from January.</span> : "Cancelled"}</p></div>
        {data.can_admin && live && (
          <div className="row">
            <button className="btn primary" disabled={busy || !r.ready_to_publish} title={r.ready_to_publish ? "" : "Approve every department first"}
              onClick={() => run(() => send(`/budget/rounds/${r.round.id}/publish`, "POST"))}>Publish as the FY{r.round.fiscal_year} plan</button>
            <button className="linklike" disabled={busy} onClick={() => run(() => send(`/budget/rounds/${r.round.id}/cancel`, "POST"))}>Cancel round</button>
          </div>
        )}
      </div>
      {err && <div className="banner error">{err}</div>}

      <div className="kpi-row">
        {[["Revenue", s.revenue, base.revenue], ["Operating costs", opex, baseOpex], ["Operating income", s.operating_income, null],
          ["Cash at year end", s.cash_end, null], ["Headcount at year end", s.headcount_end, null]].map(([label, v, b]) => (
          <div key={label as string} className="card kpi">
            <span className="muted small">{label}</span>
            <b>{label === "Headcount at year end" ? v : money(v as number)}</b>
            {typeof b === "number" && b > 0 && <span className="small muted">{`${((Number(v) / b - 1) * 100).toFixed(0)}% vs last 12 months`}</span>}
          </div>
        ))}
      </div>

      {r.checks.length > 0 && (
        <section className="card pad">
          <h3>Fred&apos;s review</h3>
          <ul className="calls">{r.checks.map((c, i) => (
            <li key={i} className={c.level === "bad" ? "wrong" : c.level === "watch" ? "wrong" : "right"}>
              {c.department && <b>{c.department}: </b>}{c.text}
            </li>))}
          </ul>
        </section>
      )}

      <section className="card pad">
        <h3>Revenue plan</h3>
        <p className="muted small">Starts from today&apos;s {money(r.round.revenue.start_mrr)} MRR. Prefilled with your last six months; change it to the plan you&apos;re committing to.</p>
        <form className="inline-form" onSubmit={(e) => { e.preventDefault(); run(() => send(`/budget/rounds/${r.round.id}/revenue`, "PUT", { new_customers: rev.new_customers, arpa_new: rev.arpa_new, revenue_churn: rev.revenue_churn, net_expansion: rev.net_expansion })); }}>
          <label>New customers / month<input type="number" step="0.5" value={rev.new_customers} disabled={!data.can_admin || !live} onChange={(e) => setRev({ ...rev, new_customers: Number(e.target.value) })} /></label>
          <label>Their monthly price<input type="number" step="10" value={rev.arpa_new} disabled={!data.can_admin || !live} onChange={(e) => setRev({ ...rev, arpa_new: Number(e.target.value) })} /></label>
          <label>Revenue churn / month (%)<input type="number" step="0.1" value={+(rev.revenue_churn * 100).toFixed(2)} disabled={!data.can_admin || !live} onChange={(e) => setRev({ ...rev, revenue_churn: Number(e.target.value) / 100 })} /></label>
          <label>Expansion / month (%)<input type="number" step="0.1" value={+(rev.net_expansion * 100).toFixed(2)} disabled={!data.can_admin || !live} onChange={(e) => setRev({ ...rev, net_expansion: Number(e.target.value) / 100 })} /></label>
          {data.can_admin && live && <button className="btn small" disabled={busy}>Update</button>}
        </form>
      </section>

      <section className="card">
        <table className="table">
          <thead><tr><th>Department</th><th>Owner</th><th>Status</th><th className="r">FY{r.round.fiscal_year} total</th><th className="r">Hires</th><th /></tr></thead>
          <tbody>{r.submissions.map((sub) => {
            const d = r.plan.departments[sub.department];
            const [label, cls] = STATUS[sub.status];
            return [
              <tr key={sub.department} className={open === sub.department ? "is-open" : ""}>
                <td><b>{sub.department}</b>{sub.mine && <span className="muted"> (yours)</span>}</td>
                <td className="small">{sub.owner_name ?? <span className="muted">Admins</span>}</td>
                <td><span className={`pill ${cls}`}>{label}</span></td>
                <td className="r">{money(d?.total)}</td>
                <td className="r">{d?.hires ?? 0}</td>
                <td className="r"><button className="linklike" onClick={() => setOpen(open === sub.department ? null : sub.department)}>{open === sub.department ? "Close" : sub.can_edit ? "Edit" : "View"}</button></td>
              </tr>,
              open === sub.department && <tr key={`${sub.department}-x`}><td colSpan={6}><Department s={sub} r={r} accounts={r.accounts} admin={data.can_admin} members={data.members} /></td></tr>,
            ];
          })}</tbody>
        </table>
      </section>

      {r.events.length > 0 && (
        <details className="card pad">
          <summary><b>History</b></summary>
          <ul className="plain-list small">{r.events.map((e, i) => (
            <li key={i} suppressHydrationWarning>{localTime(e.created_at)} · {e.actor} {ACTION[e.action] ?? e.action}{e.department ? ` ${e.department}` : ""}{e.note ? `: “${e.note}”` : ""}</li>))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function BudgetView({ data }: { data: BudgetOverview }) {
  const r = data.current;
  if (!r || r.round.status !== "open") {
    return (
      <div className="stack">
        {r && <Round data={data} r={r} />}
        <OpenRound data={data} />
      </div>
    );
  }
  return <Round data={data} r={r} />;
}
