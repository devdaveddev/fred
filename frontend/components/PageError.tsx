import Link from "next/link";
import { unstable_rethrow } from "next/navigation";
import { ApiError } from "@/lib/api";
import { AutoRefresh } from "./AutoRefresh";

export function PageError({ title, error }: { title: string; error: unknown }) {
  unstable_rethrow(error); // let auth redirects through
  const status = error instanceof ApiError ? error.status : 0;
  const msg = error instanceof ApiError ? error.message : "The service is not reachable right now. Try again in a moment.";
  return (
    <>
      <div className="page-head"><div><h1>{title}</h1></div></div>
      {status === 403 ? (
        <div className="empty">
          <div className="empty-icon">◌</div>
          <h3>Nobody is doing this job yet</h3>
          <p>{msg}</p>
          <Link href="/team" className="btn primary">Go to your team</Link>
        </div>
      ) : status === 409 && msg.startsWith("Your demo company is being set up") ? (
        <div className="empty">
          <AutoRefresh />
          <div className="empty-icon spin">◌</div>
          <h3>Setting up your demo company</h3>
          <p>{msg}</p>
        </div>
      ) : status === 409 ? (
        <div className="empty">
          <div className="empty-icon">◌</div>
          <h3>Waiting for data</h3>
          <p>{msg}</p>
          <Link href="/settings/data" className="btn primary">Go to data sources</Link>
        </div>
      ) : (
        <div className="banner error"><strong>Can&apos;t load this page.</strong> {msg}</div>
      )}
    </>
  );
}
