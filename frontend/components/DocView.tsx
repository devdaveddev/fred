"use client";

import { useState } from "react";
import type { Doc, DocBlock } from "@/lib/api";
import { useReceipt } from "./ReceiptDrawer";

function Block({ b }: { b: DocBlock }) {
  if (b.type === "p") return <p>{b.text}</p>;
  if (b.type === "callout") return <div className={`doc-callout ${b.tone}`}>{b.text}</div>;
  if (b.type === "bullets") return <ul>{(b.items as string[]).map((x, i) => <li key={i}>{x}</li>)}</ul>;
  if (b.type === "kpis") {
    return (
      <div className="doc-kpis">
        {(b.items as { label: string; value: string }[]).map((k) => (
          <div key={k.label}><span>{k.label}</span><b>{k.value}</b></div>
        ))}
      </div>
    );
  }
  return (
    <table className="table doc-table">
      <thead><tr>{b.columns!.map((c, i) => <th key={c} className={i ? "r" : ""}>{c}</th>)}</tr></thead>
      <tbody>{b.rows!.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={j && /\d/.test(c) ? "r num" : ""}>{c}</td>)}</tr>)}</tbody>
    </table>
  );
}

export function DocView({ d }: { d: Doc }) {
  const { open } = useReceipt();
  const [copied, setCopied] = useState(false);
  const [showFacts, setShowFacts] = useState(false);
  const v = d.verification;
  const board = d.kind === "board_pack";

  const copy = async () => {
    const r = await fetch(`/api/documents/${d.id}/markdown`);
    const { markdown } = await r.json();
    await navigator.clipboard.writeText(markdown);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <>
      <div className="doc-toolbar no-print">
        <span className={`verify ${v.ok ? "ok" : "bad"}`}>
          {v.ok ? `✓ All ${v.numbers} numbers trace to your data` : `${v.untraced.length} numbers could not be traced`}
        </span>
        <span className="row">
          <button className="btn small" onClick={() => setShowFacts((s) => !s)}>{showFacts ? "Hide sources" : "Show sources"}</button>
          <button className="btn small" onClick={copy}>{copied ? "Copied" : "Copy as text"}</button>
          <button className="btn small primary" onClick={() => window.print()}>Download PDF</button>
        </span>
      </div>
      <article className={`doc ${board ? "doc-board" : ""}`}>
        <header className="doc-head">
          <h1>{d.body.title}</h1>
          <p className="muted">{d.body.subtitle}</p>
        </header>
        {d.body.sections.map((s) => (
          <section key={s.id} className="doc-section">
            <h2>{s.heading}</h2>
            {s.blocks.map((b, i) => <Block key={i} b={b} />)}
          </section>
        ))}
        <footer className="doc-foot muted small">Prepared by Fred from data snapshot {d.snapshot_id}. Every figure traces to a recorded calculation.</footer>
      </article>
      {showFacts && (
        <div className="card pad no-print" style={{ marginTop: 16 }}>
          <h3>Where every number came from</h3>
          <table className="table">
            <thead><tr><th>Shown as</th><th>What it is</th><th>Source</th></tr></thead>
            <tbody>
              {d.facts.map((f) => (
                <tr key={f.key}>
                  <td className="num">{f.shown.join(", ")}</td>
                  <td>{f.label || f.key.replace(/_/g, " ")}</td>
                  <td>{f.receipt_id ? <button className="receipt-btn" onClick={() => open(f.receipt_id!)}>receipt</button>
                    : <span className="muted small">{f.unit === "constant" ? "definition" : f.unit}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
