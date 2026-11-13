"""compute_metrics against a tiny hand-built ledger (the 'matches a spreadsheet' gate)."""
from datetime import date

import pytest

from app.metrics import TenantData, compute_metrics


def _m(i: int) -> date:
    return date(2025 + (i // 12), i % 12 + 1, 1)


def ledger() -> TenantData:
    # 14 months. A: 100 flat. B: 100 -> 150 at month 13. C: churns at month 13. D: new at month 12.
    months = [_m(i) for i in range(14)]
    mrr = {
        "A": {m: 100.0 for m in months},
        "B": {m: (150.0 if i == 13 else 100.0) for i, m in enumerate(months)},
        "C": {m: 100.0 for m in months[:13]},
        "D": {m: 200.0 for m in months[12:]},
    }
    sm = {m: 300.0 for m in months}
    opex = {m: 200.0 for m in months}
    cash = {months[-1]: 10_000.0}
    return TenantData(tenant_id="t", slug="x", name="X", currency="USD", gross_margin=0.8, annual_wacc=0.0,
                      mrr=mrr, sm=sm, opex=opex, cash=cash)


def test_hand_computed_ledger():
    ms = compute_metrics(ledger())
    r = {k: v.value for k, v in ms.receipts.items()}
    assert r["mrr"] == 450  # A100 + B150 + D200
    assert r["arr"] == 5400
    assert r["active_customers"] == 3
    assert r["arpa"] == 150
    assert r["new_customers"] == 1  # D, inside trailing 3 months
    assert r["cac"] == 900  # 3 x 300 / 1
    # churn per month over window (months 11,12,13): 0, 0, 1/4 -> mean 1/12
    assert r["monthly_logo_churn"] == pytest.approx(1 / 12)
    # cohort at month 1 = A,B,C start 300; end: A100 B150 C0 -> exp 50, churn 100
    assert r["nrr_12m"] == pytest.approx(250 / 300)
    assert r["grr_12m"] == pytest.approx(200 / 300)
    # burn per month = 500 - mrr*0.8 ; window mrr 300, 500, 450
    assert r["net_burn_monthly"] == pytest.approx(((500 - 240) + (500 - 400) + (500 - 360)) / 3)
    assert r["runway_months"] == pytest.approx(10_000 / r["net_burn_monthly"])
    # net expansion over window: m11 0/300, m12 0/300, m13 B +50 on prior 500 -> mean 50/1500
    assert r["monthly_net_expansion"] == pytest.approx((50 / 500) / 3)
    assert ms.warnings == []
    assert ms.baseline.customers == 3


def test_receipts_are_deterministic():
    a = compute_metrics(ledger())
    b = compute_metrics(ledger())
    assert {k: v.id for k, v in a.receipts.items()} == {k: v.id for k, v in b.receipts.items()}
    assert a.snapshot_id == b.snapshot_id


def test_short_history_warns():
    d = ledger()
    d.mrr = {a: {m: v for m, v in s.items() if m >= _m(10)} for a, s in d.mrr.items()}
    d.mrr = {a: s for a, s in d.mrr.items() if s}
    d.months = []
    d.__post_init__()
    ms = compute_metrics(d)
    assert "nrr_12m" not in ms.receipts
    assert any("NRR" in w for w in ms.warnings)
