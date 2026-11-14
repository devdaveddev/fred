"""Market researcher: discover customer segments from how companies describe themselves.

Pipeline (no labels, no LLM):
  1. embed every customer's company description with a pre-trained model
  2. k-means, with k chosen by silhouette score
  3. name each segment by its most distinctive words (class-based TF-IDF)
  4. join segment membership to revenue, retention and lead conversion
  5. find open leads that look like the best segment (lookalikes)
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer
from sklearn.metrics import silhouette_score

EXTRA_STOP = {"company", "business", "tiny", "small", "growing", "large", "mainly", "provides", "runs", "builds",
              "offers", "makes", "sells", "operates", "online", "platform", "software", "saas", "fintech",
              "ecommerce", "healthcare", "media", "manufacturing", "education"}


def _clean(desc: str, name: str) -> str:
    t = desc.replace(name, " ")
    return re.sub(r"\d+", " ", t)


def _labels(texts: list[str], assign: np.ndarray, k: int) -> dict[int, str]:
    vec = CountVectorizer(stop_words=list(ENGLISH_STOP_WORDS | EXTRA_STOP), ngram_range=(1, 2), min_df=3)
    X = vec.fit_transform(texts)
    terms = np.array(vec.get_feature_names_out())
    tf = np.vstack([np.asarray(X[assign == c].sum(axis=0)).ravel() for c in range(k)]).astype(float)
    tf = tf / np.maximum(tf.sum(axis=1, keepdims=True), 1)
    idf = np.log(1 + tf.mean() / np.maximum(tf.mean(axis=0), 1e-12))
    score = tf * idf
    out = {}
    for c in range(k):
        top = [terms[i] for i in np.argsort(-score[c])[:6]]
        picked: list[str] = []
        for t in top:  # prefer bigrams; drop unigrams already inside a chosen bigram
            if any(t in p or p in t for p in picked):
                continue
            picked.append(t)
            if len(picked) == 2:
                break
        out[c] = " / ".join(picked)
    return out


MAX_CLOSED = 2500
MAX_OPEN = 3000


def discover(accounts: pd.DataFrame, mrr: pd.DataFrame, leads: pd.DataFrame, as_of: pd.Timestamp, embed,
             k_range=range(6, 15)) -> dict:
    acc = accounts.dropna(subset=["description"]).reset_index(drop=True)
    texts = [_clean(d, n) for d, n in zip(acc["description"], acc["name"])]
    E = embed(texts)
    best = None
    for k in k_range:
        km = KMeans(n_clusters=k, n_init=5, random_state=0).fit(E)
        s = silhouette_score(E, km.labels_, sample_size=min(2000, len(E)), random_state=0)
        if best is None or s > best[0]:
            best = (s, k, km)
    sil, k, km = best
    acc["segment_id"] = km.labels_
    names = _labels(texts, km.labels_, k)

    # economics per segment
    now = mrr[mrr["month"] == as_of].set_index("account_id")["mrr"]
    yr = mrr[mrr["month"] == as_of - pd.DateOffset(months=12)].set_index("account_id")["mrr"]
    leads = leads[(leads["status"] == "open") | (leads["created_at"] >= as_of - pd.DateOffset(months=12))]
    # Conversion rates per segment don't need every closed lead: the most recent ones are plenty and keep
    # the embedding cost flat as the CRM grows. Open leads are all kept (they're the lookalike candidates).
    open_l = leads[leads["status"] == "open"].sort_values("created_at").tail(MAX_OPEN)
    closed_l = leads[leads["status"] != "open"].sort_values("created_at").tail(MAX_CLOSED)
    leads = pd.concat([closed_l, open_l]).reset_index(drop=True)
    lead_texts = [_clean(d, n) for d, n in zip(leads["description"].fillna(""), leads["company"])]
    LE = embed(lead_texts)
    lead_seg = np.argmax(LE @ km.cluster_centers_.T, axis=1)
    leads = leads.assign(segment_id=lead_seg)
    closed = leads[leads["status"] != "open"]
    base_conv = float((closed["status"] == "converted").mean())

    segs = []
    for c in range(k):
        ids = acc.loc[acc["segment_id"] == c, "account_id"]
        cohort = yr.index.intersection(ids)
        kept = now.index.intersection(cohort)
        cl = closed[closed["segment_id"] == c]
        active = now.index.intersection(ids)
        segs.append({
            "segment_id": int(c), "name": names[c], "customers": len(active),
            "mrr": float(now.loc[active].sum()), "avg_mrr": float(now.loc[active].mean()) if len(active) else 0.0,
            "retention_12m": float(len(kept) / len(cohort)) if len(cohort) >= 10 else None,
            "nrr_12m": float(now.loc[kept].sum() / yr.loc[cohort].sum()) if len(cohort) >= 10 else None,
            "lead_conversion": float((cl["status"] == "converted").mean()) if len(cl) >= 40 else None,
            "examples": acc.loc[acc["segment_id"] == c, "description"].head(3).tolist(),
            "industries": acc.loc[acc["segment_id"] == c, "industry"].value_counts().head(2).index.tolist(),
        })
    total_mrr = sum(s["mrr"] for s in segs) or 1.0
    scored = [s for s in segs if s["nrr_12m"] is not None and s["customers"] >= 15]
    for s in segs:
        s["mrr_share"] = s["mrr"] / total_mrr
    # attractiveness = how much revenue a cohort keeps (NRR) x how big each customer is, relative to average
    avg_nrr = np.mean([s["nrr_12m"] for s in scored]) if scored else 1.0
    avg_arpa = np.mean([s["avg_mrr"] for s in scored]) if scored else 1.0
    for s in segs:
        s["score"] = (s["nrr_12m"] / avg_nrr) * (s["avg_mrr"] / avg_arpa) if s in scored else None
    ranked = sorted(scored, key=lambda s: -s["score"])
    best_seg, worst_seg = (ranked[0], ranked[-1]) if ranked else (None, None)

    lookalikes = []
    if best_seg is not None:
        open_idx = np.where(leads["status"].to_numpy() == "open")[0]
        sims = LE[open_idx] @ km.cluster_centers_[best_seg["segment_id"]]
        order = open_idx[np.argsort(-sims)][:15]
        for i in order:
            r = leads.iloc[i]
            lookalikes.append({"lead_id": r["lead_id"], "company": r["company"], "description": r["description"],
                               "similarity": float(LE[i] @ km.cluster_centers_[best_seg["segment_id"]]
                                                   / np.linalg.norm(km.cluster_centers_[best_seg["segment_id"]]))})
    return {"k": int(k), "silhouette": float(sil), "segments": sorted(segs, key=lambda s: -s["mrr"]),
            "best": best_seg, "worst": worst_seg, "lookalikes": lookalikes, "base_conversion": base_conv,
            "assignments": dict(zip(acc["account_id"], acc["segment_id"].astype(int)))}
