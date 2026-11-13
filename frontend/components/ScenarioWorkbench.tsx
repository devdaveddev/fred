"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Receipt, type ScenarioRequest, type ScenarioResult, type Shock, type Unit } from "@/lib/api";
import { fmt, fmtDelta } from "@/lib/format";
import { FanChart } from "./FanChart";
import { useReceipt } from "./ReceiptDrawer";

const NEUTRAL: Shock = { price_change: 0, churn_multiplier: 1, sm_spend_change: 0, cac_change: 0 };

const PRESETS: { label: string; shock: Partial<Shock> }[] = [
  { label: "Raise price 20%", shock: { price_change: 0.2, churn_multiplier: 1.15 } },
  { label: "Cut S&M 30%", shock: { sm_spend_change: -0.3 } },
  { label: "Double S&M", shock: { sm_spend_change: 1.0, cac_change: 0.25 } },
  { label: "Move upmarket", shock: { price_change: 0.5, cac_change: 0.6, churn_multiplier: 0.8 } },
  { label: "Churn spike", shock: { churn_multiplier: 1.6 } },
];

const pct = (v: number) => `${v > 0 ? "+" : ""}${Math.round(v * 100)}%`;

type Row = {
  label: string;
  unit: Unit;
  better: "up" | "down";
  get: (r: ScenarioResult["baseline"]) => number | null;
  receipt?: string;
  range?: (r: ScenarioResult["baseline"]) => [number, number];
};

const ROWS: Row[] = [
  { label: "ARR at horizon (median)", unit: "currency", better: "up", get: (r) => r.arr_end.p50, receipt: "sim_arr_end_p50",
    range: (r) => [r.arr_end.p10, r.arr_end.p90] },
  { label: "12-month NRR (median)", unit: "percent", better: "up", get: (r) => r.nrr_12m.p50, receipt: "sim_nrr_12m_p50",
    range: (r) => [r.nrr_12m.p10, r.nrr_12m.p90] },
  { label: "CAC", unit: "currency", better: "down", get: (r) => r.cac, receipt: "sim_cac" },
  { label: "CAC payback", unit: "months", better: "down", get: (r) => r.cac_payback_months, receipt: "sim_cac_payback_months" },
  { label: "LTV:CAC (WACC-adjusted)", unit: "multiple", better: "up", get: (r) => r.ltv_cac_wacc, receipt: "sim_ltv_cac" },
  { label: "Chance of running out of cash", unit: "percent", better: "down", get: (r) => r.p_cash_out, receipt: "sim_p_cash_out" },
];

function Slider(props: {
  label: string; value: number; min: number; max: number; step: number; display: string; hint?: string;
  onChange: (v: number) => void;
}) {
  return (
    <div className="field">
      <label>
        <span>{props.label}</span>
        <b>{props.display}</b>
      </label>
      <input type="range" min={props.min} max={props.max} step={props.step} value={props.value}
        onChange={(e) => props.onChange(Number(e.target.value))} aria-label={props.label} />
      {props.hint && <div className="hint">{props.hint}</div>}
    </div>
  );
}

function Value({ v, unit, receipt, currency }: { v: number | null; unit: Unit; receipt?: Receipt; currency: string }) {
  const { open } = useReceipt();
  if (!receipt) return <span className="num">{fmt(v, unit, currency)}</span>;
  return (
    <button className="value-link num" onClick={() => open(receipt)} title="Show receipt">
      {fmt(v, unit, currency)}
    </button>
  );
}

