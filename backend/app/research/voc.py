"""Business analyst: voice of the customer. Why customers leave, and what unhappy ones say.

Zero-shot tagging with a pre-trained embedding model: each comment gets the theme whose
plain-English description it is most similar to. No labelled data, no LLM, fully editable themes.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

THEMES_FILE = Path(__file__).parent.parent / "knowledge" / "themes.yaml"


@lru_cache
def themes() -> dict:
    return yaml.safe_load(THEMES_FILE.read_text())


def tag(texts: list[str], embed) -> tuple[list[str], np.ndarray]:
    t = themes()
    protos, owner = [], []
    for th in t["themes"]:
        for p in th["prototypes"]:
            protos.append(p)
            owner.append(th["id"])
    P = embed(protos)
    E = embed(texts)
    S = E @ P.T
    owner_arr = np.array(owner)
    ids = [th["id"] for th in t["themes"]]
    per_theme = np.stack([S[:, owner_arr == i].max(axis=1) for i in ids], axis=1)
    best = per_theme.argmax(axis=1)
    conf = per_theme.max(axis=1)
    labels = [ids[b] if c >= t["min_similarity"] else "other" for b, c in zip(best, conf)]
    return labels, conf


def analyse(feedback: pd.DataFrame, mrr: pd.DataFrame, as_of: pd.Timestamp, embed) -> dict:
    meta = {th["id"]: th for th in themes()["themes"]}
    fb = feedback.copy()
    if fb.empty:
        return {"churn_reasons": [], "detractor_themes": [], "nps": None, "tagged": 0}
    fb["theme"], fb["confidence"] = tag(fb["body"].tolist(), embed)

    # churn reasons, weighted by the MRR each leaver took with them
    ex = fb[fb["kind"] == "exit_survey"].copy()
    last_mrr = mrr.sort_values("month").groupby("account_id")["mrr"].last()
    ex["lost_mrr"] = ex["account_id"].map(last_mrr).fillna(0.0)
    reasons = []
    total_mrr = float(ex["lost_mrr"].sum()) or 1.0
    for th, grp in ex.groupby("theme"):
        reasons.append({"theme": th, "label": meta.get(th, {}).get("label", "Other"),
                        "action": meta.get(th, {}).get("action", ""), "responses": len(grp),
                        "share": float(len(grp) / len(ex)), "lost_mrr": float(grp["lost_mrr"].sum()),
                        "mrr_share": float(grp["lost_mrr"].sum() / total_mrr),
                        "quotes": grp.sort_values("confidence", ascending=False)["body"].head(2).tolist()})
    reasons.sort(key=lambda r: -r["lost_mrr"])

    # NPS over the last 6 months, and what detractors talk about
    recent = fb[(fb["kind"] == "nps") & (fb["created_at"] >= as_of - pd.DateOffset(months=5))]
    nps = None
    detractor_themes = []
    if len(recent) >= 20:
        promoters = (recent["score"] >= 9).mean()
        detractors = (recent["score"] <= 6).mean()
        nps = {"score": float((promoters - detractors) * 100), "responses": len(recent),
               "promoters": float(promoters), "detractors": float(detractors)}
        det = recent[recent["score"] <= 6]
        for th, grp in det.groupby("theme"):
            if th == "praise":
                continue
            detractor_themes.append({"theme": th, "label": meta.get(th, {}).get("label", "Other"),
                                     "count": len(grp), "share": float(len(grp) / max(1, len(det))),
                                     "quotes": grp["body"].head(2).tolist()})
        detractor_themes.sort(key=lambda r: -r["count"])
    return {"churn_reasons": reasons, "detractor_themes": detractor_themes, "nps": nps, "tagged": len(fb),
            "exit_responses": len(ex), "themes_version": themes()["version"]}
