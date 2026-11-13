"use client";

import Link from "next/link";
import { useState } from "react";
import type { Bva, PnlRow } from "@/lib/api";
import { fmt, monthLabel } from "@/lib/format";
import { MethodBadge } from "./team";
import { useReceipt } from "./ReceiptDrawer";

const K = (v: number) => fmt(v, "currency");
const signed = (v: number) => `${v >= 0 ? "+" : "−"}${fmt(Math.abs(v), "currency")}`;

function Var({ v, favorable, material, rid }: { v: number; favorable: boolean; material: boolean; rid?: string }) {
  const { open } = useReceipt();
  const cls = Math.abs(v) < 1 ? "flat" : favorable ? "up" : "down";
  const body = <span className={`delta ${cls} num`} style={{ fontWeight: material ? 700 : 400 }}>{signed(v)}</span>;
  return rid ? <button className="value-link" onClick={() => open(rid)} title="Receipt">{body}</button> : body;
}

function Waterfall({ b }: { b: Bva["revenue_bridge"] }) {
  const parts: [string, number][] = [
    ["New-logo volume", b.ytd.volume], ["New-logo price", b.ytd.price], ["Churn", b.ytd.churn], ["Expansion", b.ytd.expansion],
  ];
  const max = Math.max(...parts.map(([, v]) => Math.abs(v)), Math.abs(b.ytd_total), 1);
  return (
    <div>
      {[...parts, ["Total vs plan", b.ytd_total] as [string, number]].map(([label, v]) => (
        <div key={label} className="bench-row">
          <span>
            <b>{label}</b>
            <span className={`bar-h ${v < 0 ? "neg" : ""}`} style={{ width: `${(Math.abs(v) / max) * 100}%`, marginTop: 6 }} />
          </span>
          <span className={`num delta ${v < 0 ? "down" : "up"}`}>{signed(v)}</span>
        </div>
      ))}
      <p className="note">
        {b.new_customers.actual.toFixed(0)} new customers vs {b.new_customers.plan.toFixed(0)} planned · revenue churn{" "}
        {fmt(b.revenue_churn.actual, "percent")}/mo vs {fmt(b.revenue_churn.plan, "percent")} · net expansion{" "}
        {fmt(b.net_expansion.actual, "percent")}/mo vs {fmt(b.net_expansion.plan, "percent")}. Components add up exactly to the revenue gap.
      </p>
    </div>
  );
}

const KIND: Record<string, string> = {
  late_start: "Late starts", unfilled: "Open roles", cancelled: "Cancelled", unplanned: "Unplanned hires",
  departure: "Departures", pay_rate: "Pay vs plan",
};
const STATUS: Record<string, [string, string]> = {
  on_time: ["On time", "good"], cancelled: ["Cancelled", "neutral-pill"], overdue: ["Overdue", "bad"], upcoming: ["Upcoming", "neutral-pill"],
};

