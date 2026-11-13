import type { Market, Segment } from "@/lib/api";
import { PageError } from "@/components/PageError";
import { sapi } from "@/lib/server-api";
import { fmt } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Market segments · Fred" };

const ACRONYMS = new Set(["api", "hr", "iot", "d2c", "b2b", "ci", "cd", "crm", "erp", "k-12", "ai"]);
const word = (w: string) => (ACRONYMS.has(w.toLowerCase()) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1));
const title = (s: string) => s.split(" / ").map((p) => p.split(" ").map(word).join(" ")).join(" · ");

function verdict(s: Segment, avgArpa: number): [string, string] {
  if (s.nrr_12m === null) return ["Too new to judge", "neutral-pill"];
  if (s.nrr_12m >= 1 && s.avg_mrr >= avgArpa) return ["Double down", "good"];
  if (s.nrr_12m < 0.75) return ["Stop chasing", "bad"];
  if (s.nrr_12m >= 0.9) return ["Healthy", "good"];
  return ["Watch", "watch"];
}

export default async function MarketPage() {
  let d: Market;
  try {
    d = await sapi.market();
  } catch (e) {
    return <PageError title="Market segments" error={e} />;
  }
  const avgArpa = d.segments.reduce((a, s) => a + s.avg_mrr * s.customers, 0) / Math.max(1, d.segments.reduce((a, s) => a + s.customers, 0));
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Market segments</h1>
          <p>Your market researcher grouped every customer by how they describe their own business, then checked which groups stay, grow and pay.</p>
        </div>
      </div>

      {d.best && d.worst && (
        <div className="two">
          <div className="card callout good-callout">
            <div className="eyebrow">Win more of</div>
            <h2>{title(d.best.name)}</h2>
            <p>They keep {fmt(d.best.nrr_12m, "percent")} of their revenue after a year and pay {(d.best.avg_mrr / avgArpa).toFixed(1)}x your average.</p>
            <p className="muted small">e.g. &ldquo;{d.best.examples[0]}&rdquo;</p>
          </div>
          <div className="card callout bad-callout">
            <div className="eyebrow">Stop paying to acquire</div>
            <h2>{title(d.worst.name)}</h2>
            <p>They keep only {fmt(d.worst.nrr_12m, "percent")} of their revenue after a year.</p>
            <p className="muted small">e.g. &ldquo;{d.worst.examples[0]}&rdquo;</p>
          </div>
        </div>
      )}

      <div className="eyebrow" style={{ marginTop: 28 }}>All segments, largest first</div>
      <div className="seg-grid">
        {d.segments.map((s) => {
          const [label, cls] = verdict(s, avgArpa);
          return (
            <div key={s.segment_id} className="card seg">
              <div className="seg-head"><b>{title(s.name)}</b><span className={`pill ${cls}`}>{label}</span></div>
              <p className="muted small">{s.examples[0]}</p>
              <div className="seg-stats">
                <span><b>{s.customers}</b> customers</span>
                <span><b>{fmt(s.mrr_share, "percent")}</b> of revenue</span>
                {s.nrr_12m !== null && <span><b>{fmt(s.nrr_12m, "percent")}</b> kept after 1 yr</span>}
              </div>
            </div>
          );
        })}
      </div>

      {d.lookalikes.length > 0 && d.best && (
        <>
          <div className="eyebrow" style={{ marginTop: 28 }}>Open leads that look like your best segment</div>
          <ul className="card plain-list">
            {d.lookalikes.slice(0, 8).map((l) => (
              <li key={l.lead_id}><b>{l.company}</b><span className="muted"> {l.description.replace(l.company, "").trim()}</span></li>
            ))}
          </ul>
        </>
      )}

      <details className="card how">
        <summary>How this was found</summary>
        <p>
          Each company description was turned into a vector by <b>{d.model.name}</b>, an open pre-trained language model ({d.model.license},{" "}
          {d.model.size_mb} MB, {d.model.runtime}). Customers were grouped with k-means; the number of groups ({d.k}) was picked by silhouette
          score ({d.silhouette.toFixed(2)}). Each group is named by the words that set it apart from the others. Nobody told the model what the segments were.
        </p>
      </details>
    </>
  );
}
