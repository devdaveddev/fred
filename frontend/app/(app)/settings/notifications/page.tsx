import { NotificationSettings } from "@/components/NotificationSettings";
import { PageError } from "@/components/PageError";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Notifications · Fred" };

export default async function NotificationsPage() {
  try {
    const n = await sapi.notifications();
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Notifications</h1>
            <p>Every morning Fred refreshes your data and tells the team what changed. If nothing changed, it stays quiet.</p>
          </div>
        </div>
        <NotificationSettings initial={n} />
      </>
    );
  } catch (e) {
    return <PageError title="Notifications" error={e} />;
  }
}
