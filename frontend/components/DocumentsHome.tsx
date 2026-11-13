"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import type { DocMeta } from "@/lib/api";
import { send } from "@/lib/client";
import { localTime } from "@/lib/format";

const KIND: Record<string, string> = { investor_update: "Investor update", board_pack: "Board pack", decision_memo: "Decision memo" };

export function DocumentsHome({ docs, canCreate }: { docs: DocMeta[]; canCreate: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [memo, setMemo] = useState<"hire" | "price" | "spend">("hire");

  const create = async (body: object, key: string) => {
    setBusy(key);
    setErr(null);
    try {
      const r = await send<{ id: string }>("/documents", "POST", body);
      router.push(`/documents/${r.id}`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  };

  const submitMemo = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const n = (k: string) => Number(f.get(k));
    const params = memo === "hire" ? { role: f.get("role"), salary_annual: n("salary"), count: n("count") }
      : memo === "price" ? { price_change: n("price") / 100, churn_multiplier: n("churn") }
      : { spend_change: n("spend") / 100 };
    create({ kind: "decision_memo", memo_type: memo, params }, "memo");
  };

  return (
    <>
      {canCreate ? (
        <div className="doc-create">
          <div className="card doc-option">
            <h3>Investor update</h3>
            <p className="muted">This month&apos;s numbers, what went well, what didn&apos;t, what you&apos;re doing about it, and your asks.</p>
            <button className="btn primary" disabled={!!busy} onClick={() => create({ kind: "investor_update" }, "iu")}>
              {busy === "iu" ? "Writing…" : "Write it"}
            </button>
          </div>
          <div className="card doc-option">
            <h3>Board pack</h3>
            <p className="muted">Financials vs plan, customers, go-to-market, team, risks and the decisions you need from the board.</p>
            <button className="btn primary" disabled={!!busy} onClick={() => create({ kind: "board_pack" }, "bp")}>
              {busy === "bp" ? "Writing…" : "Write it"}
            </button>
          </div>
          <form className="card doc-option" onSubmit={submitMemo}>
            <h3>Decision memo</h3>
            <div className="tabs">
              {(["hire", "price", "spend"] as const).map((m) => (
                <button type="button" key={m} className={`preset ${memo === m ? "on" : ""}`} onClick={() => setMemo(m)}>
                  {m === "hire" ? "Should we hire?" : m === "price" ? "Change prices?" : "Change marketing spend?"}
                </button>
              ))}
            </div>
            <div className="memo-fields">
              {memo === "hire" && (<>
                <label>Role<input name="role" defaultValue="Account Executive" required /></label>
                <label>Salary / year (USD)<input name="salary" type="number" defaultValue={115000} min={10000} required /></label>
                <label>How many<input name="count" type="number" defaultValue={1} min={1} max={50} required /></label>
              </>)}
              {memo === "price" && (<>
                <label>Price change (%)<input name="price" type="number" defaultValue={15} min={-50} max={200} required /></label>
                <label>Expected churn reaction (×)<input name="churn" type="number" step="0.05" defaultValue={1.1} min={0.5} max={3} required /></label>
              </>)}
              {memo === "spend" && (
                <label>Sales &amp; marketing change (%)<input name="spend" type="number" defaultValue={30} min={-80} max={300} required /></label>
              )}
            </div>
            <button className="btn primary" disabled={!!busy}>{busy === "memo" ? "Thinking it through…" : "Write the memo"}</button>
          </form>
        </div>
      ) : (
        <div className="banner">Add the founder&apos;s office to your team to write investor updates, board packs and decision memos.</div>
      )}
      {err && <div className="banner error">{err}</div>}

      <div className="eyebrow" style={{ marginTop: 28 }}>Written so far</div>
      {docs.length === 0 ? <p className="muted">Nothing yet.</p> : (
        <ul className="card plain-list">
          {docs.map((d) => (
            <li key={d.id} className="doc-row">
              <Link href={`/documents/${d.id}`}><b>{d.title}</b></Link>
              <span className="muted small" suppressHydrationWarning>{KIND[d.kind]} · {localTime(d.created_at, false)}{d.author ? ` · ${d.author}` : ""}</span>
              <span className={`pill ${d.verification.ok ? "good" : "bad"}`}>{d.verification.ok ? `${d.verification.numbers} numbers verified` : "Check numbers"}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
