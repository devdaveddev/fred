import { Nav } from "@/components/Nav";
import { ReceiptProvider } from "@/components/ReceiptDrawer";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await sapi.me();
  return (
    <ReceiptProvider>
      <div className="shell">
        <Nav me={me} />
        <main className="main">
          {["demo", "preparing"].includes(me.workspace.data_source) && (
            <div className="demo-bar">
              <span>You&apos;re exploring a <b>demo company</b>. Every number here is made up.</span>
              <a href="/settings/data#upload">Use your own data →</a>
            </div>
          )}
          {children}
        </main>
      </div>
    </ReceiptProvider>
  );
}
