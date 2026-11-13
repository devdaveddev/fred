"""Marketing analyst: channel economics, response curves, budget reallocation, anomaly detection."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..revcore import ltv_cac, ltv_wacc

PAID = ["paid_search", "linkedin", "events", "content"]
FREE = ["organic", "referral"]
WINDOW_MONTHS = 6


def channel_economics(leads: pd.DataFrame, accounts: pd.DataFrame, mrr: pd.DataFrame, spend: pd.DataFrame,
                      as_of: pd.Timestamp, gross_margin: float, monthly_churn: float, wacc: float) -> list[dict]:
    start = as_of - pd.DateOffset(months=WINDOW_MONTHS - 1)
    first = mrr[mrr["mrr"] > 0].groupby("account_id")["month"].min()
    new_ids = first[(first >= start) & (first <= as_of)].index
    new = accounts[accounts["account_id"].isin(new_ids)]
    arpa_new = mrr[mrr["account_id"].isin(new_ids)].groupby("account_id")["mrr"].first()
    new = new.assign(mrr=new["account_id"].map(arpa_new))
    sp = spend[(spend["day"] >= start) & (spend["day"] < as_of + pd.DateOffset(months=1))]
    by_ch = sp.groupby("channel")["amount"].sum()
    sales_cost = float(by_ch.get("sales_team", 0.0))
    total_new = max(1, len(new))
    closed = leads[(leads["status"] != "open") & (leads["created_at"] >= start - pd.DateOffset(months=2))]
    rows = []
    for ch in PAID + FREE:
        n = int((new["acquisition_channel"] == ch).sum())
        media = float(by_ch.get(ch, 0.0))
        loaded = media + sales_cost * n / total_new  # sales cost allocated by customers won
        arpa = float(new.loc[new["acquisition_channel"] == ch, "mrr"].mean()) if n else 0.0
        conv = closed[closed["source_channel"] == ch]
        cac = loaded / n if n else None
        ltv = ltv_wacc(arpa, gross_margin, monthly_churn, wacc) if n else 0.0
        rows.append({
            "channel": ch, "paid": ch in PAID, "spend": media, "spend_loaded": loaded,
            "leads": int(((leads["source_channel"] == ch) & (leads["created_at"] >= start)).sum()),
            "conversion": float((conv["status"] == "converted").mean()) if len(conv) else 0.0,
            "new_customers": n, "arpa": arpa,
            "cac_media": media / n if n and media else None, "cac_loaded": cac,
            "payback_months": cac / (arpa * gross_margin) if cac and arpa else None,
            "ltv_cac": ltv_cac(ltv, cac) if cac else None,
        })
    return rows


def response_curves(leads: pd.DataFrame, spend: pd.DataFrame) -> dict[str, dict]:
    """log(leads) = a + b log(spend) per channel, monthly. b < 1 = diminishing returns."""
    sp = spend.assign(month=spend["day"].dt.to_period("M").dt.to_timestamp()).groupby(["channel", "month"])["amount"].sum()
    ld = leads.assign(month=leads["created_at"].dt.to_period("M").dt.to_timestamp()) \
        .groupby(["source_channel", "month"]).size()
    closed = leads[leads["status"] != "open"]
    out = {}
    for ch in PAID:
        s = sp.get(ch)
        if s is None:
            continue
        df = pd.DataFrame({"spend": s, "leads": ld.get(ch, pd.Series(dtype=float))}).dropna()
        df = df[(df["spend"] > 0) & (df["leads"] > 0)].iloc[:-1]  # drop the in-progress/anomalous last month
        if len(df) < 6:
            continue
        x, y = np.log(df["spend"].to_numpy()), np.log(df["leads"].to_numpy())
        b, a = np.polyfit(x, y, 1)
        resid = y - (a + b * x)
        r2 = 1 - resid.var() / y.var() if y.var() else 0.0
        c = closed[closed["source_channel"] == ch]
        out[ch] = {"a": float(a), "b": float(b), "r2": float(r2), "n_months": len(df),
                   "conversion": float((c["status"] == "converted").mean()) if len(c) else 0.0,
                   "current_spend": float(df["spend"].tail(3).mean())}
    return out


def _customers(curve: dict, spend: float) -> float:
    return float(np.exp(curve["a"]) * spend ** curve["b"] * curve["conversion"]) if spend > 0 else 0.0


def reallocate(curves: dict[str, dict], step: float = 500.0, lo: float = 0.5, hi: float = 1.75,
               min_r2: float = 0.3) -> dict:
    """Same total budget, moved to where the next dollar buys the most customers.

    Greedy on marginal customers per dollar is optimal here because every fitted curve is concave
    (0 < b < 1). Guardrails: each channel stays within [lo, hi] x current spend so we never
    extrapolate far outside observed data, and poorly fitted channels (R^2 < min_r2) are frozen.
    """
    movable = {c: v for c, v in curves.items() if v["r2"] >= min_r2 and 0.05 < v["b"] < 1.0}
    frozen = [c for c in curves if c not in movable]
    if len(movable) < 2:
        return {"moves": [], "frozen": frozen, "delta_customers_monthly": 0.0, "budget": 0.0}
    cur = {c: v["current_spend"] for c, v in movable.items()}
    budget = sum(cur.values())
    alloc = {c: lo * s for c, s in cur.items()}
    left = budget - sum(alloc.values())
    while left >= step:
        gains = {c: _customers(movable[c], alloc[c] + step) - _customers(movable[c], alloc[c])
                 for c in alloc if alloc[c] + step <= hi * cur[c]}
        if not gains:
            break
        best = max(gains, key=gains.get)
        alloc[best] += step
        left -= step
    before = sum(_customers(movable[c], cur[c]) for c in cur)
    after = sum(_customers(movable[c], alloc[c]) for c in alloc)
    moves = [{"channel": c, "current": cur[c], "proposed": alloc[c], "change": alloc[c] - cur[c],
              "customers_now": _customers(movable[c], cur[c]), "customers_after": _customers(movable[c], alloc[c]),
              "marginal_cac_now": step / max(1e-9, _customers(movable[c], cur[c] + step) - _customers(movable[c], cur[c]))}
             for c in cur]
    return {"moves": sorted(moves, key=lambda m: m["change"]), "frozen": frozen, "budget": budget,
            "customers_before": before, "customers_after": after, "delta_customers_monthly": after - before}


def anomalies(spend: pd.DataFrame, leads: pd.DataFrame, recent_days: int = 14, history_days: int = 60,
              z_cut: float = 4.0) -> list[dict]:
    """Robust z-score (median / MAD) of daily spend, checked against leads over the same days."""
    end = spend["day"].max()
    rec_start, hist_start = end - pd.Timedelta(days=recent_days - 1), end - pd.Timedelta(days=recent_days + history_days - 1)
    out = []
    for ch, s in spend.groupby("channel"):
        s = s.set_index("day")["amount"]
        hist, rec = s[(s.index >= hist_start) & (s.index < rec_start)], s[s.index >= rec_start]
        med = hist.median()
        mad = 1.4826 * (hist - med).abs().median()
        if not mad or rec.empty:
            continue
        z = (rec - med) / mad
        hot = z[z > z_cut]
        if len(hot) < 3:
            continue
        excess = float((rec[hot.index] - med).sum())
        d0 = hot.index.min()
        lead_days = leads[leads["source_channel"] == ch].assign(day=lambda x: x["created_at"].dt.normalize()) \
            .groupby("day").size()
        lead_hist = lead_days[(lead_days.index >= hist_start) & (lead_days.index < rec_start)]
        lead_hot = lead_days.reindex(hot.index, fill_value=0)
        lead_ratio = float(lead_hot.mean() / lead_hist.mean()) if len(lead_hist) and lead_hist.mean() else None
        out.append({"channel": ch, "since": str(d0.date()), "days": len(hot), "typical_daily": float(med),
                    "recent_daily": float(rec[hot.index].mean()), "excess_spend": excess,
                    "max_z": float(hot.max()), "lead_ratio": lead_ratio,
                    "verdict": "spend up, leads flat" if lead_ratio is not None and lead_ratio < 1.3 else "spend up"})
    return out
