"use client";

import type { Metric } from "@/lib/api";
import { fmt } from "@/lib/format";
import { useReceipt } from "./ReceiptDrawer";

const STATUS_TEXT = { good: "Healthy", watch: "Watch", bad: "At risk", neutral: "" } as const;

export function MetricCard({ m, currency }: { m: Metric; currency: string }) {
  const { open } = useReceipt();
  return (
    <div className="card metric">
      <div className="metric-top">
        <span className="metric-label">{m.label}</span>
        <span className={`pill ${m.status}`}>{STATUS_TEXT[m.status]}</span>
      </div>
      <button className="value-link metric-value num" style={{ alignSelf: "flex-start" }} onClick={() => open(m.receipt)}
        title="Show how this number was computed">
        {m.id === "runway_months" && m.value === null ? "Not burning" : fmt(m.value, m.unit, currency)}
      </button>
      <div className="metric-help">{m.help}</div>
      <div className="metric-foot">
        <span className="metric-help">{m.benchmark ?? ""}</span>
        <button className="receipt-btn" onClick={() => open(m.receipt)} aria-label={`Receipt for ${m.label}`}>
          receipt
        </button>
      </div>
    </div>
  );
}
