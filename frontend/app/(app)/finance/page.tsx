import { type Bva } from "@/lib/api";
import { sapi } from "@/lib/server-api";
import { PageError } from "@/components/PageError";
import { FinanceView } from "@/components/FinanceView";
import { monthLabel } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Budget vs actual · Fred" };

export default async function FinancePage({ searchParams }: { searchParams: Promise<{ month?: string }> }) {
  const { month } = await searchParams;
  let d: Bva;
  try {
    d = await sapi.bva(month && /^\d{4}-\d{2}$/.test(month) ? month : undefined);
  } catch (e) {
    return <PageError title="Budget vs actual" error={e} />;
  }
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Budget vs actual</h1>
          <p>
            {monthLabel(d.month)} close against the {d.plan.name}: what moved, why, and whether it is one-off or here to stay.
          </p>
        </div>
        <span className="chip mono">{d.snapshot_id}</span>
      </div>
      <FinanceView d={d} />
    </>
  );
}
