import { type MarketingResponse } from "@/lib/api";
import { sapi } from "@/lib/server-api";
import { PageError } from "@/components/PageError";
import { MethodBadge } from "@/components/team";
import { fmt } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Marketing · Fred" };

const LABEL: Record<string, string> = { paid_search: "Paid search", linkedin: "LinkedIn", events: "Events", content: "Content", organic: "Organic", referral: "Referral" };
const L = (c: string) => LABEL[c] ?? c;

export default async function MarketingPage() {
  let d: MarketingResponse;
  try {
    d = await sapi.marketing();
  } catch (e) {
    return <PageError title="Marketing" error={e} />;
  }
  const r = d.reallocation;
  const maxSpend = Math.max(...r.moves.map((m) => Math.max(m.current, m.proposed)), 1);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Marketing</h1>
          <p>Channel economics over the last 6 months, fitted spend response curves, and where the next dollar should go.</p>
        </div>
      </div>

      {d.anomalies.map((a) => (
        <div key={a.channel} className="banner error">
          <b>{L(a.channel)}:</b> daily spend {fmt(a.recent_daily, "currency")} vs a typical {fmt(a.typical_daily, "currency")} for {a.days} days
          since {a.since} ({fmt(a.excess_spend, "currency")} above normal{a.lead_ratio !== null ? `, leads at ${a.lead_ratio.toFixed(1)}x usual` : ""}).
        </div>
      ))}

      <div className="section-title">Channel economics <MethodBadge kind="statistics" /></div>
      <div className="card table-wrap">
        <table className="table">
          <thead>
            <tr>
              <th>Channel</th><th className="r">Media spend</th><th className="r">Leads</th><th className="r">Lead → customer</th>
              <th className="r">New customers</th><th className="r">CAC (fully loaded)</th><th className="r">Payback</th><th className="r">LTV:CAC</th>
            </tr>
          </thead>
          <tbody>
            {d.channels.map((c) => (
              <tr key={c.channel}>
                <td><b>{L(c.channel)}</b>{!c.paid && <span className="sub">no media spend</span>}</td>
                <td className="r num">{c.spend ? fmt(c.spend, "currency") : "n/a"}</td>
                <td className="r num">{c.leads.toLocaleString("en-US")}</td>
                <td className="r num">{fmt(c.conversion, "percent")}</td>
                <td className="r num">{c.new_customers}</td>
                <td className="r num">{fmt(c.cac_loaded, "currency")}</td>
                <td className="r num">{fmt(c.payback_months, "months")}</td>
                <td className="r num">{fmt(c.ltv_cac, "multiple")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="note">Fully loaded CAC allocates sales team cost to channels by customers won. Organic and referral carry only that share.</p>

      <div className="two" style={{ marginTop: 16 }}>
        <div className="card pad">
          <h3>Same budget, better mix <MethodBadge kind="optimization" /></h3>
          {r.moves.length ? (
            <>
              <p className="note">
                Monthly budget {fmt(r.budget, "currency")}. Predicted new customers {r.customers_before?.toFixed(1)} → {r.customers_after?.toFixed(1)} a month
                (+{r.delta_customers_monthly.toFixed(1)}, about {fmt(r.delta_customers_monthly * d.arpa_new * 12, "currency")} of new ARR every month).
              </p>
              {r.moves.map((m) => (
                <div key={m.channel} className="bench-row">
                  <span>
                    <b>{L(m.channel)}</b>
                    <span className="bar-h" style={{ width: `${(m.current / maxSpend) * 100}%`, opacity: 0.35, marginTop: 6 }} />
                    <span className="bar-h" style={{ width: `${(m.proposed / maxSpend) * 100}%`, marginTop: 3 }} />
                  </span>
                  <span className={`num delta ${m.change > 0 ? "up" : m.change < 0 ? "down" : "flat"}`}>
                    {m.change >= 0 ? "+" : "−"}{fmt(Math.abs(m.change), "currency")}
                    <span className="sub note">{fmt(m.current, "currency")} → {fmt(m.proposed, "currency")}</span>
                  </span>
                </div>
              ))}
              {r.frozen.length > 0 && <p className="note">Held fixed (curve fit too weak to trust): {r.frozen.map(L).join(", ")}.</p>}
            </>
          ) : <p className="muted">Not enough reliable channel history to recommend a reallocation.</p>}
        </div>
        <div className="card pad">
          <h3>Spend response curves <MethodBadge kind="statistics" /></h3>
          <p className="note">leads = e<sup>a</sup> × spend<sup>b</sup>, fitted monthly. b below 1 means each extra dollar buys fewer leads.</p>
          {Object.entries(d.curves).map(([ch, c]) => (
            <div key={ch} className="bench-row">
              <span>
                <b>{L(ch)}</b>
                <span className="sub note">fit R² {c.r2.toFixed(2)} over {c.n_months} months · converts {fmt(c.conversion, "percent")}</span>
              </span>
              <span className="num">b = {c.b.toFixed(2)}<span className="sub note">{c.r2 < 0.3 ? "unreliable" : c.b < 0.6 ? "saturating" : "still scaling"}</span></span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
