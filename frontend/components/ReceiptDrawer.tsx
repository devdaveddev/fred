"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Receipt } from "@/lib/api";
import { humanize } from "@/lib/format";

type Ctx = { open: (r: Receipt | string) => void };
const ReceiptCtx = createContext<Ctx>({ open: () => {} });
export const useReceipt = () => useContext(ReceiptCtx);

type Detail = Receipt & { as_of_month?: string; row_counts?: Record<string, number>; created_at?: string };

function show(v: unknown): string {
  if (v === null || v === undefined) return "n/a";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-US") : v.toLocaleString("en-US", { maximumFractionDigits: 4 });
  if (Array.isArray(v)) return v.length > 6 ? `${v.slice(0, 6).map(show).join(", ")} … (${v.length})` : v.map(show).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function Inputs({ inputs }: { inputs: Record<string, unknown> }) {
  const flat: [string, unknown][] = [];
  for (const [k, v] of Object.entries(inputs)) {
    if (v && typeof v === "object" && !Array.isArray(v)) {
      for (const [k2, v2] of Object.entries(v as Record<string, unknown>)) flat.push([`${k} · ${k2}`, v2]);
    } else flat.push([k, v]);
  }
  return (
    <table className="inputs-table">
      <tbody>
        {flat.map(([k, v]) => (
          <tr key={k}>
            <td>{humanize(k)}</td>
            <td>{show(v)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ReceiptProvider({ children }: { children: ReactNode }) {
  const [rec, setRec] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = useCallback((r: Receipt | string) => {
    setError(null);
    if (typeof r !== "string") setRec(r);
    const id = typeof r === "string" ? r : r.id;
    // Fetch the stored copy: proves the number was persisted, and adds snapshot details.
    api.receipt(id).then((d) => setRec(d)).catch((e) => setError(String(e.message ?? e)));
  }, []);

  const close = () => { setRec(null); setError(null); };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <ReceiptCtx.Provider value={{ open }}>
      {children}
      {(rec || error) && (
        <>
          <div className="scrim" onClick={close} />
          <aside className="drawer" role="dialog" aria-label="Receipt">
            <button className="drawer-close" onClick={close} aria-label="Close">×</button>
            <div className="mono muted">RECEIPT</div>
            {error && !rec && <p className="banner error">{error}</p>}
            {rec && (
              <>
                <h2>{humanize(rec.metric)}</h2>
                <div className="metric-value num">{show(rec.value)}</div>
                <p className="muted" style={{ fontSize: 12 }}>
                  This number was computed by revcore, not by an AI model. Same snapshot, formula version and seed always give the same value.
                </p>
                <div className="section-title">Formula</div>
                <div className="formula">{rec.formula}</div>
                <dl className="kv">
                  <dt>Receipt id</dt><dd className="mono">{rec.id}</dd>
                  <dt>Formula version</dt><dd className="mono">{rec.formula_version}</dd>
                  <dt>Input snapshot</dt><dd className="mono">{rec.input_snapshot}</dd>
                  {rec.as_of_month && (<><dt>Data as of</dt><dd>{rec.as_of_month}</dd></>)}
                  {rec.seed !== null && rec.seed !== undefined && (<><dt>Random seed</dt><dd className="mono">{rec.seed}</dd></>)}
                  {rec.row_counts && (<><dt>Rows in snapshot</dt><dd>{Object.entries(rec.row_counts).map(([k, v]) => `${v.toLocaleString("en-US")} ${humanize(k)}`).join(" · ")}</dd></>)}
                </dl>
                <div className="section-title">Inputs</div>
                <Inputs inputs={rec.inputs} />
              </>
            )}
          </aside>
        </>
      )}
    </ReceiptCtx.Provider>
  );
}
