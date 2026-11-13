"""revcore against hand-computed fixtures and the blueprint's reference run."""
import math

import pytest

from app import revcore as rc

BASE = rc.Baseline(customers=400, arpa_monthly=500, gross_margin=0.78, monthly_churn=0.02,
                   monthly_expansion=0.012, new_customers_monthly=30, sm_spend_monthly=90_000,
                   other_opex_monthly=150_000, cash=3_000_000, annual_wacc=0.14)


def test_unit_economics_hand_computed():
    assert rc.cac(90_000, 30) == 3_000
    assert rc.cac(1, 0) == math.inf
    assert rc.cac_payback_months(3_000, 500, 0.78) == pytest.approx(7.6923, rel=1e-4)
    assert rc.ltv_cac(12_000, 3_000) == 4


def test_ltv_zero_wacc_zero_churn_is_undiscounted_sum():
    assert rc.ltv_wacc(100, 1.0, 0.0, 0.0, horizon_months=12) == pytest.approx(1200)


def test_retention():
    assert rc.nrr(100, 20, 5, 10) == pytest.approx(1.05)
    assert rc.grr(100, 5, 10) == pytest.approx(0.85)
    assert rc.grr(100, -50, 0) == 1.0  # capped


def test_efficiency():
    assert rc.magic_number(330, 300, 120) == pytest.approx(1.0)
    assert rc.burn_multiple(150, 100) == 1.5
    assert rc.burn_multiple(150, 0) == math.inf
    assert rc.burn_multiple(-50, 100) == 0.0  # cash-flow positive is not a negative multiple
    assert rc.rule_of_40(0.3, 0.12) == {"score": 42.0, "compliant": True}
    assert rc.runway_months(1_200, 100) == 12


def test_som_reports_binding_constraint():
    out = rc.som([{"name": "fin", "sam_accounts": 2000, "acv": 6000, "cac": 4000,
                   "budget_share": 0.6, "competitor_sov": 0.55}], sm_budget=1_080_000)
    seg = out["segments"][0]
    assert seg["accounts"] == pytest.approx(162.0)
    assert seg["binding_constraint"] == "budget"


def test_simulation_is_reproducible():
    a = rc.simulate(BASE, rc.Shock(price_change=0.2), runs=500, seed=1)
    b = rc.simulate(BASE, rc.Shock(price_change=0.2), runs=500, seed=1)
    assert a == b


def test_blueprint_reference_run():
    r = rc.compare(BASE, rc.Shock(price_change=0.20, churn_multiplier=1.15))
    b, s = r["baseline"], r["scenario"]
    assert b["ltv_cac_wacc"] == pytest.approx(4.10, abs=0.01)
    assert s["ltv_cac_wacc"] == pytest.approx(3.97, abs=0.01)
    assert b["cac"] == pytest.approx(3_000)
    assert s["cac"] == pytest.approx(3_409, abs=1)
    assert b["nrr_12m"]["p50"] == pytest.approx(0.91, abs=0.01)
    assert s["nrr_12m"]["p50"] == pytest.approx(0.88, abs=0.01)
    assert s["arr_end"]["p50"] / b["arr_end"]["p50"] == pytest.approx(1.05, abs=0.01)
