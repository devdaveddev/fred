"use client";

import { useState } from "react";
import { fmt, monthLabel } from "@/lib/format";
import { useReceipt } from "./ReceiptDrawer";

type Point = { month: string; value: number; receipt_id: string };

const W = 900, H = 240, PAD = { l: 56, r: 12, t: 12, b: 26 };

function niceMax(v: number) {
  const p = 10 ** Math.floor(Math.log10(v));
  return Math.ceil(v / p) * p;
}

export function MrrChart({ points, currency }: { points: Point[]; currency: string }) {
  const { open } = useReceipt();
  const [hover, setHover] = useState<number | null>(null);
  if (!points.length) return null;

  const max = niceMax(Math.max(...points.map((p) => p.value)));
  const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const bw = iw / points.length;
  const y = (v: number) => PAD.t + ih - (v / max) * ih;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);
  const first = points[0].value, last = points[points.length - 1].value;
  const growth = first ? (last / first - 1) : 0;

  return (
    <div className="card chart-card">
      <div className="chart-head">
        <h3>MRR, last {points.length} months</h3>
        <span className="muted num">
          {fmt(first, "currency", currency)} → {fmt(last, "currency", currency)} ({growth >= 0 ? "+" : ""}{(growth * 100).toFixed(0)}%)
        </span>
      </div>
      <div className="chart" style={{ position: "relative" }}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Monthly recurring revenue by month">
          <g className="axis">
            {ticks.map((t) => (
              <g key={t}>
                <line className="gridline" x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} />
                <text x={PAD.l - 8} y={y(t) + 4} textAnchor="end">{fmt(t, "currency", currency)}</text>
              </g>
            ))}
            {points.map((p, i) => (i % 3 === 0 || i === points.length - 1) && (
              <text key={p.month} x={PAD.l + bw * i + bw / 2} y={H - 6} textAnchor="middle">{monthLabel(p.month)}</text>
            ))}
          </g>
          {points.map((p, i) => (
            <rect
              key={p.month}
              x={PAD.l + bw * i + bw * 0.18}
              width={bw * 0.64}
              y={y(p.value)}
              height={PAD.t + ih - y(p.value)}
              rx={3}
              fill="var(--accent)"
              opacity={hover === null || hover === i ? 0.9 : 0.4}
              style={{ cursor: "pointer" }}
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
              onClick={() => open(p.receipt_id)}
            />
          ))}
        </svg>
        {hover !== null && (
          <div className="tooltip num" style={{ left: `${((PAD.l + bw * hover + bw / 2) / W) * 100}%`, top: `${(y(points[hover].value) / H) * 100}%` }}>
            {monthLabel(points[hover].month)} · {fmt(points[hover].value, "currency", currency)} · click for receipt
          </div>
        )}
      </div>
    </div>
  );
}
