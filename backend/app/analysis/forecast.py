"""MRR forecast: two honest methods, backtested, best one wins.

1. Driver model: MRR(t+1) = MRR(t) x (1 - revenue churn + net expansion) + new MRR(t), with
   rates from the trailing 6 months and new MRR on a fitted linear trend.
2. Holt's linear trend (double exponential smoothing), parameters by grid search.

Both are backtested on the last 6 known months; the band is the backtest error, scaled by horizon.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BACKTEST = 6


def decompose(mrr: pd.DataFrame) -> pd.DataFrame:
    """Per month: total MRR, new, churned, net expansion (retained accounts)."""
    wide = mrr.pivot(index="account_id", columns="month", values="mrr").fillna(0.0).sort_index(axis=1)
    W = wide.to_numpy()
    prev = np.concatenate([np.zeros((W.shape[0], 1)), W[:, :-1]], axis=1)
    new = np.where((prev == 0) & (W > 0), W, 0).sum(0)
    churned = np.where((prev > 0) & (W == 0), prev, 0).sum(0)
    net_exp = np.where((prev > 0) & (W > 0), W - prev, 0).sum(0)
    return pd.DataFrame({"month": wide.columns, "mrr": W.sum(0), "new": new, "churned": churned,
                         "net_expansion": net_exp, "prior": prev.sum(0)})


def _driver(d: pd.DataFrame, h: int) -> np.ndarray:
    tail = d.iloc[1:].tail(6)
    churn = float((tail["churned"] / tail["prior"]).mean())
    exp = float((tail["net_expansion"] / tail["prior"]).mean())
    recent = d.iloc[1:].tail(12)
    slope, icpt = np.polyfit(np.arange(len(recent)), recent["new"].to_numpy(), 1)
    m = float(d["mrr"].iloc[-1])
    out = []
    for k in range(1, h + 1):
        new = max(0.0, icpt + slope * (len(recent) - 1 + k))
        m = m * (1 - churn + exp) + new
        out.append(m)
    return np.array(out)


def _holt(y: np.ndarray, h: int) -> np.ndarray:
    best, best_sse = None, np.inf
    for a in np.linspace(0.1, 0.9, 9):
        for b in np.linspace(0.05, 0.5, 10):
            lvl, tr, sse = y[0], y[1] - y[0], 0.0
            for t in range(1, len(y)):
                sse += (y[t] - (lvl + tr)) ** 2
                new_lvl = a * y[t] + (1 - a) * (lvl + tr)
                tr = b * (new_lvl - lvl) + (1 - b) * tr
                lvl = new_lvl
            if sse < best_sse:
                best, best_sse = (lvl, tr), sse
    lvl, tr = best
    return np.array([lvl + tr * k for k in range(1, h + 1)])


def _mape(actual: np.ndarray, pred: np.ndarray) -> float:
    return float(np.mean(np.abs(pred - actual) / actual))


def forecast(mrr: pd.DataFrame, horizon: int = 12) -> dict:
    d = decompose(mrr)
    train, test = d.iloc[:-BACKTEST], d["mrr"].iloc[-BACKTEST:].to_numpy()
    errors = {"driver": _mape(test, _driver(train, BACKTEST)),
              "holt": _mape(test, _holt(train["mrr"].to_numpy(), BACKTEST))}
    method = min(errors, key=errors.get)
    pred = _driver(d, horizon) if method == "driver" else _holt(d["mrr"].to_numpy(), horizon)
    err = max(errors[method], 0.005)
    last = pd.Timestamp(d["month"].iloc[-1])
    points = []
    for k, v in enumerate(pred, start=1):
        band = err * np.sqrt(k / BACKTEST)
        points.append({"month": str((last + pd.DateOffset(months=k)).date()), "mrr": float(v),
                       "low": float(v * (1 - band)), "high": float(v * (1 + band))})
    tail = d.iloc[1:].tail(6)
    return {
        "method": method,
        "backtest_mape": errors,
        "history": [{"month": str(pd.Timestamp(m).date()), "mrr": float(v)} for m, v in zip(d["month"], d["mrr"])],
        "forecast": points,
        "drivers": {"revenue_churn": float((tail["churned"] / tail["prior"]).mean()),
                    "net_expansion": float((tail["net_expansion"] / tail["prior"]).mean()),
                    "new_mrr_monthly": float(tail["new"].mean())},
        "decomposition": d.assign(month=d["month"].astype(str)).to_dict("records"),
    }
