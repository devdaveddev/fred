from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .formulas import FORMULA_VERSION, cac_payback_months, ltv_cac, ltv_wacc


@dataclass(frozen=True)
class Baseline:
    customers: int
    arpa_monthly: float
    gross_margin: float
    monthly_churn: float
    monthly_expansion: float  # net expansion (expansion - contraction) MRR as share of MRR
    new_customers_monthly: float
    sm_spend_monthly: float
    other_opex_monthly: float
    cash: float
    annual_wacc: float
    price_elasticity: float = -0.6  # % change in new-logo volume per % price change


@dataclass(frozen=True)
class Shock:
    price_change: float = 0.0  # 0.20 = +20%
    churn_multiplier: float = 1.0
    sm_spend_change: float = 0.0
    cac_change: float = 0.0  # persona swap effect on CAC


def _pct(a: np.ndarray) -> dict[str, float]:
    return {k: float(np.percentile(a, p)) for k, p in (("p10", 10), ("p50", 50), ("p90", 90))}


def simulate(base: Baseline, shock: Shock, months: int = 24, runs: int = 5000, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    price = base.arpa_monthly * (1 + shock.price_change)
    vol_mult = max(0.0, 1 + base.price_elasticity * shock.price_change)
    sm = base.sm_spend_monthly * (1 + shock.sm_spend_change)
    new_mean = base.new_customers_monthly * vol_mult * (1 + shock.sm_spend_change) / (1 + shock.cac_change)
    churn = min(0.99, base.monthly_churn * shock.churn_multiplier)

    mrr = np.full(runs, float(base.customers)) * price
    start_mrr = mrr.copy()
    cohort = mrr.copy()  # starting cohort only, for NRR
    cash = np.full(runs, float(base.cash))
    runway = np.full(runs, np.inf)
    arr_path = np.empty((months, 3))

    for m in range(months):
        c = rng.beta(churn * 200 + 1e-9, (1 - churn) * 200, runs)
        e = rng.normal(base.monthly_expansion, abs(base.monthly_expansion) * 0.25, runs)
        if base.monthly_expansion >= 0:
            e = e.clip(0)
        new = rng.poisson(new_mean, runs)
        if m < 12:
            cohort = cohort * (1 - c + e)
        mrr = mrr * (1 - c + e) + new * price
        cash -= sm + base.other_opex_monthly - mrr * base.gross_margin
        hit = (cash <= 0) & np.isinf(runway)
        runway[hit] = m + 1
        arr_path[m] = np.percentile(mrr * 12, (10, 50, 90))

    cac_v = sm / new_mean if new_mean else float("inf")
    ltv = ltv_wacc(price, base.gross_margin, churn, base.annual_wacc)
    return {
        "formula_version": FORMULA_VERSION,
        "seed": seed,
        "runs": runs,
        "months": months,
        "inputs": {"baseline": asdict(base), "shock": asdict(shock)},
        "arr_end": _pct(mrr * 12),
        "arr_path": [{"month": i + 1, "p10": r[0], "p50": r[1], "p90": r[2]} for i, r in enumerate(arr_path)],
        "nrr_12m": _pct(cohort / start_mrr),
        "p_cash_out": float(np.mean(np.isfinite(runway))),
        # months + 1 means no cash-out inside the horizon
        "runway_months_p50": float(np.median(np.where(np.isinf(runway), months + 1, runway))),
        "cac": cac_v,
        "cac_payback_months": cac_payback_months(cac_v, price, base.gross_margin),
        "ltv_cac_wacc": ltv_cac(ltv, cac_v),
    }


def compare(base: Baseline, shock: Shock, **kw) -> dict:
    a, b = simulate(base, Shock(), **kw), simulate(base, shock, **kw)
    keys = ("cac", "cac_payback_months", "ltv_cac_wacc", "p_cash_out", "runway_months_p50")
    return {
        "baseline": a,
        "scenario": b,
        "delta": {k: b[k] - a[k] for k in keys} | {"arr_end_p50": b["arr_end"]["p50"] - a["arr_end"]["p50"]},
    }
