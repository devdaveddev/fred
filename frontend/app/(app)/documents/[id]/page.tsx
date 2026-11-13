import Link from "next/link";
import { DocView } from "@/components/DocView";
import { PageError } from "@/components/PageError";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export default async function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  try {
    const d = await sapi.document(id);
    return (
      <>
        <p className="no-print"><Link href="/documents" className="muted">← Documents</Link></p>
        <DocView d={d} />
      </>
    );
  } catch (e) {
    return <PageError title="Document" error={e} />;
  }
}
