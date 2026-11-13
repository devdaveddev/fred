import { type AtRiskResponse } from "@/lib/api";
import { sapi } from "@/lib/server-api";
import { PageError } from "@/components/PageError";
import { ModelSummary } from "@/components/ModelSummary";
import { ProbBar } from "@/components/team";
import { fmt } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "At-risk accounts · Fred" };

export default async function AccountsPage() {
  let d: AtRiskResponse;
  try {
    d = await sapi.atRisk(60);
  } catch (e) {
    return <PageError title="At-risk accounts" error={e} />;
  }
  const t = d.totals;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>At-risk accounts</h1>
          <p>Accounts ranked by expected MRR loss in the next 3 months, with the usage signals behind each score and a play to run.</p>
        </div>
      </div>
      <ModelSummary m={d.model} what="Churn model (3-month horizon)" />
      <div className="kpis">
        <div className="card kpi"><span>Accounts scored</span><b className="num">{t.accounts}</b></div>
        <div className="card kpi"><span>High risk (25%+)</span><b className="num">{t.high_risk}</b></div>
        <div className="card kpi"><span>Expected MRR loss</span><b className="num">{fmt(t.mrr_at_risk, "currency")}</b></div>
        <div className="card kpi"><span>Share of MRR</span><b className="num">{fmt(t.mrr_at_risk / t.mrr, "percent")}</b></div>
      </div>
      <div className="card table-wrap">
        <table className="table">
          <thead>
            <tr><th>Account</th><th>Churn odds</th><th className="r">MRR at risk</th><th>Why</th><th>Play</th></tr>
          </thead>
          <tbody>
            {d.accounts.map((a) => (
              <tr key={a.account_id}>
                <td>
                  <b>{a.name}</b>
                  <span className="sub">{a.segment} · {a.industry} · {a.tenure_months} months</span>
                </td>
                <td><ProbBar p={a.p} /></td>
                <td className="r num">{fmt(a.mrr_at_risk, "currency")}<span className="sub">of {fmt(a.mrr, "currency")}</span></td>
                <td>{a.reasons.length ? <ul className="reasons risk">{a.reasons.map((r) => <li key={r}>{r}</li>)}</ul> : <span className="muted">No single driver; broad risk</span>}</td>
                <td style={{ maxWidth: 260 }}>{a.play}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
