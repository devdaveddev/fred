import type { ModelCard } from "@/lib/api";
import { MethodBadge } from "./team";

export function ModelSummary({ m, what }: { m: ModelCard; what: string }) {
  const x = m.metrics;
  return (
    <div className="stats-strip">
      <span className="chip"><MethodBadge kind="ml" /> {what}</span>
      <span className="chip" title="Area under ROC curve on the held-out, most recent data">Holdout AUC <b>&nbsp;{x.auc.toFixed(2)}</b></span>
      <span className="chip" title="Top 10% of scores vs average">Top-decile lift <b>&nbsp;{x.top_decile_lift.toFixed(1)}x</b></span>
      <span className="chip" title="Average gap between predicted and observed probability">Calibration error <b>&nbsp;{(x.calibration_error * 100).toFixed(1)} pts</b></span>
      <span className="chip">Trained on {m.n_train.toLocaleString("en-US")} rows, tested on {m.n_test.toLocaleString("en-US")}</span>
      <span className="chip mono">{m.version}</span>
    </div>
  );
}
