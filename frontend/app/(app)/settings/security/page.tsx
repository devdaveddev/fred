import { PageError } from "@/components/PageError";
import { SecuritySettings } from "@/components/SecuritySettings";
import { ApiError } from "@/lib/api";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Security · Fred" };

export default async function SecurityPage() {
  try {
    const [me, sso, s] = await Promise.all([sapi.me(), sapi.sso(), sapi.sessions()]);
    const audit = await sapi.auditLog().catch((e) => { if (e instanceof ApiError && e.status === 403) return null; throw e; });
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Security</h1>
            <p>Who can get in, how they sign in, and a record of everything sensitive that happened.</p>
          </div>
        </div>
        <SecuritySettings sso={sso} sessions={s.sessions} audit={audit} isOwner={me.user.role === "owner"} />
      </>
    );
  } catch (e) {
    return <PageError title="Security" error={e} />;
  }
}
