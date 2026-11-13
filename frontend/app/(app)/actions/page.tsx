import { PageError } from "@/components/PageError";
import { TaskList } from "@/components/TaskList";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Follow-ups · Fred" };

export default async function ActionsPage() {
  try {
    const { tasks, muted } = await sapi.tasks();
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Follow-ups</h1>
            <p>Every assigned item, who owns it, and whether the problem is actually getting smaller in the data.</p>
          </div>
        </div>
        <TaskList tasks={tasks} muted={muted} />
      </>
    );
  } catch (e) {
    return <PageError title="Follow-ups" error={e} />;
  }
}
