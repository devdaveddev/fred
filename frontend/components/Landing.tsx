"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

/* ------------------------------------------------------------------ scroll helpers */
function useProgress<T extends HTMLElement>(start = 0.85, span = 0.7) {
  const ref = useRef<T>(null);
  const [p, setP] = useState(0);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) { setP(1); return; }
    let raf = 0;
    const tick = () => {
      raf = 0;
      const el = ref.current;
      if (!el) return;
      const r = el.getBoundingClientRect();
      const vh = window.innerHeight;
      setP(Math.min(1, Math.max(0, (vh * start - r.top) / (r.height + vh * span))));
    };
    const on = () => { if (!raf) raf = requestAnimationFrame(tick); };
    tick();
    window.addEventListener("scroll", on, { passive: true });
    window.addEventListener("resize", on);
    return () => { window.removeEventListener("scroll", on); window.removeEventListener("resize", on); cancelAnimationFrame(raf); };
  }, [start, span]);
  return [ref, p] as const;
}

type Seg = { t: string; s?: "serif" | "glow" | "line" };

/** Words come into focus as the reader scrolls past them. */
function Reveal({ segs, className = "" }: { segs: Seg[]; className?: string }) {
  const [ref, p] = useProgress<HTMLParagraphElement>(0.9, 0.2);
  const words: { w: string; s?: Seg["s"] }[] = [];
  segs.forEach((seg) => seg.t.split(" ").filter(Boolean).forEach((w) => words.push({ w, s: seg.s })));
  const lit = p * words.length * 1.15;
  return (
    <p ref={ref} className={`lp-reveal ${className}`}>
      {words.map((x, i) => {
        const on = Math.min(1, Math.max(0, lit - i));
        return (
          <span key={i} className={`w ${x.s ?? ""}`} style={{ opacity: 0.12 + 0.88 * on, filter: `blur(${(1 - on) * 6}px)` }}>
            {x.w}{" "}
          </span>
        );
      })}
    </p>
  );
}

/* ------------------------------------------------------------------ role cards (mock product views) */
const ROLES = [
  {
    n: "01", id: "founders_office", title: "Founder's office", italic: "the close, the plan, the board.",
    instead: "BizOps / strategic finance lead",
    does: ["Budget vs actual with the reason behind every variance", "12-month forecast, goal planning and cash runway", "Board pack and investor update, every number traced", "Department budgets: drafted, reviewed, published"],
    view: (
      <div className="mock bridge">
        <div className="mock-k">Revenue vs plan, YTD · bridge</div>
        {[["Plan", 100, "base"], ["New-logo volume", -14, "neg"], ["Churn", -5, "neg"], ["Pricing", 2, "pos"], ["Expansion", 1, "pos"], ["Actual", 84, "base"]].map(([l, v, k]) => (
          <div key={l as string} className="br-row"><span>{l}</span><i className={k as string} style={{ width: `${Math.abs(v as number) * (k === "base" ? 0.8 : 3)}%` }} /></div>
        ))}
        <div className="mock-foot">Exact: the four causes add up to the gap.</div>
      </div>
    ),
  },
  {
    n: "02", id: "business_analyst", title: "Business analyst", italic: "who's about to leave, and why.",
    instead: "Business / data analyst",
    does: ["Health metrics against benchmarks", "Accounts likely to churn, each with its reason and a play", "Why customers leave, read from surveys, tickets and NPS"],
    view: (
      <div className="mock list">
        <div className="mock-k">At-risk accounts · next quarter</div>
        {[["Northwind", 71, "Seats used fell 38% in 8 weeks"], ["Bluebird Labs", 54, "No admin login since onboarding"], ["Kestrel Pay", 43, "Tickets about SSO, 3 this month"]].map(([n, p, r]) => (
          <div key={n as string} className="li"><b>{n}</b><span className="bar"><i style={{ width: `${p}%` }} /></span><em>{p}%</em><small>{r}</small></div>
        ))}
      </div>
    ),
  },
  {
    n: "03", id: "market_researcher", title: "Market researcher", italic: "the customers you actually win.",
    instead: "Market research analyst",
    does: ["Customer segments found from what companies say they do", "Which segments to double down on, and which to stop chasing", "Lookalike prospects for the best segment"],
    view: (
      <div className="mock seg">
        <div className="mock-k">Segments discovered · retention</div>
        {[[22, 30, 46, "Payments APIs", "a"], [58, 22, 34, "Dev tooling", "b"], [70, 62, 22, "Fashion labels", "c"], [32, 66, 28, "Clinics", "b"]].map(([x, y, r, l, k]) => (
          <span key={l as string} className={`dot ${k}`} style={{ left: `${x}%`, top: `${y}%`, width: r as number, height: r as number }}><em>{l}</em></span>
        ))}
      </div>
    ),
  },
  {
    n: "04", id: "lead_generator", title: "Lead generator", italic: "every lead scored and routed.",
    instead: "Lead researcher / SDR triage",
    does: ["Every open lead scored, with the reasons in plain words", "Routing to AE, SDR or nurture", "Your real ideal customer, tested for significance", "Scores written back to HubSpot or Salesforce"],
    view: (
      <div className="mock list">
        <div className="mock-k">Open leads · fast lane</div>
        {[["VP Finance · Acme", 82, "Requested a demo; viewed pricing 6×"], ["Head of Ops · Lumen", 64, "Referral; fintech, 240 staff"], ["Founder · Tidal", 19, "Free email; no product activity"]].map(([n, p, r]) => (
          <div key={n as string} className="li"><b>{n}</b><span className="bar g"><i style={{ width: `${p}%` }} /></span><em>{p}%</em><small>{r}</small></div>
        ))}
      </div>
    ),
  },
  {
    n: "05", id: "marketing_analyst", title: "Marketing analyst", italic: "where the next dollar goes.",
    instead: "Marketing / performance analyst",
    does: ["CAC, payback and LTV:CAC by channel", "Same budget, moved to where it buys more customers", "Alerts when spend jumps and leads don't"],
    view: (
      <div className="mock curve">
        <div className="mock-k">Response curves · same budget</div>
        <svg viewBox="0 0 300 140" preserveAspectRatio="none" aria-hidden>
          <path d="M0 135 C 60 60, 140 35, 300 25" className="c1" />
          <path d="M0 135 C 80 100, 180 80, 300 72" className="c2" />
          <path d="M0 135 C 50 120, 150 112, 300 108" className="c3" />
        </svg>
        <div className="mock-foot">Content +$4K/mo · LinkedIn −$4K/mo → +3 customers a month</div>
      </div>
    ),
  },
];

