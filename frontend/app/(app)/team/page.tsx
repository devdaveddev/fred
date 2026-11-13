import { PageError } from "@/components/PageError";
import { TeamBoard } from "@/components/TeamBoard";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Your team · Fred" };

export default async function TeamPage({ searchParams }: { searchParams: Promise<{ welcome?: string }> }) {
  const { welcome } = await searchParams;
  try {
    const { roles } = await sapi.team();
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Your team</h1>
            <p>Add the roles you need. Each one is a set of jobs that runs on your data every day.</p>
          </div>
        </div>
        <TeamBoard initial={roles} welcome={welcome === "1"} />
      </>
    );
  } catch (e) {
    return <PageError title="Your team" error={e} />;
  }
}
