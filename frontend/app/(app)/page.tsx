import Link from "next/link";
import type { Briefing } from "@/lib/api";
import { ApiError } from "@/lib/api";
import { PageError } from "@/components/PageError";
import { Today } from "@/components/Today";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export default async function TodayPage() {
  const me = await sapi.me();
  if (me.hired.length === 0) {
    return (
      <div className="empty">
        <div className="empty-icon">◌</div>
        <h3>Your team is empty</h3>
        <p>Add a business analyst, market researcher or any other role, and they will start on your data right away.</p>
        <Link href="/team" className="btn primary">Build your team</Link>
      </div>
    );
  }
  let b: Briefing;
  try {
    b = await sapi.briefing();
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) return <PageError title="Today" error={e} />;
    return <PageError title="Today" error={e} />;
  }
  return <Today b={b} name={me.user.name} />;
}
