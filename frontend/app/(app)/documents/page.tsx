import { DocumentsHome } from "@/components/DocumentsHome";
import { PageError } from "@/components/PageError";
import { sapi } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata = { title: "Documents · Fred" };

export default async function DocumentsPage() {
  try {
    const { documents, can_create } = await sapi.documents();
    return (
      <>
        <div className="page-head">
          <div>
            <h1>Documents</h1>
            <p>Finished work, ready to send. Every number in every document is checked against the data it came from.</p>
          </div>
        </div>
        <DocumentsHome docs={documents} canCreate={can_create} />
      </>
    );
  } catch (e) {
    return <PageError title="Documents" error={e} />;
  }
}
