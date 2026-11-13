"""Lead scoring: P(lead becomes a customer), with reason codes and routing.

Replaces the lead researcher / SDR triage step: every open lead gets a calibrated probability, the
three facts that drive it, an expected value and a next action. Pure ML + rules, zero LLM calls.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .common import ALGORITHM, ModelCard, contributions, fit_time_split, version_of

INDUSTRIES = ["fintech", "saas", "ecommerce", "healthcare", "media", "manufacturing", "education"]
SENIORITY = ["ic", "manager", "director", "exec"]
CHANNELS = ["paid_search", "linkedin", "events", "content", "organic", "referral"]

GROUPS = {
    "demo": ["demo_requested"],
    "pricing": ["pricing_views"],
    "docs": ["docs_views"],
    "email": ["email_engagement"],
    "webinar": ["webinar"],
    "size": ["log_employees"],
    "industry": [f"ind_{i}" for i in INDUSTRIES],
    "seniority": [f"sen_{s}" for s in SENIORITY],
    "channel": [f"ch_{c}" for c in CHANNELS],
}
FEATURES = [c for cols in GROUPS.values() for c in cols]


def features(leads: pd.DataFrame) -> pd.DataFrame:
    X = pd.DataFrame(index=leads.index)
    X["demo_requested"] = leads["demo_requested"].astype(int)
    X["pricing_views"] = leads["pricing_views"].clip(upper=10)
    X["docs_views"] = leads["docs_views"].clip(upper=15)
    X["email_engagement"] = leads["email_engagement"]
    X["webinar"] = leads["webinar"].astype(int)
    X["log_employees"] = np.log1p(leads["employees"].fillna(0))
    for i in INDUSTRIES:
        X[f"ind_{i}"] = (leads["industry"] == i).astype(int)
    for s in SENIORITY:
        X[f"sen_{s}"] = (leads["seniority"] == s).astype(int)
    for c in CHANNELS:
        X[f"ch_{c}"] = (leads["source_channel"] == c).astype(int)
    return X[FEATURES]


@dataclass
class LeadModel:
    model: object
    card: ModelCard
    reference: pd.Series
    segment_rates: dict[str, dict[str, float]]


def _rates(closed: pd.DataFrame, col: str) -> dict[str, float]:
    return closed.groupby(col)["y"].mean().to_dict()


def train(leads: pd.DataFrame, snapshot_id: str) -> LeadModel:
    closed = leads[leads["status"] != "open"].copy()
    closed["y"] = (closed["status"] == "converted").astype(int)
    X, y = features(closed), closed["y"].to_numpy()
    model, metrics, n_tr, n_te = fit_time_split(X, y, closed["created_at"])
    ref = X.median()
    for cols in (GROUPS["industry"], GROUPS["seniority"], GROUPS["channel"]):
        ref[cols] = 0  # one-hot reference = "none of these": compare against the average lead
    for cols in (GROUPS["industry"], GROUPS["seniority"], GROUPS["channel"]):
        mode = X[cols].sum().idxmax()
        ref[mode] = 1
    card = ModelCard(kind="lead_score", version=version_of("lead_score", snapshot_id, FEATURES), algorithm=ALGORITHM,
                     snapshot_id=snapshot_id, features=FEATURES, n_train=n_tr, n_test=n_te, metrics=metrics,
                     notes=["Label: lead converted to a paying customer.",
                            "Holdout: most recent 25% of closed leads by creation date (no look-ahead).",
                            "Open leads (outcome unknown) are never used for training."])
    closed["size_band"] = closed["employees"].map(_size_band)
    seg = {c: _rates(closed, c) for c in ("industry", "seniority", "source_channel", "size_band")}
    return LeadModel(model, card, ref, seg)


def _size_band(emp: float) -> str:
    return "under 20" if emp < 20 else ("20-49" if emp < 50 else ("50-1,000" if emp <= 1000 else "1,000+"))


def reason_text(group: str, row: pd.Series, lm: LeadModel, base: float) -> str:
    seg = lm.segment_rates
    if group == "demo":
        return "Requested a demo" if row.demo_requested else "Has not requested a demo"
    if group == "pricing":
        return f"Viewed pricing {int(row.pricing_views)} time{'s' if row.pricing_views != 1 else ''}"
    if group == "docs":
        return f"Read {int(row.docs_views)} docs pages"
    if group == "email":
        return f"Engaged with {int(row.email_engagement)} emails"
    if group == "webinar":
        return "Attended a webinar" if row.webinar else "No webinar attendance"
    if group == "size":
        return f"{int(row.employees):,} employees ({_size_band(row.employees)} band)"
    if group == "industry":
        r = seg["industry"].get(row.industry, base)
        return f"{row.industry.title()}: converts at {r / base:.1f}x the average"
    if group == "seniority":
        r = seg["seniority"].get(row.seniority, base)
        return f"{row.seniority.title()}-level contact ({r / base:.1f}x average)"
    r = seg["source_channel"].get(row.source_channel, base)
    return f"Came via {row.source_channel.replace('_', ' ')} ({r / base:.1f}x average)"


def route(p: float, demo: bool, thresholds: dict[str, float]) -> tuple[str, str]:
    if p >= thresholds["fast_lane"] or (demo and p >= thresholds["fast_lane"] * 0.75):
        return "fast_lane", "AE fast lane: call within 1 business hour"
    if p >= thresholds["sdr"]:
        return "sdr", "SDR sequence: personalised 5-touch, lead with the top reason"
    return "nurture", "Nurture: add to the industry content track, re-score weekly"


def score_open(lm: LeadModel, leads: pd.DataFrame, value_by_segment: dict[str, float],
               thresholds: dict[str, float]) -> pd.DataFrame:
    open_ = leads[leads["status"] == "open"].copy()
    if open_.empty:
        return open_
    X = features(open_)
    open_["p"] = lm.model.predict_proba(X)[:, 1]
    contrib = contributions(lm.model, X, GROUPS, lm.reference)
    base = lm.card.metrics["base_rate"]
    seg_col = {"industry": "industry", "seniority": "seniority", "channel": "source_channel"}

    def consistent(g: str, row: pd.Series, positive: bool) -> bool:
        """Only state a categorical reason when the segment's own rate agrees with the direction."""
        if g == "size":
            rate = lm.segment_rates["size_band"].get(_size_band(row["employees"]), base)
            return rate > base * 1.05 if positive else rate < base * 0.95
        if g not in seg_col:
            return True
        key = seg_col[g]
        rate = lm.segment_rates["source_channel" if g == "channel" else key].get(row[key], base)
        return rate > base * 1.05 if positive else rate < base * 0.95

    reasons, risks = [], []
    for idx, row in open_.iterrows():
        c = contrib.loc[idx].sort_values()
        reasons.append([reason_text(g, row, lm, base) for g in c.index[::-1]
                        if c[g] > 0.002 and consistent(g, row, True)][:3])
        risks.append([reason_text(g, row, lm, base) for g in c.index
                       if c[g] < -0.01 and consistent(g, row, False)][:1])
    open_["reasons"], open_["risks"] = reasons, risks
    seg = np.where(open_["employees"] < 60, "SMB", np.where(open_["employees"] < 600, "Mid-market", "Enterprise"))
    open_["segment"] = seg
    open_["first_year_value"] = [value_by_segment.get(s, 0.0) for s in seg]
    open_["expected_value"] = open_["p"] * open_["first_year_value"]
    lanes = [route(p, d, thresholds) for p, d in zip(open_["p"], open_["demo_requested"])]
    open_["lane"], open_["next_action"] = [lane for lane, _ in lanes], [a for _, a in lanes]
    return open_.sort_values("expected_value", ascending=False)


def icp_table(leads: pd.DataFrame, min_n: int = 60) -> list[dict]:
    """Which segments convert better than average, with a two-proportion z-test so noise is not sold as insight."""
    closed = leads[leads["status"] != "open"].copy()
    closed["y"] = (closed["status"] == "converted").astype(int)
    closed["size_band"] = closed["employees"].map(_size_band)
    base = closed["y"].mean()
    out = []
    for dim in ("industry", "size_band", "seniority", "source_channel"):
        for val, grp in closed.groupby(dim):
            n = len(grp)
            if n < min_n:
                continue
            r = grp["y"].mean()
            rest = closed[closed[dim] != val]["y"]
            p_pool = closed["y"].mean()
            se = np.sqrt(p_pool * (1 - p_pool) * (1 / n + 1 / max(1, len(rest))))
            z = (r - rest.mean()) / se if se else 0.0
            out.append({"dimension": dim, "value": str(val), "leads": int(n), "conversion": float(r),
                        "lift": float(r / base) if base else 0.0, "z": float(z), "significant": bool(abs(z) >= 2.0)})
    return sorted(out, key=lambda d: -d["lift"])