export function FinanceView({ d }: { d: Bva }) {
  const { open } = useReceipt();
  const [showAccounts, setShowAccounts] = useState(true);
  const row = (k: string) => d.pnl.find((r) => r.key === k) as PnlRow;
  const rev = row("revenue"), opex = row("opex"), oi = row("operating_income");
  const hcPlan = Object.values(d.payroll.departments).reduce((a, x) => a + x.headcount_plan, 0);
  const rows = d.pnl.filter((r) => showAccounts || r.level !== "account");

  return (
    <>
      <div className="tabs">
        {d.closed_months.map((m) => (
          <Link key={m} href={`/finance?month=${m.slice(0, 7)}`} className={`preset ${m === d.month ? "on" : ""}`}>
            {monthLabel(m)}
          </Link>
        ))}
      </div>

      <div className="kpis">
        <div className="card kpi"><span>Revenue YTD vs plan</span><b className="num">{K(rev.actual_ytd)}</b>
          <span className={`delta ${rev.favorable_ytd ? "up" : "down"}`}>{signed(rev.var_ytd)} ({fmt(rev.var_pct_ytd, "percent")})</span></div>
        <div className="card kpi"><span>Operating expenses YTD</span><b className="num">{K(opex.actual_ytd)}</b>
          <span className={`delta ${opex.favorable_ytd ? "up" : "down"}`}>{signed(opex.var_ytd)} vs plan</span></div>
        <div className="card kpi"><span>Operating income (burn) YTD</span><b className="num">{K(oi.actual_ytd)}</b>
          <span className={`delta ${oi.favorable_ytd ? "up" : "down"}`}>{signed(oi.var_ytd)} vs plan</span></div>
        <div className="card kpi"><span>Cash, end of {monthLabel(d.month)}</span><b className="num">{K(d.cash.actual)}</b>
          <span className={`delta ${d.cash.actual >= d.cash.plan ? "up" : "down"}`}>{signed(d.cash.actual - d.cash.plan)} vs plan</span></div>
        <div className="card kpi"><span>Headcount</span><b className="num">{d.headcount.active} <small className="muted">/ {hcPlan} planned</small></b>
          <span className="muted">{d.headcount.departures_ytd} departure{d.headcount.departures_ytd === 1 ? "" : "s"} YTD</span></div>
      </div>

      <div className="section-title">What moved and why, ranked by size <MethodBadge kind="rule" /></div>
      <div className="findings">
        {d.findings.map((f) => (
          <article key={f.key} className={`card finding sev-border-${f.favorable && !f.early_warning ? "info" : Math.abs(f.variance) >= 100000 ? "high" : "medium"}`}>
            <header className="finding-head">
              <div className="finding-meta">
                <span className="muted">{f.area}</span>
                {f.early_warning && <span className="pill watch">Early warning</span>}
              </div>
              <button className="value-link" onClick={() => open(f.receipt_id)} title="Receipt">
                <span className={`num delta ${f.favorable ? "up" : "down"}`} style={{ fontSize: 18, fontWeight: 650 }}>{signed(f.variance)}</span>
              </button>
            </header>
            <h3>{f.title}</h3>
            <p className="finding-summary" style={{ color: "var(--text)" }}>{f.explanation}</p>
            <footer className="finding-foot"><span className="muted method-detail">{f.method}</span>
              <button className="receipt-btn" onClick={() => open(f.receipt_id)}>receipt</button></footer>
          </article>
        ))}
      </div>

      <div className="section-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>P&L vs plan</span>
        <button className="btn small" onClick={() => setShowAccounts((s) => !s)}>{showAccounts ? "Hide accounts" : "Show accounts"}</button>
      </div>
      <div className="card table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th></th>
              <th className="r">{monthLabel(d.month)} plan</th><th className="r">Actual</th><th className="r">Var</th>
              <th className="r">YTD plan</th><th className="r">YTD actual</th><th className="r">YTD var</th><th className="r">%</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} style={r.level === "account" ? undefined : { fontWeight: 650, background: r.level === "total" ? "var(--surface-2)" : undefined }}>
                <td style={{ paddingLeft: r.level === "account" ? 28 : 12 }}>{r.label}</td>
                <td className="r num">{K(r.plan_m)}</td>
                <td className="r num">{K(r.actual_m)}</td>
                <td className="r"><Var v={r.var_m} favorable={r.cost ? r.var_m <= 0 : r.var_m >= 0} material={r.material_m} /></td>
                <td className="r num">{K(r.plan_ytd)}</td>
                <td className="r num">{K(r.actual_ytd)}</td>
                <td className="r"><Var v={r.var_ytd} favorable={r.favorable_ytd} material={r.material_ytd} rid={r.receipt_id} /></td>
                <td className="r num muted">{r.var_pct_ytd === null ? "" : `${(r.var_pct_ytd * 100).toFixed(1)}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">Bold variances are material: revenue ≥2% or $25K, payroll ≥3% or $15K, other lines ≥5% or $5K (year to date). Cost of revenue is judged against a flexed budget.</p>

      <div className="two" style={{ marginTop: 16 }}>
        <div className="card pad">
          <h3>Revenue bridge, year to date <MethodBadge kind="rule" /></h3>
          <Waterfall b={d.revenue_bridge} />
        </div>
        <div className="card pad">
          <h3>Payroll by department, year to date <MethodBadge kind="rule" /></h3>
          {Object.entries(d.payroll.departments).map(([dept, x]) => (
            <div key={dept} className="bench-row">
              <span>
                <b>{dept}</b> <span className="note">{x.headcount_actual} people (plan {x.headcount_plan})</span>
                <span className="sub">
                  {Object.entries(x.components).filter(([, v]) => Math.abs(v) >= 500).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
                    .map(([k, v]) => `${KIND[k] ?? k} ${signed(v)}`).join(" · ")}
                </span>
              </span>
              <span className={`num delta ${x.variance <= 0 ? "up" : "down"}`}>{signed(x.variance)}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="section-title">Hiring plan vs reality</div>
      <div className="card table-wrap">
        <table className="table">
          <thead><tr><th>Role</th><th>Department</th><th>Planned start</th><th>Actual start</th><th className="r">Planned salary</th><th>Status</th></tr></thead>
          <tbody>
            {d.headcount.plan_hires.map((h, i) => {
              const late = h.status.startsWith("late_") ? Number(h.status.split("_")[1]) : 0;
              const [label, cls] = late ? [`${late} month${late > 1 ? "s" : ""} late`, "watch"] : STATUS[h.status] ?? [h.status, "neutral-pill"];
              return (
                <tr key={i}>
                  <td><b>{h.role}</b><span className="sub">{h.location}</span></td>
                  <td>{h.department}</td>
                  <td>{monthLabel(h.planned_start)}</td>
                  <td>{h.actual_start ? monthLabel(h.actual_start) : <span className="muted">—</span>}</td>
                  <td className="r num">{K(h.salary_plan)}</td>
                  <td><span className={`pill ${cls}`}>{label}</span></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="note">
        Plan: {d.plan.name}, approved {d.plan.approved_on}. Every variance links to a receipt with its plan, actuals and components. {d.llm_calls} LLM calls.
      </p>
    </>
  );
}
