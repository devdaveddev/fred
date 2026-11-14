import { CrewView } from "@/components/CrewView";
import { PageError } from "@/components/PageError";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Crew · Fred" };

export default async function CrewPage() {
  try {
    const c = await sapi.crew();
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Your crew</h1>
            <p>Each role is an agent. Every morning they turn what they find into owned follow-ups, remind people before and after due dates, and send each owner a weekly note.</p>
          </div>
        </div>
        <CrewView initial={c} />
      </>
    );
  } catch (e) {
    return <PageError title="Your crew" error={e} />;
  }
}
