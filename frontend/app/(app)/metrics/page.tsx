import { type Metric, type Overview } from "@/lib/api";
import { sapi } from "@/lib/server-api";
import { MetricCard } from "@/components/MetricCard";
import { MrrChart } from "@/components/MrrChart";
import { monthLabel } from "@/lib/format";
import Link from "next/link";
import { PageError } from "@/components/PageError";

export const dynamic = "force-dynamic";

const GROUPS = ["Growth", "Retention", "Unit economics", "Efficiency", "Cash"];

export default async function MetricsPage() {
  let data: Overview;
  try {
    data = await sapi.overview();
  } catch (e) {
    return <PageError title="Metrics" error={e} />;
  }

  const byGroup = (g: string) => data.metrics.filter((m: Metric) => m.group === g);
  const atRisk = data.metrics.filter((m) => m.status === "bad");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{data.tenant.name}</h1>
          <p>
            Revenue health as of {monthLabel(data.as_of)}.{" "}
            {atRisk.length === 1 ? "1 metric needs attention." : atRisk.length ? `${atRisk.length} metrics need attention.` : "Nothing at risk."}
          </p>
        </div>
        <div className="row">
          <span className="chip mono" title="Input snapshot every number on this page was computed from">{data.snapshot_id}</span>
          <Link href="/scenarios" className="btn primary">Run a what-if →</Link>
        </div>
      </div>

      {data.warnings.length > 0 && (
        <div className="banner">
          <ul>{data.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}

      <MrrChart points={data.mrr_series} currency={data.tenant.currency} />

      {GROUPS.map((g) => {
        const ms = byGroup(g);
        if (!ms.length) return null;
        return (
          <section key={g}>
            <div className="section-title">{g}</div>
            <div className="grid">
              {ms.map((m) => <MetricCard key={m.id} m={m} currency={data.tenant.currency} />)}
            </div>
          </section>
        );
      })}
    </>
  );
}
