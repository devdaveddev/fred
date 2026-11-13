"use client";

import Link from "next/link";
import type { Finding, MethodKind, Severity } from "@/lib/api";
import { fmt } from "@/lib/format";
import { useReceipt } from "./ReceiptDrawer";

import { ROLE_LABEL, ROLE_META } from "@/lib/roles";

const METHOD_LABEL: Record<MethodKind, string> = {
  rule: "Rule",
  statistics: "Statistics",
  ml: "ML model",
  optimization: "Optimizer",
  llm: "LLM",
};

export function MethodBadge({ kind, detail }: { kind: MethodKind; detail?: string }) {
  return (
    <span className={`method m-${kind}`} title={detail}>
      {METHOD_LABEL[kind]}
    </span>
  );
}

export function SeverityDot({ s }: { s: Severity }) {
  return <span className={`sev sev-${s}`} aria-label={s} title={s} />;
}

export function FindingCard({ f }: { f: Finding }) {
  const { open } = useReceipt();
  return (
    <article className={`card finding sev-border-${f.severity}`}>
      <header className="finding-head">
        <div className="finding-meta">
          <SeverityDot s={f.severity} />
          <span className="muted">{ROLE_LABEL[f.role] ?? f.role}</span>
          <MethodBadge kind={f.method.kind} detail={f.method.detail} />
        </div>
        {f.impact_usd !== null && (
          <div className="impact">
            <b className="num">{fmt(f.impact_usd, "currency")}</b>
            <span>{f.impact_label}</span>
          </div>
        )}
      </header>
      <h3>{f.title}</h3>
      <p className="finding-summary">{f.summary}</p>
      <ul className="actions">
        {f.actions.map((a) => <li key={a}>{a}</li>)}
      </ul>
      <footer className="finding-foot">
        <span className="muted method-detail">{f.method.detail}</span>
        <span className="row">
          {f.frameworks.length > 0 && <span className="muted frameworks">{f.frameworks.join(" · ")}</span>}
          {f.receipts.slice(0, 3).map((r) => (
            <button key={r} className="receipt-btn" onClick={() => open(r)}>receipt</button>
          ))}
          {f.link && <Link href={f.link} className="btn small">Open →</Link>}
        </span>
      </footer>
    </article>
  );
}

export function ProbBar({ p }: { p: number }) {
  return (
    <span className="probbar" title={`${(p * 100).toFixed(1)}%`}>
      <span className="track"><i style={{ width: `${Math.min(100, p * 100)}%` }} /></span>
      <b className="num">{(p * 100).toFixed(0)}%</b>
    </span>
  );
}

export function Avatar({ id, size = 36 }: { id: string; size?: number }) {
  const m = ROLE_META[id] ?? { initials: "?", hue: 220 };
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.36, ["--h" as string]: m.hue }} aria-hidden>
      {m.initials}
    </span>
  );
}
