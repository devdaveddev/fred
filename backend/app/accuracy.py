"""Fred vs your last six closes: would Fred have been right, using only what it knew at the time?

Every check replays history with data cut off at the moment the call would have been made:
  1. Forecast: for each of the last six months, the forecast made 1 and 3 months earlier vs what happened,
     against the "assume nothing changes" baseline an analyst's spreadsheet often uses.
  2. Early warnings: lines Fred flagged as running over plan at a month-end. Did they keep running over?
  3. Variance explanations: how much of each month's budget gap the bridges attribute to named causes.
  4. Data agreement: billing revenue vs revenue in the books, month by month.
  5. Models: out-of-time results of the churn and lead models (trained on the past, tested on later data).
Nothing here is tuned to look good: every number is computed from the workspace's own history.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .analysis import forecast as fc
from .finance import bva

MONTHS = 6
MIN_HISTORY = 12  # months of billing a forecast needs (6 to backtest its own method choice + 6 to fit)


def forecast_backtest(mrr: pd.DataFrame, months: int = MONTHS, horizons: tuple[int, ...] = (1, 3)) -> dict:
    d = fc.decompose(mrr)
    all_months = list(pd.to_datetime(d["month"]))
    series = d.set_index(pd.to_datetime(d["month"]))["mrr"]
    rows = []
    for target in all_months[-months:]:
        for h in horizons:
            cutoff = target - pd.DateOffset(months=h)
            past = mrr[mrr["month"] <= cutoff]
            if past["month"].nunique() < MIN_HISTORY:
                continue
            f = fc.forecast(past, horizon=h)
            p = f["forecast"][h - 1]
            actual, naive = float(series[target]), float(series[cutoff])
            rows.append({"month": str(target.date()), "horizon": h, "made_in": str(cutoff.date()), "method": f["method"],
                         "predicted": p["mrr"], "low": p["low"], "high": p["high"], "actual": actual,
                         "error": p["mrr"] / actual - 1, "naive_error": naive / actual - 1,
                         "in_band": p["low"] <= actual <= p["high"]})
    summary = {}
    for h in horizons:
        r = [x for x in rows if x["horizon"] == h]
        if not r:
            continue
        mape = float(np.mean([abs(x["error"]) for x in r]))
        naive = float(np.mean([abs(x["naive_error"]) for x in r]))
        summary[h] = {"n": len(r), "mape": mape, "bias": float(np.mean([x["error"] for x in r])), "naive_mape": naive,
                      "beats_naive": mape < naive, "band_coverage": float(np.mean([x["in_band"] for x in r]))}
    return {"rows": rows, "summary": summary}


def _closed(fd, months: int) -> list[pd.Timestamp]:
    return fd.closed_months[-months:]


def early_warnings(fd, mrr: pd.DataFrame, months: int = MONTHS) -> dict:
    """At each past month-end, which non-payroll lines did Fred say were running over/under plan? Then check
    the months after: did the gap keep the same direction?"""
    closed = fd.closed_months
    calls = []
    for m in _closed(fd, months)[:-1]:  # need at least one later month to judge
        r = bva.review(_cut(fd, m), mrr[mrr["month"] <= m], m)
        for f in r["findings"]:
            comps = f["components"] if isinstance(f["components"], dict) else {}
            if comps.get("pattern") != "run_rate":
                continue
            later = [x for x in closed if x > m]
            a = fd.actual[(fd.actual["account"] == f["key"]) & fd.actual["month"].isin(later)].set_index("month")["amount"]
            p = fd.plan_lines[(fd.plan_lines["account"] == f["key"]) & fd.plan_lines["month"].isin(later)] \
                .set_index("month")["amount"]
            p = p.reindex(a.index)
            if fd.accounts.set_index("code").loc[f["key"], "kind"] == "cogs":  # judged as flagged: per revenue dollar
                rev = fd.actual[fd.actual["account"] == "4000"].set_index("month")["amount"].reindex(a.index)
                rev_p = fd.plan_lines[fd.plan_lines["account"] == "4000"].set_index("month")["amount"].reindex(a.index)
                p = p * rev / rev_p
            gap = (a - p).dropna()
            if gap.empty:
                continue
            sign = np.sign(comps.get("annualized") or f["variance"])
            held = float((np.sign(gap) == sign).mean())
            calls.append({"flagged_in": str(m.date()), "line": f["area"], "said": f["title"],
                          "months_after": len(gap), "held_share": held, "right": held >= 0.5})
    # one call per line: the first time Fred raised it
    first: dict[str, dict] = {}
    for c in calls:
        first.setdefault(c["line"], c)
    calls = list(first.values())
    return {"calls": calls, "n": len(calls), "right": sum(c["right"] for c in calls)}


def _cut(fd, month: pd.Timestamp):
    """The finance data as it stood at a month-end: books closed through that month only."""
    from dataclasses import replace

    return replace(fd, actual=fd.actual[fd.actual["month"] <= month], payroll=fd.payroll[fd.payroll["month"] <= month],
                   cash=fd.cash[fd.cash["month"] <= month])


def explanation_coverage(fd, mrr: pd.DataFrame, months: int = MONTHS) -> dict:
    out = []
    for m in _closed(fd, months):
        r = bva.review(_cut(fd, m), mrr[mrr["month"] <= m], m)
        explained = gap = 0.0
        rb = r.get("revenue_bridge")
        rev = next(x for x in r["pnl"] if x["key"] == "revenue")
        if rb:
            gap += abs(rev["var_ytd"])
            explained += abs(rev["var_ytd"]) - abs(rev["var_ytd"] - sum(rb["ytd"][k] for k in
                                                                        ("volume", "price", "churn", "expansion")))
        for d in r["payroll"]["departments"].values():
            gap += abs(d["variance"])
            explained += abs(d["variance"]) - abs(d["variance"] - sum(d["components"].values()))
        out.append({"month": str(m.date()), "gap": gap, "explained": max(explained, 0.0),
                    "share": (max(explained, 0.0) / gap) if gap else 1.0, "findings": len(r["findings"])})
    return {"months": out, "share": float(np.mean([x["share"] for x in out])) if out else None}


def data_agreement(fd, mrr: pd.DataFrame, months: int = MONTHS) -> dict:
    rev_codes = fd.accounts[fd.accounts["kind"] == "revenue"]["code"]
    books = fd.actual[fd.actual["account"].isin(rev_codes)].groupby("month")["amount"].sum()
    billing = mrr.groupby("month")["mrr"].sum()
    rows = []
    for m in fd.closed_months[-months:]:
        if m in books.index and m in billing.index and books[m]:
            rows.append({"month": str(m.date()), "books": float(books[m]), "billing": float(billing[m]),
                         "diff": float(billing[m] / books[m] - 1)})
    worst = max((abs(r["diff"]) for r in rows), default=None)
    return {"rows": rows, "max_abs_diff": worst}


def models(a) -> list[dict]:
    out = []
    for m, label, outcome in ((a.churn_model, "Churn risk", "churned"), (a.lead_model, "Lead scoring", "converted")):
        if m is None:
            continue
        x = m.card.metrics
        out.append({"kind": m.card.kind, "label": label, "outcome": outcome, "n_test": m.card.n_test,
                    "auc": x.get("auc"), "top_decile_rate": x.get("top_decile_rate"), "base_rate": x.get("base_rate"),
                    "lift": x.get("top_decile_lift"), "calibration_error": x.get("calibration_error")})
    return out


def verdict(rep: dict) -> list[dict]:
    """Plain-language scorecard: one line per check, with a pass/watch/fail state."""
    lines = []
    fs = rep["forecast"]["summary"].get(1) or next(iter(rep["forecast"]["summary"].values()), None)
    if fs:
        state = "good" if fs["beats_naive"] and fs["mape"] < 0.05 else "watch" if fs["mape"] < 0.10 else "bad"
        lines.append({"check": "Forecast", "state": state,
                      "text": f"Next-month forecasts were off by {fs['mape']:.1%} on average over {fs['n']} months"
                              + (f", vs {fs['naive_mape']:.1%} for assuming no change." if fs["naive_mape"] else ".")})
    ew = rep.get("early_warnings")
    if ew and ew["n"]:
        lines.append({"check": "Early warnings", "state": "good" if ew["right"] / ew["n"] >= 0.7 else "watch",
                      "text": f"{ew['right']} of {ew['n']} lines Fred flagged as drifting from plan kept drifting afterwards."})
    ec = rep.get("explanations")
    if ec and ec["share"] is not None:
        lines.append({"check": "Explanations", "state": "good" if ec["share"] >= 0.95 else "watch",
                      "text": f"{ec['share']:.0%} of revenue and payroll gaps were traced to named causes."})
    da = rep.get("agreement")
    if da and da["max_abs_diff"] is not None:
        lines.append({"check": "Data agreement", "state": "good" if da["max_abs_diff"] < 0.02 else "watch",
                      "text": f"Billing and the books agreed within {da['max_abs_diff']:.1%} every month."})
    for m in rep["models"]:
        lines.append({"check": m["label"], "state": "good" if (m["lift"] or 0) >= 2 else "watch",
                      "text": f"Of the 10% Fred ranked highest, {m['top_decile_rate']:.0%} {m['outcome']}, vs "
                              f"{m['base_rate']:.0%} overall (tested on {m['n_test']} later cases)."})
    return lines


def report(ws, a, fd) -> dict:
    mrr = ws.mrr_frame()
    rep: dict = {"forecast": forecast_backtest(mrr), "models": models(a)}
    if fd is not None and len(fd.closed_months) >= 2:
        rep["early_warnings"] = early_warnings(fd, mrr)
        rep["explanations"] = explanation_coverage(fd, mrr)
        rep["agreement"] = data_agreement(fd, mrr)
    rep["verdict"] = verdict(rep)
    return rep
