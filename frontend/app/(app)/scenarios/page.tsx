import { ScenarioWorkbench } from "@/components/ScenarioWorkbench";

export const metadata = { title: "What-if · Fred" };

export default function ScenariosPage() {
  return (
    <>
      <div className="page-head">
        <div>
          <h1>What-if</h1>
          <p>Change price, churn, spend or CAC and see ARR, retention, unit economics and cash move together.</p>
        </div>
      </div>
      <ScenarioWorkbench currency="USD" />
    </>
  );
}
