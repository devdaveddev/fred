from __future__ import annotations

import numpy as np

# 1.1.0: burn multiple is 0 when not burning; scenario baseline uses net (not gross) expansion.
FORMULA_VERSION = "revcore-1.1.0"

# Human-readable formula per metric id. Rendered in receipts so a CFO can audit the math.
FORMULAS: dict[str, str] = {
    "mrr": "sum(mrr of active accounts in month)",
    "arr": "mrr x 12",
    "active_customers": "count(accounts with mrr > 0 in month)",
    "arpa": "mrr / active_customers",
    "new_customers": "count(accounts whose first paid month is inside the window)",
    "cac": "S&M spend in window / new customers in window",
    "monthly_logo_churn": "mean over window of (accounts lost in month / accounts active prior month)",
    "monthly_net_expansion": "mean over window of ((expansion - contraction) MRR of retained accounts / MRR prior month)",
    "cac_payback_months": "CAC / (ARPA x gross margin)",
    "ltv_wacc": "sum_{t=1..120} ARPA x GM x (1 - churn)^(t-1) / (1 + r)^t, r = (1 + WACC)^(1/12) - 1",
    "ltv_cac": "LTV (WACC-adjusted) / CAC",
    "nrr_12m": "(start MRR + expansion - contraction - churned) / start MRR, cohort active 12 months ago",
    "grr_12m": "min(1, (start MRR - contraction - churned) / start MRR), cohort active 12 months ago",
    "magic_number": "(revenue this Q - revenue last Q) x 4 / S&M spend last Q",
    "net_burn_monthly": "mean over window of (S&M + other opex - MRR x gross margin)",
    "burn_multiple": "net burn in window / net new ARR in window",
    "runway_months": "cash / monthly net burn",
    "sim_arr_end_p50": "median ARR at horizon across seeded Monte Carlo runs",
    "sim_nrr_12m_p50": "median 12-month NRR of starting cohort across seeded runs",
    "sim_p_cash_out": "share of seeded runs where cash <= 0 inside horizon",
    "sim_ltv_cac": "LTV (WACC-adjusted, shocked price + churn) / CAC (shocked)",
    "sim_cac": "shocked S&M monthly / shocked new customers monthly",
    "sim_cac_payback_months": "shocked CAC / (shocked ARPA x gross margin)",
    # analyst team
    "anomaly_excess_spend": "sum over flagged days of (daily spend - median of prior 60 days); flagged when "
                            "(spend - median) / (1.4826 x MAD) > 4",
    "realloc_delta_customers": "sum_c conv_c x e^a_c x proposed_c^b_c - sum_c conv_c x e^a_c x current_c^b_c, "
                               "curves fitted by OLS on log(leads) ~ log(spend)",
    "lead_fast_lane_expected_value": "sum over fast-lane leads of P(convert) x first-year value of the lead's segment",
    "churn_mrr_at_risk": "sum over accounts with P(churn in 3 months) >= 0.25 of P x current MRR",
    "forecast_arr_12m": "12 x MRR forecast at month 12 (driver model or Holt trend, whichever backtests better)",
    "bva_variance": "actual - plan over the fiscal YTD, decomposed into components that sum exactly to it "
                    "(revenue: volume + price + churn + expansion; payroll: timing + open roles + unplanned + "
                    "departures + rates; variable costs: flexed volume + rate)",
    "bva_line": "sum of GL actuals - sum of approved plan for this P&L line, fiscal YTD",
    "growth_yoy": "MRR this month / MRR same month last year - 1",
    "segment_nrr": "MRR now of a segment's accounts active 12 months ago / their MRR then",
    "churn_reason_share": "MRR lost by leavers tagged with this theme / all MRR lost by leavers who answered the exit survey",
    "headcount": "employees with start <= month end and no end date before it",
    "cash": "cash balance at month end (bank)",
    "cash_vs_plan": "actual cash - (cash at plan approval + planned operating income YTD)",
    "at_risk_accounts": "count of accounts with P(churn in 3 months) >= 0.25",
    "churn_arr_at_risk": "12 x sum over at-risk accounts of P(churn) x MRR",
    "channel_payback": "fully loaded channel CAC / (channel ARPA x gross margin)",
    "fast_lane_leads": "open leads routed to the AE fast lane by calibrated score",
    "departures_ytd": "employees whose end date falls in the fiscal year to date",
    "late_hires": "planned hires that started after their planned month",
    "memo_hire_runway": "cash / (net burn + count x salary x 1.22 / 12)",
    "memo_arr_p50": "median ARR at month 24 across 5,000 seeded runs with the proposed change",
    "memo_nrr_p50": "median 12-month NRR across seeded runs with the proposed change",
    "memo_p_cash_out": "share of seeded runs where cash <= 0 within 24 months, with the change",
    "memo_ltv_cac": "WACC-adjusted LTV / CAC with the proposed change",
    "plan_sm_needed": "S&M now x (new customers needed / new customers now) ^ (1 / fitted spend elasticity)",
}


