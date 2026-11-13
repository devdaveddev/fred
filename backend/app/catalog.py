"""Display metadata + health thresholds per metric. Thresholds come from the formula contract."""
from __future__ import annotations

import math
from collections.abc import Callable

# id: (label, group, unit, benchmark text, status fn, help)
StatusFn = Callable[[float], str]


def _band(good: Callable[[float], bool], bad: Callable[[float], bool]) -> StatusFn:
    return lambda v: "good" if good(v) else ("bad" if bad(v) else "watch")


METRICS: dict[str, tuple[str, str, str, str | None, StatusFn | None, str]] = {
    "arr": ("ARR", "Growth", "currency", None, None, "Current MRR annualised."),
    "mrr": ("MRR", "Growth", "currency", None, None, "Recurring revenue this month, one-time fees excluded."),
    "active_customers": ("Customers", "Growth", "count", None, None, "Accounts paying this month."),
    "arpa": ("ARPA", "Growth", "currency", None, None, "Average monthly revenue per paying account."),
    "nrr_12m": ("Net revenue retention", "Retention", "percent", "Healthy at 100% or more",
                _band(lambda v: v >= 1.0, lambda v: v < 0.9),
                "Revenue kept from customers you had 12 months ago, including expansion. New logos excluded."),
    "grr_12m": ("Gross revenue retention", "Retention", "percent", "Healthy at 90% or more",
                _band(lambda v: v >= 0.9, lambda v: v < 0.8),
                "Revenue kept from the same cohort, ignoring expansion. Capped at 100%."),
    "monthly_logo_churn": ("Monthly logo churn", "Retention", "percent", "Below 2% for mid-market",
                           _band(lambda v: v < 0.02, lambda v: v > 0.035),
                           "Share of paying accounts lost per month, averaged over the last 3 months."),
    "cac": ("CAC", "Unit economics", "currency", None, None,
            "Sales and marketing spend divided by new customers, last 3 months."),
    "cac_payback_months": ("CAC payback", "Unit economics", "months", "Under 12 months is strong",
                           _band(lambda v: v <= 12, lambda v: v > 24),
                           "Months of gross profit needed to earn back the cost of acquiring a customer."),
    "ltv_cac": ("LTV:CAC (WACC-adjusted)", "Unit economics", "multiple", "3x or more",
                _band(lambda v: v >= 3, lambda v: v < 1.5),
                "Lifetime gross profit, discounted at your cost of capital, per dollar of CAC."),
    "magic_number": ("Magic Number", "Efficiency", "multiple", "Above 0.75 is efficient",
                     _band(lambda v: v >= 0.75, lambda v: v < 0.5),
                     "New annualised revenue this quarter per dollar of last quarter's S&M spend."),
    "burn_multiple": ("Burn multiple", "Efficiency", "multiple", "Below 1.5 is good",
                      _band(lambda v: v < 1.5, lambda v: v > 2),
                      "Cash burned for each dollar of net new ARR, last 3 months."),
    "net_burn_monthly": ("Net burn / month", "Cash", "currency", None, None,
                         "Spend minus gross profit, averaged over the last 3 months."),
    "runway_months": ("Runway", "Cash", "months", "18 months or more",
                      _band(lambda v: v >= 18, lambda v: v < 12),
                      "Months until cash runs out at the current net burn."),
}

ORDER = list(METRICS)


def status_for(metric: str, value: float | None) -> str:
    fn = METRICS[metric][4]
    if fn is None or value is None:
        return "neutral"
    if math.isinf(value):
        # inf payback/burn multiple is bad; inf runway (cash-flow positive) is good
        return "good" if metric in ("runway_months", "ltv_cac") else "bad"
    return fn(value)
