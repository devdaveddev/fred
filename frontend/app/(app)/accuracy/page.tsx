import { PageError } from "@/components/PageError";
import { fmt, monthLabel } from "@/lib/format";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Track record · Fred" };

const pct = (v: number, d = 1) => `${(v * 100).toFixed(d)}%`;
const signed = (v: number) => `${v >= 0 ? "+" : "−"}${pct(Math.abs(v))}`;
const ICON = { good: "✓", watch: "!", bad: "✕" } as const;

export default async function AccuracyPage() {
  try {
    const r = await sapi.accuracy();
    const f1 = r.forecast.rows.filter((x) => x.horizon === 1);
    const f3 = r.forecast.rows.filter((x) => x.horizon === 3);
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Track record</h1>
            <p>Fred replayed your last six month-ends using only the data it would have had at the time, then compared its calls with what actually happened. Nothing here is tuned to look good.</p>
          </div>
        </div>

        <div className="verdict-grid">
          {r.verdict.map((v) => (
            <div key={v.check} className={`card verdict ${v.state}`}>
              <div className="verdict-head"><span className="verdict-icon">{ICON[v.state]}</span>{v.check}</div>
              <p>{v.text}</p>
            </div>
          ))}
        </div>

        {r.early_warnings && r.early_warnings.n > 0 && (
          <section className="card pad">
            <h3>Warnings Fred raised, and what happened next</h3>
            <ul className="calls">
              {r.early_warnings.calls.map((c) => (
                <li key={c.line} className={c.right ? "right" : "wrong"}>
                  <b>{c.right ? "Held" : "Didn't hold"}</b> · {c.said}
                  <span className="muted small"> (raised after the {monthLabel(c.flagged_in)} close; same direction in {Math.round(c.held_share * c.months_after)} of {c.months_after} months since)</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <details className="card pad">
          <summary><b>Forecast: predicted vs actual MRR</b> <span className="muted small">· made one month ahead{f3.length ? " and three months ahead" : ""}</span></summary>
          <table className="table compact">
            <thead><tr><th>Month</th><th>Ahead</th><th className="r">Fred said</th><th className="r">Actual</th><th className="r">Fred off by</th><th className="r">“No change” off by</th></tr></thead>
            <tbody>{[...f1, ...f3].map((x) => (
              <tr key={`${x.month}-${x.horizon}`}>
                <td>{monthLabel(x.month)}</td><td>{x.horizon} mo</td>
                <td className="r">{fmt(x.predicted, "currency")}</td><td className="r">{fmt(x.actual, "currency")}</td>
                <td className="r">{signed(x.error)}</td><td className="r muted">{signed(x.naive_error)}</td>
              </tr>))}
            </tbody>
          </table>
          <p className="muted small">“No change” is the forecast a spreadsheet makes by copying last month forward.</p>
        </details>

        {r.explanations && (
          <details className="card pad">
            <summary><b>Budget gaps explained by named causes</b></summary>
            <table className="table compact">
              <thead><tr><th>Close</th><th className="r">Revenue + payroll gap</th><th className="r">Explained</th><th className="r">Findings</th></tr></thead>
              <tbody>{r.explanations.months.map((m) => (
                <tr key={m.month}><td>{monthLabel(m.month)}</td><td className="r">{fmt(m.gap, "currency")}</td>
                  <td className="r">{pct(m.share, 0)}</td><td className="r">{m.findings}</td></tr>))}
              </tbody>
            </table>
          </details>
        )}

        {r.agreement && r.agreement.rows.length > 0 && (
          <details className="card pad">
            <summary><b>Billing vs the books</b></summary>
            <table className="table compact">
              <thead><tr><th>Month</th><th className="r">Revenue in books</th><th className="r">MRR from billing</th><th className="r">Difference</th></tr></thead>
              <tbody>{r.agreement.rows.map((m) => (
                <tr key={m.month}><td>{monthLabel(m.month)}</td><td className="r">{fmt(m.books, "currency")}</td>
                  <td className="r">{fmt(m.billing, "currency")}</td><td className="r">{signed(m.diff)}</td></tr>))}
              </tbody>
            </table>
          </details>
        )}

        {r.models.length > 0 && (
          <details className="card pad">
            <summary><b>Models, tested on data they never saw</b></summary>
            <table className="table compact">
              <thead><tr><th>Model</th><th className="r">Tested on</th><th className="r">Top 10% hit rate</th><th className="r">Overall rate</th><th className="r">AUC</th></tr></thead>
              <tbody>{r.models.map((m) => (
                <tr key={m.kind}><td>{m.label}</td><td className="r">{m.n_test}</td><td className="r">{pct(m.top_decile_rate, 0)}</td>
                  <td className="r">{pct(m.base_rate, 0)}</td><td className="r">{m.auc.toFixed(2)}</td></tr>))}
              </tbody>
            </table>
            <p className="muted small">Trained on older records, tested on the most recent ones, so the score reflects how the model does on cases it hasn&apos;t seen.</p>
          </details>
        )}
      </>
    );
  } catch (e) {
    return <PageError title="Track record" error={e} />;
  }
}
