"use client";

import { useState } from "react";
import { api, type ForecastResponse, type Plan } from "@/lib/api";
import { fmt } from "@/lib/format";
import { MethodBadge } from "./team";
import { useReceipt } from "./ReceiptDrawer";

const W = 900, H = 260, PAD = { l: 64, r: 14, t: 14, b: 28 };

function ForecastChart({ d, target }: { d: ForecastResponse; target?: { arr: number; months: number } }) {
  const hist = d.history.map((h) => h.mrr * 12);
  const fc = d.forecast;
  const n = hist.length + fc.length;
  const vals = [...hist, ...fc.map((f) => f.high * 12), target?.arr ?? 0];
  const hi = Math.max(...vals) * 1.05, lo = 0;
  const x = (i: number) => PAD.l + (i / (n - 1)) * (W - PAD.l - PAD.r);
  const y = (v: number) => PAD.t + (H - PAD.t - PAD.b) * (1 - (v - lo) / (hi - lo));
  const histPath = hist.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join("");
  const o = hist.length - 1;
  const fcPath = `M${x(o)},${y(hist[o])}` + fc.map((f, k) => `L${x(o + k + 1)},${y(f.mrr * 12)}`).join("");
  const band = `M${x(o)},${y(hist[o])}` + fc.map((f, k) => `L${x(o + k + 1)},${y(f.high * 12)}`).join("")
    + [...fc].reverse().map((f, k) => `L${x(o + fc.length - k)},${y(f.low * 12)}`).join("") + "Z";
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((t) => t * hi);
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="ARR history and forecast">
        <g className="axis">
          {ticks.map((t) => (
            <g key={t}><line className="gridline" x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} />
              <text x={PAD.l - 8} y={y(t) + 4} textAnchor="end">{fmt(t, "currency")}</text></g>
          ))}
          <text x={x(0)} y={H - 8}>{d.history[0].month.slice(0, 7)}</text>
          <text x={x(o)} y={H - 8} textAnchor="middle">today</text>
          <text x={x(n - 1)} y={H - 8} textAnchor="end">{fc[fc.length - 1].month.slice(0, 7)}</text>
        </g>
        <path d={band} fill="var(--accent)" opacity={0.15} />
        <path d={histPath} fill="none" stroke="var(--text)" strokeWidth={2} />
        <path d={fcPath} fill="none" stroke="var(--accent)" strokeWidth={2.5} strokeDasharray="6 4" />
        {target && target.months <= fc.length && (
          <g>
            <circle cx={x(o + target.months)} cy={y(target.arr)} r={5} fill="var(--bad)" />
            <text x={x(o + target.months) - 8} y={y(target.arr) - 8} textAnchor="end" fill="var(--bad)" fontSize={12}>goal</text>
          </g>
        )}
      </svg>
    </div>
  );
}

export function Planner({ d }: { d: ForecastResponse }) {
  const { open } = useReceipt();
  const [target, setTarget] = useState(d.default_goal.target_arr);
  const [months, setMonths] = useState(d.default_goal.months);
  const [plan, setPlan] = useState<Plan>(d.plan);
  const [rid, setRid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.plan(target, months);
      setPlan(r.plan);
      setRid(r.receipt_id);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  const s = plan.lever_spend, rt = plan.lever_retention;
  const f12 = d.forecast[d.forecast.length - 1];

  return (
    <>
      <div className="card chart-card">
        <div className="chart-head">
          <h3>ARR: history and 12-month forecast <MethodBadge kind="statistics" /></h3>
          <span className="muted num">
            {fmt(f12.mrr * 12, "currency")} in 12 months ({fmt(f12.low * 12, "currency")} to {fmt(f12.high * 12, "currency")})
          </span>
        </div>
        <ForecastChart d={d} target={{ arr: plan.target_arr, months: plan.months }} />
        <p className="note">
          Method: {d.method === "holt" ? "Holt trend" : "driver model"}, chosen because it had the lower error when predicting the last 6 known months
          (driver {fmt(d.backtest_mape.driver, "percent")}, Holt {fmt(d.backtest_mape.holt, "percent")}). Band = backtest error scaled by horizon.
          Drivers: revenue churn {fmt(d.drivers.revenue_churn, "percent")}/mo, net expansion {fmt(d.drivers.net_expansion, "percent")}/mo,
          new MRR {fmt(d.drivers.new_mrr_monthly, "currency")}/mo.
        </p>
      </div>

      <div className="section-title">Goal planner <MethodBadge kind="rule" /></div>
      <div className="card pad">
        <div className="form-row">
          <label>Target ARR (USD)
            <input type="number" value={target} step={100000} min={0} onChange={(e) => setTarget(Number(e.target.value))} />
          </label>
          <label>In months
            <input type="number" value={months} min={3} max={60} onChange={(e) => setMonths(Number(e.target.value))} />
          </label>
          <button className="btn primary" onClick={run} disabled={busy}>{busy ? "Solving…" : "Solve the plan"}</button>
          {rid && <button className="receipt-btn" onClick={() => open(rid)}>receipt</button>}
        </div>
        {err && <div className="banner error" style={{ marginTop: 12 }}>{err}</div>}
        <div className="verdict">{plan.verdict}</div>
        {plan.warnings.length > 0 && (
          <div className="banner"><ul>{plan.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
        )}
        <div className="kpis">
          <div className="card kpi"><span>Current path reaches</span><b className="num">{fmt(plan.baseline_arr, "currency")}</b></div>
          <div className="card kpi"><span>Gap to goal</span><b className="num">{fmt(Math.max(0, plan.gap), "currency")}</b></div>
          <div className="card kpi"><span>New customers / month</span><b className="num">{s.new_customers_now.toFixed(0)} → {s.new_customers_needed.toFixed(0)}</b></div>
          <div className="card kpi"><span>S&M / month</span><b className="num">{fmt(s.sm_monthly_now, "currency")} → {fmt(s.sm_monthly_needed, "currency")}</b></div>
          <div className="card kpi"><span>Cash at the end</span><b className="num">{fmt(s.end_cash, "currency")}</b></div>
          <div className="card kpi"><span>Runway left at the end</span><b className="num">{s.runway_after_months === null ? "Not burning" : fmt(s.runway_after_months, "months")}</b></div>
        </div>
        <div className="two">
          <div>
            <h3>Lever 1: buy the growth</h3>
            <p className="note">
              S&M scales along your fitted spend curve (elasticity {s.spend_elasticity.toFixed(2)}): doubling customers costs more than double the spend.
              {s.cash_out_month ? ` Cash runs out in month ${s.cash_out_month}.` : " Cash stays positive across the plan."}
            </p>
          </div>
          <div>
            <h3>Lever 2: keep the growth</h3>
            <p className="note">
              {rt.achievable && rt.revenue_churn_needed !== null
                ? `With today's acquisition, monthly revenue churn would need to fall from ${fmt(rt.revenue_churn_now, "percent")} to ${fmt(rt.revenue_churn_needed, "percent")}.`
                : `Retention alone cannot close this gap: even zero churn falls short with today's acquisition (churn now ${fmt(rt.revenue_churn_now, "percent")}/mo).`}
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
