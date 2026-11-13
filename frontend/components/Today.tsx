"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { Briefing, Finding } from "@/lib/api";
import { fmt } from "@/lib/format";
import { ROLE_META } from "@/lib/roles";
import { Avatar, MethodBadge } from "./team";
import { useReceipt } from "./ReceiptDrawer";
import { FindingActions } from "./FindingActions";

const URGENT = new Set(["critical", "high"]);

function Item({ f, me }: { f: Finding; me: string }) {
  const [open, setOpen] = useState(false);
  const { open: openReceipt } = useReceipt();
  return (
    <li className={`item ${open ? "open" : ""}`}>
      <button className="item-head" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Avatar id={f.role} size={30} />
        <span className="item-title">{f.title}</span>
        {f.tasked && <span className="pill good">Assigned</span>}
        <span className="chev" aria-hidden>{open ? "–" : "+"}</span>
      </button>
      {open && (
        <div className="item-body">
          <p>{f.summary}</p>
          {f.actions.length > 0 && (
            <>
              <div className="eyebrow">Suggested next steps</div>
              <ul className="steps">{f.actions.map((a) => <li key={a}>{a}</li>)}</ul>
            </>
          )}
          <div className="item-actions"><FindingActions f={f} me={me} /></div>
          <div className="item-foot">
            <span className="muted small">{ROLE_META[f.role]?.label} · <MethodBadge kind={f.method.kind} detail={f.method.detail} /></span>
            <span className="row">
              {f.impact_usd !== null && <span className="muted small">{fmt(f.impact_usd, "currency")} {f.impact_label}</span>}
              {f.receipts[0] && <button className="receipt-btn" onClick={() => openReceipt(f.receipts[0])}>show the working</button>}
              {f.link && <Link href={f.link} className="btn small">Open</Link>}
            </span>
          </div>
        </div>
      )}
    </li>
  );
}

export function Today({ b, name }: { b: Briefing; name: string }) {
  // Escalate at most four things; everything else waits in "also worth knowing".
  const urgent = b.findings.filter((f) => URGENT.has(f.severity)).slice(0, 4);
  const rest = b.findings.filter((f) => !urgent.includes(f));
  // The greeting depends on the viewer's clock, so it is set in the browser (server time would mismatch).
  const [greeting, setGreeting] = useState("Hello");
  useEffect(() => {
    const h = new Date().getHours();
    setGreeting(h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening");
  }, []);

  return (
    <>
      <header className="hero">
        <h1>{greeting}, {name.split(" ")[0]}.</h1>
        <p>
          {urgent.length === 0 ? "Nothing urgent today. Your team's notes are below." :
            `Your team found ${urgent.length} thing${urgent.length > 1 ? "s" : ""} that need${urgent.length > 1 ? "" : "s"} you today.`}
        </p>
      </header>

      {b.follow_ups && b.follow_ups.open > 0 && (
        <Link href="/actions" className={`followups ${b.follow_ups.overdue ? "late" : ""}`}>
          {b.follow_ups.overdue > 0
            ? `${b.follow_ups.overdue} follow-up${b.follow_ups.overdue > 1 ? "s are" : " is"} overdue`
            : `${b.follow_ups.open} follow-up${b.follow_ups.open > 1 ? "s" : ""} in progress`} →
        </Link>
      )}

      {urgent.length > 0 && (
        <section>
          <div className="eyebrow">Needs you</div>
          <ul className="items card">{urgent.map((f) => <Item key={f.id} f={f} me={name} />)}</ul>
        </section>
      )}

      <section>
        <div className="eyebrow">Your team</div>
        <div className="desks">
          {b.roles.map((r) => {
            const mine = b.findings.filter((f) => f.role === r.id);
            const top = mine[0];
            return (
              <div key={r.id} className="card desk">
                <div className="desk-head">
                  <Avatar id={r.id} size={38} />
                  <div><b>{r.label}</b><span className="muted small">{mine.length ? `${mine.length} note${mine.length > 1 ? "s" : ""}` : "All quiet"}</span></div>
                </div>
                <p className="desk-line">{top ? top.title : "Nothing new since the last data refresh."}</p>
                <div className="desk-links">{r.pages.map((p) => <Link key={p.href} href={p.href}>{p.label}</Link>)}</div>
              </div>
            );
          })}
          <Link href="/team" className="card desk add">
            <span className="plus">+</span>
            <span>Add a role</span>
          </Link>
        </div>
      </section>

      {rest.length > 0 && (
        <section>
          <div className="eyebrow">Also worth knowing</div>
          <ul className="items card">{rest.map((f) => <Item key={f.id} f={f} me={name} />)}</ul>
        </section>
      )}

      {b.warnings?.length ? (
        <section>
          <div className="eyebrow">Waiting for data</div>
          <div className="card pad waiting">
            <ul>{b.warnings.map((w) => <li key={w}>{w.replace(/^Skipped /, "").replace(/: needs /, " needs ")}</li>)}</ul>
            <Link href="/settings/data" className="btn small primary">Add data sources</Link>
          </div>
        </section>
      ) : null}
      <p className="note quiet">
        Data through {new Date(b.as_of).toLocaleDateString("en-US", { month: "long", year: "numeric", timeZone: "UTC" })}.
        Every number comes from code or a model you can inspect, never from a chatbot. <Link href="/models">How it works</Link>
      </p>
    </>
  );
}
