"use client";

import type { Bands } from "@/lib/api";
import { fmt } from "@/lib/format";

type Path = ({ month: number } & Bands)[];
const W = 900, H = 260, PAD = { l: 60, r: 14, t: 12, b: 28 };

export function FanChart({ baseline, scenario, currency }: { baseline: Path; scenario: Path; currency: string }) {
  const all = [...baseline, ...scenario];
  const lo = Math.min(...all.map((p) => p.p10)) * 0.95;
  const hi = Math.max(...all.map((p) => p.p90)) * 1.02;
  const n = baseline.length;
  const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const x = (m: number) => PAD.l + ((m - 1) / Math.max(1, n - 1)) * iw;
  const y = (v: number) => PAD.t + ih - ((v - lo) / (hi - lo)) * ih;
  const band = (p: Path) =>
    `M${p.map((d) => `${x(d.month)},${y(d.p90)}`).join("L")}L${[...p].reverse().map((d) => `${x(d.month)},${y(d.p10)}`).join("L")}Z`;
  const line = (p: Path) => `M${p.map((d) => `${x(d.month)},${y(d.p50)}`).join("L")}`;
  const ticks = Array.from({ length: 5 }, (_, i) => lo + ((hi - lo) * i) / 4);

  return (
    <div className="card chart-card">
      <div className="chart-head">
        <h3>ARR path, median with p10 to p90 band</h3>
        <div className="legend">
          <span><i style={{ background: "var(--base-series)" }} />Baseline</span>
          <span><i style={{ background: "var(--scen-series)" }} />Scenario</span>
        </div>
      </div>
      <div className="chart">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Projected ARR, baseline vs scenario">
          <g className="axis">
            {ticks.map((t) => (
              <g key={t}>
                <line className="gridline" x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} />
                <text x={PAD.l - 8} y={y(t) + 4} textAnchor="end">{fmt(t, "currency", currency)}</text>
              </g>
            ))}
            {baseline.filter((d) => d.month === 1 || d.month % 6 === 0).map((d) => (
              <text key={d.month} x={x(d.month)} y={H - 8} textAnchor="middle">M{d.month}</text>
            ))}
          </g>
          <path d={band(baseline)} fill="var(--base-series)" opacity={0.16} />
          <path d={band(scenario)} fill="var(--scen-series)" opacity={0.16} />
          <path d={line(baseline)} fill="none" stroke="var(--base-series)" strokeWidth={2} strokeDasharray="5 4" />
          <path d={line(scenario)} fill="none" stroke="var(--scen-series)" strokeWidth={2.5} />
        </svg>
      </div>
    </div>
  );
}
