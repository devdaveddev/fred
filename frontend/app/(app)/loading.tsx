// Shown instantly when you click a link, while the next page is prepared on the server.
export default function Loading() {
  return (
    <div className="page-loading" role="status" aria-live="polite">
      <div className="page-loading-bar" />
      <div className="skeleton h1" />
      <div className="skeleton p" />
      <div className="skeleton-grid"><div className="skeleton card" /><div className="skeleton card" /><div className="skeleton card" /></div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}
