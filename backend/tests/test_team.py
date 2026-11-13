"""The analyst team must rediscover the structure the generator planted. No database needed."""
import math

import pandas as pd
import pytest
from workspace_fixture import build_workspace

from app import knowledge as kb
from app import team
from app.analysis import planner
from app.seed import LEAD_RESPONSE


@pytest.fixture(scope="module")
def analysis() -> team.Analysis:
    return team.run(build_workspace())


def test_models_learn_real_signal_and_are_calibrated(analysis):
    for card in (analysis.lead_model.card, analysis.churn_model.card):
        assert card.metrics["auc"] > 0.75, card.kind
        assert card.metrics["calibration_error"] < 0.05, card.kind
        assert card.metrics["top_decile_lift"] > 2.5, card.kind


def test_planted_linkedin_anomaly_is_caught(analysis):
    hits = {a["channel"]: a for a in analysis.anomalies}
    assert "linkedin" in hits and hits["linkedin"]["verdict"] == "spend up, leads flat"
    assert set(hits) == {"linkedin"}  # no false alarms on the other channels


def test_response_curves_recover_planted_elasticity(analysis):
    for ch in ("linkedin", "content"):
        assert analysis.curves[ch]["b"] == pytest.approx(LEAD_RESPONSE[ch][1], abs=0.2)
    assert "events" in analysis.realloc["frozen"] or analysis.curves["events"]["r2"] >= 0.3


def test_reallocation_keeps_budget_and_never_loses_customers(analysis):
    r = analysis.realloc
    assert sum(m["proposed"] for m in r["moves"]) <= r["budget"] + 1e-6
    assert r["customers_after"] >= r["customers_before"]


def test_icp_finds_planted_segments(analysis):
    top = {(r["dimension"], r["value"]) for r in analysis.icp if r["significant"] and r["lift"] > 1.2}
    assert ("source_channel", "referral") in top
    assert ("industry", "fintech") in top


def test_reasons_never_contradict_the_data(analysis):
    for reasons in analysis.at_risk["reasons"]:
        assert not any(r.startswith("Usage up") or "MRR up" in r for r in reasons)
    for reasons in analysis.scored_leads["reasons"]:
        assert not any("(0." in r for r in reasons)  # no "0.9x average" sold as a buying signal
    for risks in analysis.scored_leads["risks"]:
        assert not any("50-1,000 band" in r for r in risks)  # the best size band is never a risk


def test_every_finding_has_method_and_actions_and_zero_llm(analysis):
    assert analysis.llm_calls == 0
    for f in analysis.findings:
        assert f.method.kind in {"rule", "statistics", "ml", "optimization"}
        assert f.role in {"marketing_analyst", "lead_gen", "founders_office", "growth", "market_researcher",
                          "business_analyst"}
        assert f.actions


def test_planner_closed_form():
    p = planner.plan(target_arr=12 * 2000, months=10, mrr0=1000, revenue_churn=0.0, net_expansion=0.0,
                     new_customers_now=1, arpa_new=100, sm_monthly=1000, opex_monthly=0, cash=1e9,
                     gross_margin=1.0, spend_elasticity=1.0)
    # need +1000 MRR over 10 months at 100 per customer -> 1 customer/month; spend scales linearly
    assert p["lever_spend"]["new_customers_needed"] == pytest.approx(1.0)
    assert p["lever_spend"]["sm_monthly_needed"] == pytest.approx(1000)
    assert p["out_of_sample"] is False


def test_planner_flags_out_of_sample_goals():
    p = planner.plan(target_arr=12 * 10_000, months=10, mrr0=1000, revenue_churn=0.0, net_expansion=0.0,
                     new_customers_now=1, arpa_new=100, sm_monthly=1000, opex_monthly=0, cash=1e9,
                     gross_margin=1.0, spend_elasticity=1.0, max_sm_observed=1200)
    assert p["out_of_sample"] and not p["feasible_with_spend"]
    assert len(p["warnings"]) == 2 and p["verdict"].startswith("Out of sample")


def test_knowledge_base_is_versioned_and_evaluates():
    st = kb.staleness(pd.Timestamp("2026-11-12").date())
    assert st["benchmarks"]["stale"] is False
    hits = kb.matching_playbooks({"grr_12m": 0.7, "nrr_12m": 0.9, "runway_months": 30, "magic_number": 1.2})
    ids = {h["id"] for h in hits}
    assert {"revenue_leak", "underinvesting"} <= ids and "runway_short" not in ids
    assert not math.isnan(kb.position("nrr_12m", 0.9, "growth")["target"])


def test_language_model_jobs_find_planted_truth(analysis):
    assert not analysis.warnings
    assert analysis.voc["churn_reasons"][0]["theme"] == "missing_integration"
    ids = {f.id for f in analysis.findings}
    assert {"segment_best", "churn_reason"} <= ids


def test_team_degrades_gracefully_with_billing_only():
    """A company that has only connected Stripe: nothing crashes, every skipped job says what it needs."""
    ws = build_workspace()
    empty = lambda df: df.iloc[0:0]
    ws.leads, ws.usage, ws.spend_daily, ws.feedback = empty(ws.leads), empty(ws.usage), empty(ws.spend_daily), empty(ws.feedback)
    ws.accounts = ws.accounts.assign(description=None)
    a = team.run(ws)
    assert a.lead_model is None and a.churn_model is None and a.segments is None and a.voc is None
    skipped = " ".join(a.warnings)
    for need in ("CRM", "Product usage", "Ad spend", "Company descriptions", "exit survey"):
        assert need in skipped
    assert any(f.id == "forecast" for f in a.findings)  # billing alone still powers the forecast
    assert len(a.scored_leads) == 0 and len(a.at_risk) == 0
