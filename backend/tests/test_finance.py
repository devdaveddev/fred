"""Budget vs actual: exact bridges, and every planted finance story found and correctly labelled."""
import pandas as pd
import pytest
from finance_fixture import build

from app.finance.bva import classify, headcount, review


@pytest.fixture(scope="module")
def data():
    fd, mrr, g = build()
    return fd, mrr, g, review(fd, mrr)


def finding(r, key):
    return next(f for f in r["findings"] if f["key"] == key)


def test_pnl_ties_to_general_ledger(data):
    fd, _, _, r = data
    ytd = fd.actual[fd.actual["month"].isin(pd.to_datetime(r["ytd_months"]))]
    rev = next(x for x in r["pnl"] if x["key"] == "revenue")
    assert rev["actual_ytd"] == pytest.approx(ytd[ytd["account"] == "4000"]["amount"].sum())
    oi = next(x for x in r["pnl"] if x["key"] == "operating_income")
    costs = ytd[ytd["account"] != "4000"]["amount"].sum()
    assert oi["actual_ytd"] == pytest.approx(rev["actual_ytd"] - costs)


def test_revenue_bridge_is_exact(data):
    _, _, _, r = data
    rev = next(x for x in r["pnl"] if x["key"] == "revenue")
    assert r["revenue_bridge"]["ytd_total"] == pytest.approx(rev["var_ytd"], abs=1.0)
    month = r["revenue_bridge"]["month"]
    assert sum(month[k] for k in ("volume", "price", "churn", "expansion")) == pytest.approx(rev["var_m"], abs=1.0)


def test_payroll_bridge_is_exact_per_department(data):
    _, _, _, r = data
    for dept, d in r["payroll"]["departments"].items():
        assert sum(d["components"].values()) == pytest.approx(d["variance"], abs=1.0), dept


def test_cash_reconciles_to_operating_variance(data):
    _, _, _, r = data
    c = r["cash"]
    assert c["actual"] == pytest.approx(c["plan"] + c["operating_variance"], abs=1.0)


def test_planted_stories_are_found_and_labelled(data):
    _, _, _, r = data
    rev = finding(r, "revenue")
    assert rev["components"]["volume"] < 0 and rev["components"]["churn"] < 0  # fewer logos, more churn than plan
    assert finding(r, "8100")["components"]["pattern"] == "run_rate"  # software creep
    assert finding(r, "8100")["components"]["since"] == "2026-06-01"
    assert finding(r, "8300")["components"]["pattern"] == "one_off"  # fundraising legal
    assert finding(r, "8300")["components"]["month"] == "2026-03-01"
    hosting = finding(r, "5000")["components"]
    assert hosting["pattern"] == "run_rate" and hosting["since"] == "2026-08-01" and hosting["rate"] > 0
    eng = finding(r, "payroll_Engineering")["components"]
    assert eng["departure"] < 0 and eng["unplanned"] > 0 and eng["late_start"] < 0
    assert eng["pay_rate"] > 0  # 4% raises vs 3% planned
    assert finding(r, "payroll_G&A")["components"]["cancelled"] < 0


def test_classify_rules():
    idx = pd.date_range("2026-01-01", periods=6, freq="MS")
    plan = pd.Series(100.0, index=idx)
    assert classify(pd.Series([0, 0, 90, 0, 1, 0], index=idx), plan)["pattern"] == "one_off"
    rr = classify(pd.Series([0, 0, 0, 30, 30, 30.0], index=idx), plan)
    assert rr["pattern"] == "run_rate" and rr["since"] == "2026-04-01" and rr["annualized"] == pytest.approx(360)
    assert classify(pd.Series([5, -6, 4, -5, 6, 4.0], index=idx), plan)["pattern"] == "scattered"


def test_headcount_status(data):
    fd, _, _, _ = data
    h = headcount(fd, fd.closed_months[-1])
    statuses = [x["status"] for x in h["plan_hires"]]
    assert "cancelled" in statuses and any(s.startswith("late_") for s in statuses)
    assert h["active"] == sum(h["by_department"].values())
