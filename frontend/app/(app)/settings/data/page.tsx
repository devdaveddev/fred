import { DataSources } from "@/components/DataSources";
import { PageError } from "@/components/PageError";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Data sources · Fred" };

export default async function DataPage() {
  try {
    const src = await sapi.sources();
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Data sources</h1>
            <p>Connect your tools or upload exports. Every role starts working as soon as it has the data it needs.</p>
          </div>
        </div>
        <DataSources initial={src} />
      </>
    );
  } catch (e) {
    return <PageError title="Data sources" error={e} />;
  }
}