export function ScenarioWorkbench({ currency }: { currency: string }) {
  const [shock, setShock] = useState<Shock>({ ...NEUTRAL, price_change: 0.2, churn_multiplier: 1.15 });
  const [elasticity, setElasticity] = useState(-0.6);
  const [months, setMonths] = useState(24);
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const ctrl = useRef<AbortController | null>(null);

  const request: ScenarioRequest = useMemo(
    () => ({ shock, price_elasticity: elasticity, months, runs: 5000, seed: 42 }),
    [shock, elasticity, months],
  );

  // Debounced auto-run: the user drags a slider and sees the answer, no "submit" ceremony.
  useEffect(() => {
    const t = setTimeout(() => {
      ctrl.current?.abort();
      const c = new AbortController();
      ctrl.current = c;
      setBusy(true);
      api.runScenario(request, c.signal)
        .then((r) => { setResult(r); setError(null); })
        .catch((e) => { if (e.name !== "AbortError") setError(e.message ?? String(e)); })
        .finally(() => { if (ctrl.current === c) setBusy(false); });
    }, 350);
    return () => clearTimeout(t);
  }, [request]);

  const set = (k: keyof Shock) => (v: number) => setShock((s) => ({ ...s, [k]: v }));
  const activePreset = PRESETS.find((p) => JSON.stringify({ ...NEUTRAL, ...p.shock }) === JSON.stringify(shock))?.label;

  const b = result?.baseline, s = result?.scenario;
  const arrDelta = b && s ? s.arr_end.p50 / b.arr_end.p50 - 1 : 0;

  return (
    <div className="workbench">
      <aside className="card controls">
        <div>
          <div className="group-label" style={{ marginBottom: 8 }}>Start from</div>
          <div className="presets">
            {PRESETS.map((p) => (
              <button key={p.label} className={`preset ${activePreset === p.label ? "on" : ""}`}
                onClick={() => setShock({ ...NEUTRAL, ...p.shock })}>{p.label}</button>
            ))}
          </div>
        </div>

        <div className="group-label">The change</div>
        <Slider label="Price" value={shock.price_change} min={-0.5} max={1} step={0.05} display={pct(shock.price_change)}
          onChange={set("price_change")} />
        <Slider label="Churn" value={shock.churn_multiplier} min={0.5} max={2.5} step={0.05}
          display={`×${shock.churn_multiplier.toFixed(2)}`} hint="How customers react. ×1.15 means 15% more churn than today."
          onChange={set("churn_multiplier")} />
        <Slider label="Sales & marketing spend" value={shock.sm_spend_change} min={-0.8} max={2} step={0.05}
          display={pct(shock.sm_spend_change)} onChange={set("sm_spend_change")} />
        <Slider label="CAC (persona / channel shift)" value={shock.cac_change} min={-0.5} max={2} step={0.05}
          display={pct(shock.cac_change)} onChange={set("cac_change")} />

        <div className="group-label">Assumptions</div>
        <Slider label="Price elasticity" value={elasticity} min={-2} max={0} step={0.05} display={elasticity.toFixed(2)}
          hint="% change in new customers per 1% price change. Default -0.6 until fitted to your history."
          onChange={setElasticity} />
        <Slider label="Horizon" value={months} min={12} max={48} step={6} display={`${months} months`} onChange={setMonths} />

        <div className="row">
          <button className="btn" onClick={() => { setShock(NEUTRAL); setElasticity(-0.6); setMonths(24); }}>Reset</button>
          <span className="muted" style={{ fontSize: 12 }}>{busy ? "Simulating 5,000 runs…" : "Seed 42 · 5,000 runs"}</span>
        </div>
      </aside>

      <section className={`stack ${busy ? "loading" : ""}`}>
        {error && <div className="banner error"><strong>Simulation failed.</strong> {error}</div>}
        {result?.warnings.length ? (
          <div className="banner"><ul>{result.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
        ) : null}

        {!result && !error && <div className="skeleton" style={{ height: 420 }} />}

        {b && s && (
          <>
            <div className="card headline">
              <span className="big">In {months} months, at the median</span>
              ARR goes from{" "}
              <Value v={b.arr_end.p50} unit="currency" currency={currency} receipt={b.receipts.sim_arr_end_p50} /> to{" "}
              <Value v={s.arr_end.p50} unit="currency" currency={currency} receipt={s.receipts.sim_arr_end_p50} />{" "}
              <span className={`delta ${arrDelta > 0.001 ? "up" : arrDelta < -0.001 ? "down" : "flat"}`}>
                ({arrDelta >= 0 ? "+" : ""}{(arrDelta * 100).toFixed(1)}%)
              </span>
              {s.nrr_12m.p50 < b.nrr_12m.p50 - 0.005 && <>, but net retention weakens</>}
              {s.p_cash_out > b.p_cash_out + 0.01 && <>, and cash-out risk rises</>}.
            </div>

            <FanChart baseline={b.arr_path} scenario={s.arr_path} currency={currency} />

            <div className="card" style={{ overflowX: "auto" }}>
              <table className="compare">
                <thead>
                  <tr><th>Metric</th><th>Baseline</th><th>Scenario</th><th>Change</th></tr>
                </thead>
                <tbody>
                  {ROWS.map((row) => {
                    const bv = row.get(b), sv = row.get(s);
                    const d = bv !== null && sv !== null ? sv - bv : 0;
                    const dir = Math.abs(d) < 1e-9 ? "flat" : (d > 0) === (row.better === "up") ? "up" : "down";
                    return (
                      <tr key={row.label}>
                        <td>{row.label}</td>
                        <td>
                          <Value v={bv} unit={row.unit} currency={currency} receipt={row.receipt ? b.receipts[row.receipt] : undefined} />
                          {row.range && <span className="range num">{fmt(row.range(b)[0], row.unit, currency)} to {fmt(row.range(b)[1], row.unit, currency)}</span>}
                        </td>
                        <td>
                          <Value v={sv} unit={row.unit} currency={currency} receipt={row.receipt ? s.receipts[row.receipt] : undefined} />
                          {row.range && <span className="range num">{fmt(row.range(s)[0], row.unit, currency)} to {fmt(row.range(s)[1], row.unit, currency)}</span>}
                        </td>
                        <td className={`delta ${dir} num`}>{fmtDelta(bv, sv, row.unit, currency)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="muted" style={{ fontSize: 12, margin: 0 }}>
              Baseline is built from your data snapshot <span className="mono">{result.snapshot_id}</span>. Ranges are p10 to p90 across 5,000 seeded runs. Re-running the same inputs always returns the same numbers.
            </p>
          </>
        )}
      </section>
    </div>
  );
}
