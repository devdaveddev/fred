import { type ForecastResponse } from "@/lib/api";
import { sapi } from "@/lib/server-api";
import { PageError } from "@/components/PageError";
import { Planner } from "@/components/Planner";

export const dynamic = "force-dynamic";
export const metadata = { title: "Plan & forecast · Fred" };

export default async function PlanPage() {
  let d: ForecastResponse;
  try {
    d = await sapi.forecast();
  } catch (e) {
    return <PageError title="Plan & forecast" error={e} />;
  }
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Plan & forecast</h1>
          <p>Where the business is heading, and what it takes to hit a goal: spend, retention, and whether the cash holds.</p>
        </div>
      </div>
      <Planner d={d} />
    </>
  );
}
