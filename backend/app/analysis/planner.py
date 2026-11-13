"""Founder's office planner: what it takes to hit an ARR goal, and whether the cash allows it.

Closed-form where possible, bisection where not. Every lever is solved independently so the founder
sees the trade-off: buy growth (S&M), keep growth (retention), or both.
"""
from __future__ import annotations

import math


def _arr_at(mrr0: float, g: float, n: float, arpa_new: float, months: int) -> float:
    s = sum(g ** k for k in range(months))
    return 12 * (mrr0 * g ** months + n * arpa_new * s)


def _cash_path(cash: float, mrr0: float, g: float, n: float, arpa_new: float, sm: float, opex: float, gm: float,
               months: int) -> tuple[float, int | None]:
    m, low_month = mrr0, None
    for k in range(1, months + 1):
        m = m * g + n * arpa_new
        cash -= sm + opex - m * gm
        if cash <= 0 and low_month is None:
            low_month = k
    return cash, low_month


def plan(*, target_arr: float, months: int, mrr0: float, revenue_churn: float, net_expansion: float,
         new_customers_now: float, arpa_new: float, sm_monthly: float, opex_monthly: float, cash: float,
         gross_margin: float, spend_elasticity: float, max_sm_observed: float | None = None) -> dict:
    g = 1 - revenue_churn + net_expansion
    s = sum(g ** k for k in range(months))
    baseline_arr = _arr_at(mrr0, g, new_customers_now, arpa_new, months)
    gap = target_arr - baseline_arr

    # Lever 1: more new customers, bought with S&M along the fitted diminishing-returns curve
    n_req = max(0.0, (target_arr / 12 - mrr0 * g ** months) / (arpa_new * s)) if arpa_new else math.inf
    b = min(1.0, max(0.3, spend_elasticity))
    sm_req = sm_monthly * (n_req / new_customers_now) ** (1 / b) if new_customers_now else math.inf
    end_cash, cash_out = _cash_path(cash, mrr0, g, n_req, arpa_new, sm_req, opex_monthly, gross_margin, months)
    final_mrr = target_arr / 12
    end_burn = sm_req + opex_monthly - final_mrr * gross_margin
    runway_after = end_cash / end_burn if end_burn > 0 else math.inf

    # Lever 2: same acquisition, better retention (bisection on monthly growth factor g)
    lo, hi = 0.80, 1.10
    if _arr_at(mrr0, hi, new_customers_now, arpa_new, months) < target_arr:
        g_req = None
    else:
        for _ in range(60):
            mid = (lo + hi) / 2
            if _arr_at(mrr0, mid, new_customers_now, arpa_new, months) < target_arr:
                lo = mid
            else:
                hi = mid
        g_req = hi
    churn_req = 1 + net_expansion - g_req if g_req is not None else None

    feasible = cash_out is None and runway_after >= 6
    # Out-of-sample guard: the spend curve was fitted on observed spend; far beyond it the maths is a guess.
    ratio = n_req / new_customers_now if new_customers_now else math.inf
    warnings = []
    if ratio > 1.75:
        warnings.append(f"Needs {ratio:.1f}x today's new-customer rate from month 1; real teams ramp over quarters.")
    if max_sm_observed and sm_req > 1.5 * max_sm_observed:
        warnings.append(f"Required S&M is {sm_req / max_sm_observed:.1f}x the highest month in your data, "
                        "so the spend curve is extrapolated far outside what it was fitted on.")
    stretch = bool(warnings)
    if gap <= 0:
        verdict = "On track: the current trajectory reaches the goal without changes."
    elif stretch:
        verdict = ("Out of sample: the maths closes, but only by assuming acquisition far beyond anything your data "
                   "has shown. Extend the timeline or treat this as a fundraising case, not a plan.")
    elif feasible:
        verdict = "Reachable by spending more, and cash holds with at least 6 months of runway left at the end."
    elif churn_req is not None and churn_req >= 0:
        verdict = "Not fundable by spend alone; it needs a retention improvement or new capital."
    else:
        verdict = "Not reachable on current economics in this time frame without new capital."

    return {
        "target_arr": target_arr, "months": months, "baseline_arr": baseline_arr, "gap": gap,
        "monthly_growth_factor": g,
        "lever_spend": {"new_customers_needed": n_req, "new_customers_now": new_customers_now,
                        "sm_monthly_needed": sm_req, "sm_monthly_now": sm_monthly,
                        "cash_out_month": cash_out, "end_cash": end_cash, "runway_after_months": runway_after,
                        "spend_elasticity": b},
        "lever_retention": {"revenue_churn_now": revenue_churn,
                            "revenue_churn_needed": churn_req if churn_req is not None and churn_req >= 0 else None,
                            "achievable": churn_req is not None and churn_req >= 0},
        "feasible_with_spend": feasible and not stretch,
        "out_of_sample": stretch,
        "warnings": warnings,
        "verdict": verdict,
    }
