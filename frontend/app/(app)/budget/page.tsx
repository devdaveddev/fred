import { BudgetView } from "@/components/BudgetView";
import { PageError } from "@/components/PageError";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Budget · Fred" };

export default async function BudgetPage() {
  try {
    const b = await sapi.budget();
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Budget</h1>
            <p>Each department plans its own spend and hires. Fred starts them from today&apos;s numbers, checks the plan, and once it&apos;s approved, measures every month against it.</p>
          </div>
        </div>
        <BudgetView data={b} />
      </>
    );
  } catch (e) {
    return <PageError title="Budget" error={e} />;
  }
}
