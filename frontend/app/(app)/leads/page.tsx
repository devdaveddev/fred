import { type LeadsResponse } from "@/lib/api";
import { sapi } from "@/lib/server-api";
import { PageError } from "@/components/PageError";
import { LeadTable } from "@/components/LeadTable";
import { ModelSummary } from "@/components/ModelSummary";
import { fmt } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Leads · Fred" };

export default async function LeadsPage() {
  let d: LeadsResponse;
  try {
    d = await sapi.leads(300);
  } catch (e) {
    return <PageError title="Leads" error={e} />;
  }
  const best = d.icp.filter((r) => r.significant && r.lift > 1).slice(0, 6);
  const worst = d.icp.filter((r) => r.significant && r.lift < 1).sort((a, b) => a.lift - b.lift).slice(0, 4);
  const maxLift = Math.max(...d.icp.map((r) => r.lift), 1);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Leads</h1>
          <p>{d.total_open.toLocaleString("en-US")} open leads scored, explained and routed. Highest expected value first.</p>
        </div>
      </div>
      <ModelSummary m={d.model} what="Lead conversion model" />
      <LeadTable leads={d.leads} counts={d.lanes} />

      <div className="section-title">Ideal customer profile, learned from your closed leads</div>
      <div className="two">
        <div className="card pad">
          <h3>Converts above average</h3>
          {best.map((r) => (
            <div key={r.dimension + r.value} className="bench-row">
              <span>
                <b>{r.value.replace("_", " ")}</b> <span className="note">{r.dimension.replace("_", " ")}</span>
                <span className="bar-h" style={{ width: `${(r.lift / maxLift) * 100}%`, marginTop: 6 }} />
              </span>
              <span className="num">{r.lift.toFixed(1)}x<span className="sub note">{fmt(r.conversion, "percent")} of {r.leads}</span></span>
            </div>
          ))}
        </div>
        <div className="card pad">
          <h3>Converts below average</h3>
          {worst.map((r) => (
            <div key={r.dimension + r.value} className="bench-row">
              <span>
                <b>{r.value.replace("_", " ")}</b> <span className="note">{r.dimension.replace("_", " ")}</span>
                <span className="bar-h neg" style={{ width: `${(r.lift / maxLift) * 100}%`, marginTop: 6 }} />
              </span>
              <span className="num">{r.lift.toFixed(1)}x<span className="sub note">{fmt(r.conversion, "percent")} of {r.leads}</span></span>
            </div>
          ))}
          <p className="note">Only segments with at least 60 closed leads and a significant difference (|z| ≥ 2) are shown.</p>
        </div>
      </div>
      <p className="note">
        Routing: fast lane at {fmt(d.thresholds.fast_lane, "percent")}+ calibrated odds (or a demo request at {fmt(d.thresholds.fast_lane * 0.75, "percent")}+),
        SDR at {fmt(d.thresholds.sdr, "percent")}+. Thresholds live in the knowledge base so you can tune them to sales capacity.
      </p>
    </>
  );
}