function Rail({ picked, toggle }: { picked: string[]; toggle: (id: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; left: number; moved: boolean } | null>(null);
  const [edge, setEdge] = useState({ start: true, end: false });

  const update = () => {
    const el = box.current;
    if (el) setEdge({ start: el.scrollLeft < 8, end: el.scrollLeft + el.clientWidth > el.scrollWidth - 8 });
  };
  useEffect(() => {
    update();
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  const step = (dir: 1 | -1) => {
    const el = box.current;
    const card = el?.querySelector<HTMLElement>(".lp-card");
    if (el && card) el.scrollBy({ left: dir * (card.offsetWidth + 32), behavior: "smooth" });
  };

  return (
    <section className="lp-sec lp-team" id="team">
      <div className="lp-team-head">
        <div>
          <div className="lp-label">02 / The team</div>
          <h2 className="lp-h2">Five roles.<br /><span className="serif glow">Pick yours.</span></h2>
        </div>
        <div className="lp-team-side">
          <p className="lp-small">Click a role to add it. Each starts as soon as it has the data it needs. Views use example data.</p>
          <div className="lp-arrows">
            <button aria-label="Previous role" onClick={() => step(-1)} disabled={edge.start}>←</button>
            <button aria-label="Next role" onClick={() => step(1)} disabled={edge.end}>→</button>
          </div>
        </div>
      </div>
      <div
        ref={box}
        className="lp-scroller"
        onScroll={update}
        onPointerDown={(e) => {
          if (e.pointerType !== "mouse" || !box.current) return;
          drag.current = { x: e.clientX, left: box.current.scrollLeft, moved: false };
        }}
        onPointerMove={(e) => {
          const d = drag.current;
          if (!d || !box.current) return;
          const dx = e.clientX - d.x;
          if (Math.abs(dx) > 5) { d.moved = true; box.current.classList.add("dragging"); }
          if (d.moved) box.current.scrollLeft = d.left - dx;
        }}
        onPointerUp={() => { setTimeout(() => { drag.current = null; }, 0); box.current?.classList.remove("dragging"); }}
        onPointerLeave={() => { drag.current = null; box.current?.classList.remove("dragging"); }}
      >
        {ROLES.map((r) => {
          const on = picked.includes(r.id);
          return (
            <button
              key={r.id} type="button" className={`lp-card ${on ? "on" : ""}`} aria-pressed={on}
              onClick={() => { if (!drag.current?.moved) toggle(r.id); }}
            >
              <div className="lp-card-top"><span>{r.n} / {r.title}</span><span className="lp-add">{on ? "Added ✓" : "Add +"}</span></div>
              <div className="lp-card-view">{r.view}</div>
              <h3>{r.title}: <span className="serif">{r.italic}</span></h3>
              <p className="lp-instead">Instead of hiring a {r.instead.toLowerCase()}</p>
              <ul>{r.does.map((d) => <li key={d}>{d}</li>)}</ul>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function PickBar({ picked, signedIn, clear }: { picked: string[]; signedIn: boolean; clear: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (!picked.length) return null;
  const names = ROLES.filter((r) => picked.includes(r.id)).map((r) => r.title);
  const go = async () => {
    if (!signedIn) { window.location.href = `/signup?hire=${picked.join(",")}`; return; }
    setBusy(true); setErr(null);
    for (const id of picked) {
      const r = await fetch(`/api/team/${id}/hire`, { method: "POST" });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setErr(j.detail ?? "Couldn't add the roles."); setBusy(false); return;
      }
    }
    window.location.href = "/team";
  };
  return (
    <div className="lp-pickbar" role="status">
      <span><b>{picked.length}</b> {picked.length === 1 ? "role" : "roles"} · <span className="muted">{names.join(", ")}</span></span>
      {err && <span className="err">{err}</span>}
      <button className="lp-linkbtn" onClick={clear}>Clear</button>
      <button className="lp-btn" onClick={go} disabled={busy}>{busy ? "Adding…" : signedIn ? "Add to my team ↗" : "Start with these ↗"}</button>
    </div>
  );
}

/* ------------------------------------------------------------------ page */
const SOURCES = ["Stripe", "HubSpot", "Salesforce", "QuickBooks", "Xero", "Gusto", "Google Ads", "Meta Ads", "Zendesk", "Intercom", "Slack", "CSV from anything"];
const PROOF = [
  ["2.0%", "average miss on next-month MRR forecasts", "vs 3.5% for “assume nothing changes”"],
  ["100%", "of revenue and payroll gaps traced to named causes", "exact bridges, not estimates"],
  ["4×", "churn rate among the 10% of accounts Fred ranked riskiest", "30% vs 7% overall, on cases it never saw"],
  ["1 of 2", "early warnings held in the following months", "the miss is shown, not hidden"],
];

export function Landing({ signedIn }: { signedIn: boolean }) {
  const [picked, setPicked] = useState<string[]>([]);
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  const cta = signedIn ? { href: "/", label: "Open Fred" } : { href: "/signup", label: "Start with a demo company" };
  return (
    <div className="lp">
      <nav className="lp-nav">
        <Link href="/welcome" className="lp-logo">fred<span>.</span></Link>
        <div className="lp-links">
          <a href="#team">Team</a><a href="#how">How it works</a><a href="#proof">Proof</a><a href="#trust">Security</a>
          {signedIn ? <Link href="/" className="lp-pill">Open app ↗</Link> : <><Link href="/login">Sign in</Link><Link href="/signup" className="lp-pill">Start free ↗</Link></>}
        </div>
      </nav>

      <header className="lp-hero">
        <div className="lp-kicker">
          <span>Business operations, as software.</span>
          <span className="serif glow">Your numbers. Shown working.</span>
        </div>
        <h1 className="lp-giant">
          <span className="g1">Your analyst</span>
          <span className="g2 serif fluid">team.</span>
        </h1>
        <div className="lp-hero-foot">
          <p>Fred does the analysis work of a BizOps lead, a business analyst, a market researcher, a lead qualifier and a marketing analyst, on your own billing, books, CRM and payroll. Every morning, without being asked.</p>
          <div className="lp-ctas"><Link href={cta.href} className="lp-btn">{cta.label} ↗</Link><a href="#how" className="lp-ghost">See how it works ↓</a></div>
        </div>
      </header>

      <section className="lp-sec">
        <div className="lp-label">01 / The problem</div>
        <Reveal segs={[
          { t: "You pay" }, { t: "twice:", s: "serif" }, { t: "for the tools, and for" },
          { t: "the people who run them.", s: "glow" }, { t: "Fred does" }, { t: "the work.", s: "line" },
        ]} />
        <p className="lp-note">$100–250K a year in tools, plus $450–600K in salaries, at a typical Series A company. A named person still owns every decision.</p>
      </section>

      <Rail picked={picked} toggle={toggle} />

      <section className="lp-sec" id="how">
        <div className="lp-label">03 / How it works</div>
        <h2 className="lp-h2">Connect once.<br /><span className="serif glow">It keeps working.</span></h2>
        <div className="lp-steps">
          {[
            ["Connect", "Sign in to your tools or upload exports. Fred maps your chart of accounts, rebuilds MRR from invoices, and tells each role what data it's still missing."],
            ["It works daily", "Every morning it syncs, re-runs the analysis and writes findings ranked by money at stake, each with its receipt."],
            ["You hear only news", "A Slack or email digest with what changed. Nothing new, no message."],
            ["Work gets owned", "Turn a finding into a task with an owner and a due date. Fred measures on the next data whether the fix is working."],
            ["Deliverables", "Board pack, investor update and decision memos, blocked from publishing if any number can't be traced."],
          ].map(([t, d], i) => (
            <div key={t} className="lp-step"><span className="lp-num">0{i + 1}</span><h4>{t}</h4><p>{d}</p></div>
          ))}
        </div>
        <div className="lp-marquee" aria-label="Sources Fred connects to">
          <div>{[...SOURCES, ...SOURCES].map((s, i) => <span key={i}>{s}</span>)}</div>
        </div>
      </section>

      <section className="lp-sec" id="proof">
        <div className="lp-label">04 / Proof</div>
        <h2 className="lp-h2">It replays your<br /><span className="serif glow">last six closes.</span></h2>
        <p className="lp-lede">Before you trust it, Fred re-runs its own calls using only the data it had at the time and checks them against what happened. Results from the demo company:</p>
        <div className="lp-proof">
          {PROOF.map(([n, l, s]) => (
            <div key={l} className="lp-stat"><b className="serif glow">{n}</b><span>{l}</span><small>{s}</small></div>
          ))}
        </div>
        <div className="lp-method">
          <div className="lp-label">How numbers are made</div>
          <ol>
            <li><b>Rules</b><span>Bridges, classifications, playbooks</span></li>
            <li><b>Statistics</b><span>Forecasts, response curves, anomaly tests</span></li>
            <li><b>Models trained on your data</b><span>Churn and lead scoring, calibrated, tested on later data</span></li>
            <li><b>Open pre-trained models</b><span>Segments and feedback themes, running on our servers</span></li>
            <li className="dim"><b>Language models</b><span>Never for numbers</span></li>
          </ol>
        </div>
      </section>

      <section className="lp-sec" id="trust">
        <div className="lp-label">05 / Trust</div>
        <Reveal className="small" segs={[
          { t: "Single sign-on with your identity provider. An" }, { t: "audit log", s: "serif" },
          { t: "of every sign-in, permission change and export. Credentials encrypted at rest and never shown again." },
          { t: "Your data, exportable and deletable", s: "glow" }, { t: "by you, any time." },
        ]} />
        <div className="lp-honest">
          <div className="lp-label">What still needs a person</div>
          <p>Negotiating, persuading other teams, and judgment calls the data can&apos;t settle. Fred does the analysis and writes it up; your team decides. It doesn&apos;t sell contact lists or send outbound email: it connects to the tools that do.</p>
        </div>
      </section>

      <PickBar picked={picked} signedIn={signedIn} clear={() => setPicked([])} />

      <footer className="lp-end">
        <h2 className="lp-giant small"><span className="g1">Hire the</span> <span className="serif fluid">team.</span></h2>
        <div className="lp-ctas"><Link href={cta.href} className="lp-btn">{cta.label} ↗</Link>{!signedIn && <Link href="/login" className="lp-ghost">Sign in</Link>}</div>
        <div className="lp-foot"><span>fred.</span><span>Every number traceable to its source.</span><a href="https://github.com/devdaveddev/fred">Source ↗</a></div>
      </footer>
    </div>
  );
}