# ---------- Unit economics ----------
def cac(sm_spend: float, new_customers: float) -> float:
    return sm_spend / new_customers if new_customers else float("inf")


def cac_payback_months(cac_value: float, arpa_monthly: float, gross_margin: float) -> float:
    monthly_gp = arpa_monthly * gross_margin
    return cac_value / monthly_gp if monthly_gp > 0 else float("inf")


def ltv_wacc(arpa_monthly, gross_margin, monthly_churn, annual_wacc, horizon_months=120) -> float:
    """Survival-weighted gross-profit stream discounted at WACC."""
    r = (1 + annual_wacc) ** (1 / 12) - 1
    t = np.arange(1, horizon_months + 1)
    survival = (1 - monthly_churn) ** (t - 1)
    return float(np.sum(arpa_monthly * gross_margin * survival / (1 + r) ** t))


def ltv_cac(ltv: float, cac_value: float) -> float:
    return ltv / cac_value if cac_value else float("inf")


# ---------- Retention ----------
def nrr(start_mrr, expansion, contraction, churned) -> float:
    return (start_mrr + expansion - contraction - churned) / start_mrr


def grr(start_mrr, contraction, churned) -> float:
    return min(1.0, (start_mrr - contraction - churned) / start_mrr)


# ---------- Efficiency ----------
def magic_number(rev_q, rev_prev_q, sm_spend_prev_q) -> float:
    return (rev_q - rev_prev_q) * 4 / sm_spend_prev_q if sm_spend_prev_q else float("inf")


def burn_multiple(net_burn, net_new_arr) -> float:
    """0 when not burning (cash-flow positive); inf when burning with no net new ARR."""
    if net_burn <= 0:
        return 0.0
    return net_burn / net_new_arr if net_new_arr > 0 else float("inf")


def rule_of_40(revenue_growth, profit_margin) -> dict:
    score = (revenue_growth + profit_margin) * 100
    return {"score": round(score, 2), "compliant": score >= 40}


def cash_conversion_cycle(dso, dio, dpo) -> float:
    return dso + dio - dpo


def runway_months(cash, monthly_net_burn) -> float:
    return cash / monthly_net_burn if monthly_net_burn > 0 else float("inf")


# ---------- Market sizing ----------
def som(segments: list[dict], sm_budget: float) -> dict:
    """Each segment: name, sam_accounts, acv, cac, budget_share, competitor_sov (0..1)."""
    out, total = [], 0.0
    for s in segments:
        affordable = sm_budget * s["budget_share"] / s["cac"]
        winnable = s["sam_accounts"] * (1 - s["competitor_sov"])
        accounts = min(affordable, winnable)
        value = accounts * s["acv"]
        total += value
        out.append({
            "segment": s["name"], "accounts": round(accounts, 1), "som": round(value, 2),
            "binding_constraint": "budget" if affordable < winnable else "market",
        })
    return {"som_total": round(total, 2), "segments": out}
