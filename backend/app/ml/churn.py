"""Churn gravity: P(account churns or loses >50% MRR in the next 3 months) x MRR at risk.

Features come from product usage and billing only, so the model works from day one of a connector.
Gradient boosting, not a deep sequence model: a company with hundreds of churn events cannot feed
an LSTM honestly (the blueprint's own rule; upgrade path documented in ARCHITECTURE.md).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .common import ALGORITHM, ModelCard, contributions, fit_time_split, version_of

SEGMENTS = ["SMB", "Mid-market", "Enterprise"]
GROUPS = {
    "utilisation": ["util_now"],
    "trend": ["util_trend"],
    "low_weeks": ["low_weeks"],
    "intensity": ["events_per_seat"],
    "mrr_change": ["mrr_change_3m"],
    "tenure": ["tenure_months"],
    "size": ["log_mrr"] + [f"seg_{s}" for s in SEGMENTS],
}
FEATURES = [c for cols in GROUPS.values() for c in cols]
HORIZON = 3  # months


def panel(usage: pd.DataFrame, mrr: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    """account x month rows with features known at month end, plus the forward-looking label."""
    u = usage.copy()
    u["month"] = u["week"].dt.to_period("M").dt.to_timestamp()
    u["low"] = (u["active_users"] / u["seats"]) < 0.3
    m = u.groupby(["account_id", "month"]).agg(seats=("seats", "sum"), active=("active_users", "sum"),
                                               events=("events", "sum"), low_weeks=("low", "sum")).reset_index()
    m["util"] = m["active"] / m["seats"]
    m = m.merge(mrr, on=["account_id", "month"], how="inner").sort_values(["account_id", "month"])
    g = m.groupby("account_id")
    m["util_now"] = m["util"]
    prev = (g["active"].shift(1) + g["active"].shift(2)) / (g["seats"].shift(1) + g["seats"].shift(2))
    m["util_trend"] = (m["util"] - prev).fillna(0.0)
    m["events_per_seat"] = m["events"] / m["seats"]
    m["mrr_change_3m"] = (m["mrr"] / g["mrr"].shift(3) - 1).fillna(0.0)
    m["tenure_months"] = g.cumcount()
    m["log_mrr"] = np.log1p(m["mrr"])
    seg = accounts.set_index("account_id")["segment"]
    m["segment"] = m["account_id"].map(seg)
    for s in SEGMENTS:
        m[f"seg_{s}"] = (m["segment"] == s).astype(int)

    # label: within the next HORIZON months the account disappears or drops below 50% of current MRR
    wide = mrr.pivot(index="account_id", columns="month", values="mrr").fillna(0.0)
    W = wide.to_numpy()
    n_months = W.shape[1]
    # future minimum over the next HORIZON months, per (account, month) cell
    padded = np.concatenate([W, np.full((W.shape[0], HORIZON), np.inf)], axis=1)
    fut_min = np.min(np.stack([padded[:, k + 1: k + 1 + n_months] for k in range(HORIZON)]), axis=0)
    row = wide.index.get_indexer(m["account_id"])
    col = wide.columns.get_indexer(m["month"])
    m["y"] = (fut_min[row, col] < 0.5 * m["mrr"].to_numpy()).astype(int)
    m["observable"] = col + HORIZON < n_months
    return m


@dataclass
class ChurnModel:
    model: object
    card: ModelCard
    reference: pd.Series
    healthy: dict[str, float]


def train(pnl: pd.DataFrame, snapshot_id: str) -> ChurnModel:
    obs = pnl[pnl["observable"]]
    X, y = obs[FEATURES], obs["y"].to_numpy()
    model, metrics, n_tr, n_te = fit_time_split(X, y, obs["month"], test_frac=0.25)
    healthy = obs[obs["y"] == 0]
    ref = healthy[FEATURES].median()
    seg_cols = [f"seg_{s}" for s in SEGMENTS]
    ref[seg_cols] = 0
    ref["seg_Mid-market"] = 1
    card = ModelCard(kind="churn_gravity", version=version_of("churn_gravity", snapshot_id, FEATURES),
                     algorithm=ALGORITHM, snapshot_id=snapshot_id, features=FEATURES, n_train=n_tr, n_test=n_te,
                     metrics=metrics,
                     notes=[f"Label: churned or lost >50% MRR within {HORIZON} months.",
                            "Unit: account x month. Holdout: most recent 25% of observable months.",
                            "Upgrade to a sequence model only past ~2,000 labelled churn events."])
    return ChurnModel(model, card, ref, {k: float(healthy[k].median()) for k in
                                         ("util_now", "events_per_seat", "tenure_months")})


PLAYS = {
    "utilisation": "Adoption play: CSM-led onboarding refresh and admin training; target 60%+ seat utilisation",
    "trend": "Executive check-in within 7 days; review open tickets and recent product changes",
    "low_weeks": "Trigger in-app re-engagement and a usage review call this week",
    "intensity": "Show the team the 3 workflows heavy users rely on; offer a working session",
    "mrr_change": "Right-size the plan before renewal and offer a multi-year term at a fair discount",
    "tenure": "Run the onboarding milestone review: are they past first value?",
    "size": "Assign named CSM coverage for this account size",
}


def reason_text(group: str, row: pd.Series, cm: ChurnModel) -> str:
    h = cm.healthy
    if group == "utilisation":
        return f"Seat utilisation {row.util_now:.0%} (healthy accounts: {h['util_now']:.0%})"
    if group == "trend":
        return f"Usage {'down' if row.util_trend < 0 else 'up'} {abs(row.util_trend) * 100:.0f} pts vs prior 2 months"
    if group == "low_weeks":
        return f"{int(row.low_weeks)} low-usage week{'s' if row.low_weeks != 1 else ''} this month"
    if group == "intensity":
        return f"{row.events_per_seat:.0f} events per seat (healthy: {h['events_per_seat']:.0f})"
    if group == "mrr_change":
        return f"MRR {'down' if row.mrr_change_3m < 0 else 'up'} {abs(row.mrr_change_3m):.0%} in 3 months"
    if group == "tenure":
        return f"Customer for {int(row.tenure_months)} months"
    return f"{row.segment} account at ${row.mrr:,.0f} MRR"


def score_current(cm: ChurnModel, pnl: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    last = pnl["month"].max()
    cur = pnl[pnl["month"] == last].copy()
    X = cur[FEATURES]
    cur["p"] = cm.model.predict_proba(X)[:, 1]
    cur["mrr_at_risk"] = cur["p"] * cur["mrr"]
    contrib = contributions(cm.model, X, GROUPS, cm.reference)
    h = cm.healthy

    def unhealthy(g: str, row: pd.Series) -> bool:
        """State a risk reason only if the fact itself is bad (a model interaction alone is not a reason)."""
        return {
            "utilisation": row.util_now < h["util_now"] * 0.9,
            "trend": row.util_trend < -0.02,
            "low_weeks": row.low_weeks >= 1,
            "intensity": row.events_per_seat < h["events_per_seat"] * 0.9,
            "mrr_change": row.mrr_change_3m < -0.05,
            "tenure": row.tenure_months < 6,
            "size": False,
        }[g]

    reasons, plays = [], []
    for idx, row in cur.iterrows():
        c = contrib.loc[idx].sort_values(ascending=False)
        top = [g for g in c.index if c[g] > 0.005 and unhealthy(g, row)][:3]
        reasons.append([reason_text(g, row, cm) for g in top])
        plays.append(PLAYS[top[0]] if top else "Keep in the standard success cadence")
    cur["reasons"], cur["play"] = reasons, plays
    names = accounts.set_index("account_id")[["name", "industry", "acquisition_channel"]]
    cur = cur.join(names, on="account_id")
    return cur.sort_values("mrr_at_risk", ascending=False)
