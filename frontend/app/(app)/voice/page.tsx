import type { Voice } from "@/lib/api";
import { PageError } from "@/components/PageError";
import { sapi } from "@/lib/server-api";
import { fmt } from "@/lib/format";

export const dynamic = "force-dynamic";
export const metadata = { title: "Voice of customer · Fred" };

export default async function VoicePage() {
  let d: Voice;
  try {
    d = await sapi.voice();
  } catch (e) {
    return <PageError title="Voice of customer" error={e} />;
  }
  const top = d.churn_reasons[0];
  const max = Math.max(...d.churn_reasons.map((r) => r.mrr_share), 0.01);
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Voice of customer</h1>
          <p>Your business analyst read every exit survey and NPS comment and sorted them by what customers are actually saying.</p>
        </div>
      </div>

      {top && (
        <div className="card callout bad-callout">
          <div className="eyebrow">Why customers leave</div>
          <h2>{top.label}</h2>
          <p>Behind {fmt(top.mrr_share, "percent")} of the revenue you lost to churn. {top.action}.</p>
          <blockquote>&ldquo;{top.quotes[0]}&rdquo;</blockquote>
        </div>
      )}

      <div className="two" style={{ marginTop: 16 }}>
        <div className="card pad">
          <h3>Every reason, by revenue lost</h3>
          {d.churn_reasons.map((r) => (
            <div key={r.theme} className="reason">
              <div className="reason-head"><b>{r.label}</b><span className="muted small">{fmt(r.mrr_share, "percent")}</span></div>
              <span className="bar-h" style={{ width: `${(r.mrr_share / max) * 100}%` }} />
              {r.quotes[0] && <span className="muted small">&ldquo;{r.quotes[0]}&rdquo;</span>}
            </div>
          ))}
          <p className="note">From {d.exit_responses} exit surveys.</p>
        </div>
        <div className="card pad">
          <h3>How customers feel now</h3>
          {d.nps ? (
            <>
              <div className="big-number">{d.nps.score > 0 ? "+" : ""}{d.nps.score.toFixed(0)}</div>
              <p className="muted">Net Promoter Score over the last 6 months, from {d.nps.responses} responses.</p>
              <div className="eyebrow" style={{ marginTop: 16 }}>What unhappy customers mention</div>
              {d.detractor_themes.slice(0, 4).map((t) => (
                <div key={t.theme} className="reason">
                  <div className="reason-head"><b>{t.label}</b><span className="muted small">{fmt(t.share, "percent")}</span></div>
                  {t.quotes[0] && <span className="muted small">&ldquo;{t.quotes[0]}&rdquo;</span>}
                </div>
              ))}
            </>
          ) : <p className="muted">Not enough recent NPS responses yet.</p>}
        </div>
      </div>

      <details className="card how">
        <summary>How this was found</summary>
        <p>
          Each comment was compared, using the open pre-trained model <b>{d.model.name}</b>, with short plain-English descriptions of each theme
          (themes v{d.themes_version}). The closest theme wins; comments that match nothing well are marked &ldquo;Other&rdquo;. No labelled
          training data, no chatbot. Themes are editable, so you can add one by describing it in your customers&apos; words.
        </p>
      </details>
    </>
  );
}
