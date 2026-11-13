import { PageError } from "@/components/PageError";
import { People } from "@/components/People";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "People · Fred" };

export default async function PeoplePage() {
  try {
    const m = await sapi.members();
    return (
      <>
        <div className="page-head">
          <div>
            <h1>People</h1>
            <p>Everyone who works with Fred. Tasks can be assigned to them, and their own follow-ups come first in their digest.</p>
          </div>
        </div>
        <People initial={m} />
      </>
    );
  } catch (e) {
    return <PageError title="People" error={e} />;
  }
}
