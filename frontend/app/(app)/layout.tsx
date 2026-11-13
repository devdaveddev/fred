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
        <main className="main">{children}</main>
      </div>
    </ReceiptProvider>
  );
}
