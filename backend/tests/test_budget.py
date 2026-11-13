"""Budget maths: revenue roll-forward, staff and hire costs, the assembled plan, and Fred's review checks."""
from datetime import date

import pytest

from app import budget

CTX = {
    "last_closed": date(2026, 10, 1), "loaded_factor": 1.2, "cash": 3_000_000.0,
    "accounts": [
        {"code": "4000", "name": "Subscription revenue", "pl_group": "revenue", "kind": "revenue", "department": None,
         "last12": 2_400_000.0, "last3": 650_000.0},
        {"code": "5000", "name": "Hosting", "pl_group": "cogs", "kind": "cogs", "department": None, "last12": 240_000.0, "last3": 66_000.0},
        {"code": "6100", "name": "Advertising", "pl_group": "sm", "kind": "nonpayroll", "department": None, "last12": 300_000.0, "last3": 75_000.0},
        {"code": "6000", "name": "Salaries: Sales", "pl_group": "sm", "kind": "payroll", "department": "Sales", "last12": 600_000.0, "last3": 150_000.0},
        {"code": "6010", "name": "Salaries: Engineering", "pl_group": "rnd", "kind": "payroll", "department": "Engineering", "last12": 1_200_000.0, "last3": 300_000.0},
    ],
    "staff": [
        {"name": "A", "title": "AE", "department": "Sales", "start_date": date(2024, 1, 1), "end_date": None, "salary_annual": 120_000.0},
        {"name": "B", "title": "AE", "department": "Sales", "start_date": date(2025, 1, 1), "end_date": date(2027, 4, 15), "salary_annual": 100_000.0},
        {"name": "C", "title": "Eng", "department": "Engineering", "start_date": date(2023, 1, 1), "end_date": None, "salary_annual": 180_000.0},
    ],
}


def test_revenue_path_rolls_forward_from_latest_month():
    rev = {"start_mrr": 200_000.0, "start_month": "2026-10-01", "new_customers": 10, "arpa_new": 1_000,
           "revenue_churn": 0.02, "net_expansion": 0.01}
    path = budget.revenue_path(rev, 2027)
    # Nov and Dec 2026 happen before the plan year starts
    m = 200_000.0
    for _ in range(3):
        m = m * 0.99 + 10_000
    assert path[0] == pytest.approx(m, rel=1e-9)
    assert len(path) == 12 and path[-1] > path[0]


def test_staff_cost_respects_end_dates_and_loading():
    sales = budget.staff_cost(CTX["staff"], "Sales", 2027, 1.2)
    assert sales[0] == pytest.approx((120_000 + 100_000) * 1.2 / 12)  # both in January
    assert sales[3] == pytest.approx((120_000 + 100_000) * 1.2 / 12)  # B leaves mid-April: April still paid
    assert sales[4] == pytest.approx(120_000 * 1.2 / 12)  # May: only A


def test_hire_cost_starts_in_planned_month():
    c = budget.hire_cost([{"role": "AE", "planned_start": "2027-07-01", "salary_annual": 120_000}], 2027, 1.2)
    assert c[5] == 0 and c[6] == pytest.approx(12_000) and sum(c) == pytest.approx(72_000)


def test_prefill_uses_run_rate_and_assignments():
    lines = budget.prefill(CTX, {"5000": "Engineering", "6100": "Marketing"})
    assert lines["Marketing"] == [{"account": "6100", "monthly": budget.even(300_000)}]
    assert lines["Engineering"][0]["account"] == "5000" and sum(lines["Engineering"][0]["monthly"]) == pytest.approx(264_000, abs=1)
    assert lines["Sales"] == []  # payroll is computed from people, not typed in


def rnd(**rev):
    return {"id": "r1", "fiscal_year": 2027, "name": "FY2027 budget",
            "revenue": {"start_mrr": 220_000.0, "start_month": "2026-10-01", "new_customers": 8, "arpa_new": 1_200,
                        "revenue_churn": 0.015, "net_expansion": 0.008} | rev,
            "assignments": {"5000": "Engineering", "6100": "Marketing"},
            "baseline": budget.baseline(CTX)}


def subs(extra_hires=()):
    return [{"department": "Sales", "lines": [], "hires": list(extra_hires)},
            {"department": "Marketing", "lines": [{"account": "6100", "monthly": budget.even(360_000)}], "hires": []},
            {"department": "Engineering", "lines": [{"account": "5000", "monthly": budget.even(264_000)}], "hires": []}]


def test_assemble_ties_out():
    p = budget.assemble(rnd(), subs([{"role": "AE", "planned_start": "2027-04-01", "salary_annual": 110_000}]), CTX)
    s = p["summary"]
    assert s["revenue"] == pytest.approx(sum(p["revenue"]))
    assert s["operating_income"] == pytest.approx(s["revenue"] - s["cogs"] - s["sm"] - s["rnd"] - s["ga"])
    # Sales payroll lands on the Sales payroll account; engineering on engineering's
    assert sum(p["lines"]["6000"]) == pytest.approx(p["departments"]["Sales"]["payroll"])
    assert p["departments"]["Sales"]["hires"] == 1
    assert p["departments"]["Sales"]["headcount_end"] == 2  # A stays, B leaves in April, +1 hire
    assert s["revenue_account"] == "4000"


def test_checks_flag_spend_outrunning_revenue_and_odd_salaries():
    big = subs()
    big[1]["lines"] = [{"account": "6100", "monthly": budget.even(1_500_000)}]
    big[0]["hires"] = [{"role": "AE", "planned_start": "2027-03-01", "salary_annual": 400_000}]
    plan = budget.assemble(rnd(new_customers=0), big, CTX)
    texts = [c["text"] for c in budget.checks(rnd(new_customers=0), plan, CTX, big)]
    assert any("Marketing" in c["department"] and "Spend grows" in c["text"]
               for c in budget.checks(rnd(new_customers=0), plan, CTX, big) if c["department"])
    assert any("far from the team's median" in t for t in texts)


def test_checks_warn_when_cash_runs_out():
    broke = dict(CTX, cash=100_000.0)
    big = subs()
    big[1]["lines"] = [{"account": "6100", "monthly": budget.even(3_000_000)}]
    plan = budget.assemble(rnd(), big, broke)
    assert any(c["level"] == "bad" and "runs out of cash" in c["text"] for c in budget.checks(rnd(), plan, broke, big))
