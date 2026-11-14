"""The analyst team: each role is a set of jobs, each job uses the cheapest method that works.

    rule / closed-form  ->  statistics  ->  trained ML  ->  (LLM only for language, never numbers)

Output is a ranked list of findings. Every finding states its method, carries receipts for its
numbers and ends in concrete actions. The whole run makes zero LLM calls.
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import knowledge as kb
from .analysis import forecast as fc
from .analysis import marketing as mk
from .analysis import planner
from .config import get_settings
from .ml import churn as churn_ml
from .ml import leads as lead_ml
from .provenance import Receipt, save_receipts
from .workspace import Workspace

ROLES = {
    "marketing_analyst": {"label": "Marketing analyst", "replaces": "Channel reporting, budget allocation, spend QA"},
    "lead_gen": {"label": "Lead generation", "replaces": "Lead research, scoring, triage and routing"},
    "founders_office": {"label": "Strategy & finance",
                        "replaces": "BizOps hire: budget vs actual, forecast, headcount plan, board metrics"},
    "growth": {"label": "Growth strategist", "replaces": "Retention diagnosis, playbooks, benchmarking"},
}
SEVERITY = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
CHANNEL_LABEL = {"paid_search": "Paid search", "linkedin": "LinkedIn", "events": "Events", "content": "Content",
                 "organic": "Organic", "referral": "Referral", "sales_team": "Sales team"}


def ch_label(c: str) -> str:
    return CHANNEL_LABEL.get(c, c.replace("_", " ").title())


@dataclass
class Method:
    kind: str  # rule | statistics | ml | optimization
    detail: str
    model_version: str | None = None


@dataclass
class Finding:
    id: str
    role: str
    severity: str
    title: str
    summary: str
    method: Method
    actions: list[str]
    impact_usd: float | None = None
    impact_label: str | None = None
    receipts: list[str] = field(default_factory=list)
    link: str | None = None
    frameworks: list[str] = field(default_factory=list)


@dataclass
class Analysis:
    snapshot_id: str
    as_of: str
    lead_model: lead_ml.LeadModel | None
    scored_leads: pd.DataFrame
    icp: list[dict]
    churn_model: churn_ml.ChurnModel | None
    at_risk: pd.DataFrame
    channels: list[dict]
    curves: dict
    realloc: dict
    anomalies: list[dict]
    forecast: dict
    facts: dict[str, Any]
    stage: dict
    benchmarks: list[dict]
    findings: list[Finding]
    receipts: list[Receipt]
    timings_ms: dict[str, float]
    arpa_new: float
    segments: dict | None = None
    voc: dict | None = None
    readiness: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    llm_calls: int = 0


def _money(v: float) -> str:
    return f"${v / 1e6:.2f}M" if abs(v) >= 1e6 else (f"${v / 1e3:.1f}K" if abs(v) >= 1e4 else f"${v:,.0f}")


LEAD_COLS = ["lead_id", "company", "contact_name", "industry", "employees", "country", "seniority", "source_channel",
             "created_at", "demo_requested", "pricing_views", "p", "expected_value", "first_year_value", "segment",
             "lane", "next_action", "reasons", "risks"]
RISK_COLS = ["account_id", "name", "segment", "industry", "acquisition_channel", "mrr", "p", "mrr_at_risk",
             "util_now", "util_trend", "tenure_months", "reasons", "play"]
PAID = ["paid_search", "linkedin", "events", "content"]


def readiness(ws: Workspace) -> dict[str, dict]:
    """What each job needs, and whether this workspace has it. Jobs without data are skipped, not crashed."""
    mrr_months = len(ws.data.months)
    leads = ws.leads
    closed = leads[leads["status"] != "open"] if len(leads) else leads
    conv = int((closed["status"] == "converted").sum()) if len(closed) else 0
    lost = int((closed["status"] == "lost").sum()) if len(closed) else 0
    usage_months = ws.usage["week"].dt.to_period("M").nunique() if len(ws.usage) else 0
    paid_days = ws.spend_daily[ws.spend_daily["channel"].isin(PAID)]["day"].nunique() if len(ws.spend_daily) else 0
    described = int(ws.accounts["description"].notna().sum()) if "description" in ws.accounts else 0

    def item(ok: bool, need: str, have: str) -> dict:
        return {"ok": bool(ok), "needs": need, "have": have}

    return {
        "metrics": item(mrr_months >= 2, "Billing: 2+ months of subscription revenue (Stripe or billing CSV)",
                        f"{mrr_months} months"),
        "forecast": item(mrr_months >= 9, "Billing: 9+ months of history", f"{mrr_months} months"),
        "lead_model": item(len(closed) >= 200 and conv >= 20 and lost >= 20,
                           "CRM: 200+ closed leads with at least 20 won and 20 lost (HubSpot)",
                           f"{len(closed)} closed, {conv} won"),
        "churn_model": item(usage_months >= 7 and len(ws.usage) >= 500,
                            "Product usage: 7+ months of weekly active users per account",
                            f"{usage_months} months"),
        "marketing": item(paid_days >= 120, "Ad spend by channel and day: 4+ months (ad spend CSV)",
                          f"{paid_days} days"),
        "segments": item(described >= 50, "Company descriptions for 50+ customers (HubSpot companies)",
                         f"{described} described"),
        "voice": item(len(ws.feedback) >= 20, "20+ exit survey or NPS comments (survey CSV)",
                      f"{len(ws.feedback)} comments"),
    }


def _forecast_findings(findings: list, fcast: dict, the_plan: dict, goal: dict, receipt) -> None:
    drv = fcast["drivers"]
    f12 = fcast["forecast"][-1]
    rid = receipt("forecast_arr_12m", f12["mrr"] * 12, method=fcast["method"], backtest_mape=fcast["backtest_mape"],
                  drivers=drv)
    findings.append(Finding(
        id="forecast", role="founders_office", severity="info",
        title=f"On the current trajectory ARR reaches {_money(f12['mrr'] * 12)} in 12 months",
        summary=f"Range {_money(f12['low'] * 12)} to {_money(f12['high'] * 12)}. Method chosen by backtest "
                f"({fcast['method']}, {fcast['backtest_mape'][fcast['method']]:.1%} error on the last 6 months).",
        method=Method("statistics", "Driver model vs Holt trend, best backtest wins"),
        actions=["Use this as the base case in the board pack"], receipts=[rid], link="/plan"))
    lp = the_plan["lever_spend"]
    rid = receipt("plan_sm_needed", lp["sm_monthly_needed"], target_arr=goal["target_arr"], months=goal["months"],
                  new_customers_needed=lp["new_customers_needed"], elasticity=lp["spend_elasticity"])
    findings.append(Finding(
        id="goal_plan", role="founders_office",
        severity="info" if the_plan["gap"] <= 0 else ("medium" if the_plan["feasible_with_spend"] else "high"),
        title=f"{_money(goal['target_arr'])} ARR in {goal['months']} months is " + (
            "on track." if the_plan["gap"] <= 0 else
            "beyond what your data supports." if the_plan.get("out_of_sample") else
            "reachable with more spend." if the_plan["feasible_with_spend"] else "not fundable on current cash."),
        summary=f"{the_plan['verdict']} Needs {lp['new_customers_needed']:.0f} new customers a month (today {lp['new_customers_now']:.0f}) "
                f"and about {_money(lp['sm_monthly_needed'])}/mo of S&M (today {_money(lp['sm_monthly_now'])})."
                + (f" Cash runs out in month {lp['cash_out_month']}." if lp["cash_out_month"] else ""),
        method=Method("rule", "Closed-form cohort projection + fitted spend elasticity + cash path"),
        actions=["Open the Plan page to change the goal or timeline"], receipts=[rid], link="/plan"))



def run(ws: Workspace, goal: dict | None = None) -> Analysis:
    t0, timings = time.perf_counter(), {}

    def lap(name: str) -> None:
        nonlocal t0
        now = time.perf_counter()
        timings[name] = round((now - t0) * 1000, 1)
        t0 = now

    snap, ms = ws.snapshot_id, ws.metrics
    as_of = pd.Timestamp(ms.as_of)
    R = ms.receipts
    val = lambda k: R[k].value if k in R else None
    receipts: list[Receipt] = []

    def receipt(metric: str, value: float, model_version: str | None = None, **inputs) -> str:
        r = Receipt(metric=metric, value=value, snapshot_id=snap, inputs={"as_of": str(ms.as_of), **inputs},
                    model_version=model_version)
        receipts.append(r)
        return r.id

    ready = readiness(ws)
    warnings: list[str] = [f"Skipped {k.replace('_', ' ')}: needs {v['needs']} (have {v['have']})."
                           for k, v in ready.items() if not v["ok"] and k != "metrics"]
    mrr = ws.mrr_frame()
    gm, wacc = ws.data.gross_margin, ws.data.annual_wacc
    first = mrr[mrr["mrr"] > 0].groupby("account_id")["month"].min()
    recent_new = first[first >= as_of - pd.DateOffset(months=5)].index
    arpa_new = float(mrr[mrr["account_id"].isin(recent_new)].groupby("account_id")["mrr"].first().mean()) \
        if len(recent_new) else float(mrr[mrr["month"] == as_of]["mrr"].mean() or 0.0)
    seg_value = (mrr[mrr["month"] == as_of].merge(ws.accounts[["account_id", "segment"]], on="account_id")
                 .groupby("segment")["mrr"].mean() * 12).to_dict()

    # ---------------- lead generation (ML) ----------------
    lm, scored, icp = None, pd.DataFrame(columns=LEAD_COLS), []
    if ready["lead_model"]["ok"]:
        lm = lead_ml.train(ws.leads, snap)
        thresholds = kb.benchmarks()["routing"]
        scored = lead_ml.score_open(lm, ws.leads, seg_value, thresholds)
        if scored.empty:
            scored = pd.DataFrame(columns=LEAD_COLS)
        icp = lead_ml.icp_table(ws.leads)
    lap("lead_model")

    # ---------------- churn (ML) ----------------
    cm, at_risk = None, pd.DataFrame(columns=RISK_COLS)
    if ready["churn_model"]["ok"]:
        pnl = churn_ml.panel(ws.usage, mrr, ws.accounts)
        if int(pnl.loc[pnl["observable"], "y"].sum()) >= 15:
            cm = churn_ml.train(pnl, snap)
            at_risk = churn_ml.score_current(cm, pnl, ws.accounts)
        else:
            warnings.append("Skipped churn model: fewer than 15 churn events in the usage history so far.")
    lap("churn_model")

    # ---------------- marketing (statistics + optimisation) ----------------
    channels, curves, anomalies = [], {}, []
    realloc: dict = {"moves": [], "frozen": [], "delta_customers_monthly": 0.0, "budget": 0.0}
    if ready["marketing"]["ok"]:
        channels = mk.channel_economics(ws.leads, ws.accounts, mrr, ws.spend_daily, as_of, gm,
                                        val("monthly_logo_churn") or 0.02, wacc)
        if len(ws.leads):
            curves = mk.response_curves(ws.leads, ws.spend_daily)
            realloc = mk.reallocate(curves)
        anomalies = mk.anomalies(ws.spend_daily, ws.leads)
    lap("marketing")

    # ---------------- founder's office (statistics + closed form) ----------------
    fcast, the_plan = None, None
    arr_now = val("arr") or 0.0
    goal = goal or {"target_arr": round(arr_now * 2, -5), "months": 18}
    if ready["forecast"]["ok"] and ms.baseline is not None:
        fcast = fc.forecast(mrr)
        drv = fcast["drivers"]
        elasticities = [c["b"] for c in curves.values() if c["r2"] >= 0.3]
        new_now = (val("new_customers") or 0) / 3
        the_plan = planner.plan(target_arr=goal["target_arr"], months=goal["months"], mrr0=val("mrr") or 0.0,
                                revenue_churn=drv["revenue_churn"], net_expansion=drv["net_expansion"],
                                new_customers_now=new_now, arpa_new=arpa_new,
                                sm_monthly=ms.baseline.sm_spend_monthly, opex_monthly=ms.baseline.other_opex_monthly,
                                cash=ms.baseline.cash, gross_margin=gm,
                                spend_elasticity=float(np.mean(elasticities)) if elasticities else 0.7,
                                max_sm_observed=max(ws.data.sm.values()) if ws.data.sm else None)
    lap("planning")

    # ---------------- facts + knowledge base ----------------
    cur_mrr = mrr[mrr["month"] == as_of].sort_values("mrr", ascending=False)
    top10 = float(cur_mrr["mrr"].head(10).sum() / cur_mrr["mrr"].sum()) if len(cur_mrr) else 0.0
    yr_ago = mrr[mrr["month"] == as_of - pd.DateOffset(months=12)]["mrr"].sum()
    growth_yoy = float(cur_mrr["mrr"].sum() / yr_ago - 1) if yr_ago else None
    fcf_margin = -(val("net_burn_monthly") or 0) * 12 / arr_now if arr_now else None
    at_risk_mrr = float(at_risk["mrr_at_risk"].sum()) if len(at_risk) else 0.0
    facts = {k: val(k) for k in R} | {
        "top10_share": top10, "growth_yoy": growth_yoy, "fcf_margin": fcf_margin,
        "rule_of_40": (growth_yoy + fcf_margin) if growth_yoy is not None and fcf_margin is not None else None,
        "at_risk_share": at_risk_mrr / float(cur_mrr["mrr"].sum()) if len(cur_mrr) else 0.0,
    }
    stage = kb.stage_for(arr_now)
    positions = [p for p in (kb.position(m, facts.get(m), stage["id"]) for m in kb.benchmarks()["metrics"]) if p]
    findings: list[Finding] = []

    # ---- marketing analyst findings
    for a in anomalies:
        pct = a["recent_daily"] / a["typical_daily"] - 1
        rid = receipt("anomaly_excess_spend", a["excess_spend"], channel=a["channel"], since=a["since"],
                      days=a["days"], typical_daily=a["typical_daily"], recent_daily=a["recent_daily"], max_z=a["max_z"])
        flat = a["verdict"] == "spend up, leads flat"
        findings.append(Finding(
            id=f"anomaly_{a['channel']}", role="marketing_analyst", severity="high" if flat else "medium",
            title=f"{ch_label(a['channel'])} spend is {pct:.0%} above normal for {a['days']} days"
                  + (", with no lift in leads" if flat else ""),
            summary=f"Daily spend averaged {_money(a['recent_daily'])} against a typical {_money(a['typical_daily'])} "
                    f"since {a['since']}. That is {_money(a['excess_spend'])} above normal"
                    + (f", while leads ran at {a['lead_ratio']:.1f}x their usual rate." if a['lead_ratio'] is not None else "."),
            method=Method("statistics", f"Robust z-score (median/MAD) on daily spend, peak z = {a['max_z']:.0f}"),
            actions=["Check bids, budgets and audience settings on this channel today",
                     "Pause the change if it was not intended; annotate it if it was"],
            impact_usd=a["excess_spend"], impact_label="spent above normal", receipts=[rid], link="/marketing"))
    if realloc.get("delta_customers_monthly", 0) >= 0.5:
        ups = [m for m in realloc["moves"] if m["change"] > 250]
        downs = [m for m in realloc["moves"] if m["change"] < -250]
        moved = sum(m["change"] for m in ups)
        new_arr = realloc["delta_customers_monthly"] * arpa_new * 12
        rid = receipt("realloc_delta_customers", realloc["delta_customers_monthly"],
                      budget=realloc["budget"], moves={m["channel"]: m["change"] for m in realloc["moves"]})
        findings.append(Finding(
            id="budget_reallocation", role="marketing_analyst", severity="medium",
            title=f"Moving {_money(moved)}/month between channels adds about "
                  f"{realloc['delta_customers_monthly']:.1f} customers a month at the same budget",
            summary="Each channel's lead response to spend was fitted from your history. Reallocation follows marginal "
                    "cost per customer, within 0.5x to 1.75x of today's spend per channel.",
            method=Method("optimization", "Log-log response curves per channel + greedy marginal allocation"),
            actions=[f"Shift {_money(-m['change'])}/mo out of {ch_label(m['channel'])}" for m in downs]
                    + [f"Add {_money(m['change'])}/mo to {ch_label(m['channel'])}" for m in ups],
            impact_usd=new_arr, impact_label="extra new ARR per month", receipts=[rid], link="/marketing"))

    # ---- lead gen findings
    fast = scored[scored["lane"] == "fast_lane"]
    if lm is not None and len(fast):
        ev = float(fast["expected_value"].sum())
        rid = receipt("lead_fast_lane_expected_value", ev, model_version=lm.card.version, leads=len(fast),
                      auc=lm.card.metrics["auc"])
        top = ", ".join(f"{r.company} ({r.p:.0%})" for r in fast.head(3).itertuples())
        findings.append(Finding(
            id="fast_lane", role="lead_gen", severity="high",
            title=f"{len(fast)} open leads belong in the AE fast lane",
            summary=f"Highest expected value: {top}. Each lead has its three strongest buying signals listed.",
            method=Method("ml", f"Lead model, holdout AUC {lm.card.metrics['auc']:.2f}, "
                                f"top-decile lift {lm.card.metrics['top_decile_lift']:.1f}x", lm.card.version),
            actions=["Route to AEs now; first touch within 1 business hour",
                     f"{int((scored['lane'] == 'sdr').sum())} more go to SDR sequences, the rest to nurture"],
            impact_usd=ev, impact_label="expected first-year revenue", receipts=[rid], link="/leads"))
    sig = [r for r in icp if r["significant"]]
    if sig:
        best = [r for r in sig if r["lift"] > 1][:3]
        worst = [r for r in sorted(sig, key=lambda r: r["lift"]) if r["lift"] < 1][:2]
        desc = lambda r: f"{r['value'].replace('_', ' ')} {r['dimension'].replace('_', ' ')}"
        findings.append(Finding(
            id="icp", role="lead_gen", severity="medium",
            title="Your real ICP: " + ", ".join(f"{desc(r)} ({r['lift']:.1f}x)" for r in best),
            summary="Conversion by segment, tested against the rest of the funnel (two-proportion z-test, |z| >= 2). "
                    + ("Weakest: " + ", ".join(f"{desc(r)} ({r['lift']:.1f}x)" for r in worst) + "." if worst else ""),
            method=Method("statistics", "Segment conversion rates with significance testing"),
            actions=["Point outbound lists and paid targeting at the top segments",
                     "Stop paying for the weakest segments or route them to self-serve"],
            link="/leads"))

    # ---- founder's office findings
    if fcast is not None:
        _forecast_findings(findings, fcast, the_plan, goal, receipt)

    # ---- growth findings (ML)
    risky = at_risk[at_risk["p"] >= 0.25] if len(at_risk) else at_risk
    if cm is not None and len(risky):
        rid = receipt("churn_mrr_at_risk", float(risky["mrr_at_risk"].sum()), model_version=cm.card.version,
                      accounts=len(risky), auc=cm.card.metrics["auc"])
        names = ", ".join(f"{r.name} ({r.p:.0%})" for r in risky.head(3).itertuples())
        findings.append(Finding(
            id="churn_risk", role="growth", severity="high",
            title=f"{len(risky)} accounts are likely to churn within 3 months",
            summary=f"Largest exposure: {names}. Each has its top risk reasons and a suggested play.",
            method=Method("ml", f"Churn model, holdout AUC {cm.card.metrics['auc']:.2f}", cm.card.version),
            actions=["Assign owners to the top 10 today", "Run the suggested play per account"],
            impact_usd=float(risky["mrr_at_risk"].sum()) * 12, impact_label="ARR at risk", receipts=[rid],
            link="/accounts"))

    # ---- pre-trained language model jobs (market researcher + voice of customer)
    segs, voc_out = None, None
    try:
        from .ml.embeddings import MODEL_NAME, embed
        from .research import segments as seg_mod
        from .research import voc as voc_mod

        if ready["segments"]["ok"]:
            segs = seg_mod.discover(ws.accounts, mrr, ws.leads, as_of, embed)
        if ready["voice"]["ok"]:
            voc_out = voc_mod.analyse(ws.feedback, mrr, as_of, embed)
    except Exception as e:  # model missing or failed: the rest of the team still works
        warnings.append(f"Language-model jobs unavailable: {e}")
    lap("language_model")

    pre = f"Pre-trained {MODEL_NAME}" if segs or voc_out else ""
    if segs and segs["best"]:
        b, w = segs["best"], segs["worst"]
        rid = receipt("segment_score", b["score"], model_version=MODEL_NAME, segment=b["name"], nrr_12m=b["nrr_12m"],
                      avg_mrr=b["avg_mrr"], customers=b["customers"], k=segs["k"], silhouette=segs["silhouette"])
        findings.append(Finding(
            id="segment_best", role="market_researcher", severity="medium",
            title=f"Your strongest segment is '{b['name']}'. Win more of them.",
            summary=f"{b['customers']} customers who keep {b['nrr_12m']:.0%} of their revenue after a year and pay "
                    f"{b['avg_mrr'] / max(1e-9, np.mean([s['avg_mrr'] for s in segs['segments'] if s['customers']])):.1f}x "
                    f"the average. Found by grouping customers on how they describe their own business.",
            method=Method("ml", f"{pre} embeddings + k-means ({segs['k']} segments)", MODEL_NAME),
            actions=["Point outbound and paid targeting at companies like these",
                     f"Work the {len(segs['lookalikes'])} open leads that look most like this segment"],
            impact_usd=b["mrr"] * 12, impact_label="ARR today in this segment", receipts=[rid], link="/market"))
        if w is not b and w["nrr_12m"] < 0.85:
            findings.append(Finding(
                id="segment_worst", role="market_researcher", severity="medium",
                title=f"'{w['name']}' customers rarely stay. Stop paying to acquire them.",
                summary=f"They keep only {w['nrr_12m']:.0%} of revenue after a year, and pay below-average prices.",
                method=Method("ml", f"{pre} embeddings + k-means", MODEL_NAME),
                actions=["Exclude this segment from paid targeting", "Route these leads to self-serve, not sales"],
                impact_usd=w["mrr"] * 12, impact_label="ARR at risk in this segment", link="/market"))
    if voc_out and voc_out["churn_reasons"]:
        top = voc_out["churn_reasons"][0]
        rid = receipt("churn_reason_share", top["mrr_share"], model_version=MODEL_NAME, theme=top["label"],
                      responses=top["responses"], exit_responses=voc_out["exit_responses"])
        findings.append(Finding(
            id="churn_reason", role="business_analyst", severity="high" if top["mrr_share"] >= 0.3 else "medium",
            title=f"{top['label']} is the main reason customers leave.",
            summary=f"It explains {top['mrr_share']:.0%} of churned revenue across {voc_out['exit_responses']} exit "
                    f"surveys. Customers say: \"{top['quotes'][0]}\"",
            method=Method("ml", f"{pre} zero-shot theme tagging (themes v{voc_out['themes_version']})", MODEL_NAME),
            actions=[top["action"], "Review the full list of reasons on the Voice of customer page"],
            impact_usd=top["lost_mrr"] * 12, impact_label="ARR lost to this reason", receipts=[rid], link="/voice"))

    # ---- knowledge-base playbooks (rules over facts)
    for pb in kb.matching_playbooks(facts):
        findings.append(Finding(
            id=f"pb_{pb['id']}", role=pb["role"], severity=pb["severity"], title=pb["title"], summary=pb["why"],
            method=Method("rule", f"Playbook '{pb['id']}' from knowledge base v{kb.playbooks()['version']}"),
            actions=pb["actions"], frameworks=pb.get("frameworks", []),
            receipts=[R[f].id for f, _, _ in pb["when"] if f in R]))

    findings.sort(key=lambda f: (-SEVERITY[f.severity], -(f.impact_usd or 0)))
    lap("findings")

    return Analysis(snapshot_id=snap, as_of=str(ms.as_of), lead_model=lm, scored_leads=scored, icp=icp,
                    churn_model=cm, at_risk=at_risk, channels=channels, curves=curves, realloc=realloc,
                    anomalies=anomalies, forecast=(fcast or {}) | {"plan": the_plan or {}, "goal": goal}, facts=facts, stage=stage,
                    benchmarks=positions, findings=findings, receipts=receipts, timings_ms=timings, arpa_new=arpa_new,
                    segments=segs, voc=voc_out, warnings=warnings, readiness=ready)


# ---------------- cache + persistence ----------------
_cache: dict[str, Analysis] = {}
_lock = threading.Lock()  # guards the dicts only; never held while computing
_running: dict[str, threading.Event] = {}
_failed: dict[str, str] = {}
_compute = threading.Semaphore(1)  # one analysis at a time: bounds memory on small servers


class NotReady(Exception):
    """The analysis for this data is being computed; ask again shortly."""


def _persist(conn: Connection, ws: Workspace, hit: Analysis) -> None:
    from .metrics import ensure_snapshot

    ensure_snapshot(conn, ws.data)
    save_receipts(conn, ws.tenant["id"], hit.receipts)
    for card in [m.card for m in (hit.lead_model, hit.churn_model) if m is not None]:
        conn.execute(text("""
            INSERT INTO model_run (id, tenant_id, kind, snapshot_id, algorithm, params, metrics, features,
                                   n_train, n_test)
            VALUES (:id, :t, :kind, :snap, :alg, CAST(:params AS JSONB), CAST(:metrics AS JSONB),
                    CAST(:features AS JSONB), :ntr, :nte)
            ON CONFLICT (id) DO NOTHING"""),
            {"id": card.version, "t": ws.tenant["id"], "kind": card.kind, "snap": card.snapshot_id,
             "alg": card.algorithm, "params": json.dumps(card.params), "metrics": json.dumps(card.metrics),
             "features": json.dumps(card.features), "ntr": card.n_train, "nte": card.n_test})


def _build(ws: Workspace) -> Analysis:
    """Compute, cache and persist one snapshot's analysis. Always releases waiters, even on failure."""
    snap = ws.snapshot_id
    try:
        with _compute:
            with _lock:
                hit = _cache.get(snap)
            if hit is None:
                from .compute import analyse

                hit = analyse(ws)  # in the worker process: page requests stay responsive meanwhile
                from .db import engine

                with engine.begin() as conn:
                    _persist(conn, ws, hit)
                with _lock:
                    if len(_cache) >= max(1, get_settings().analysis_cache):  # most recent workspaces stay warm
                        _cache.pop(next(iter(_cache)))
                    _cache[snap] = hit
                    _failed.pop(snap, None)
        return hit
    except Exception as e:
        logging.getLogger("fred.team").exception("analysis failed for %s", ws.tenant.get("slug"))
        with _lock:
            _failed[snap] = f"{type(e).__name__}: {e}"[:300]
        raise
    finally:
        with _lock:
            ev = _running.pop(snap, None)
        if ev:
            ev.set()


def get(conn: Connection, ws: Workspace, wait: bool = True) -> Analysis:
    """One analysis per data snapshot: models retrain only when the data changes.

    wait=False (web pages): if it isn't ready, start it in the background and raise NotReady at once, so a
    click never hangs on model training. wait=True (scheduler, write-back, warm-up): compute or wait for it.
    """
    snap = ws.snapshot_id
    with _lock:
        hit = _cache.get(snap)
        if hit is not None:
            return hit
        ev = _running.get(snap)
        if ev is None and not wait and snap in _failed:
            # don't retry a failing analysis on every page view; new data (a new snapshot) retries it
            raise RuntimeError(f"The analysis couldn't run on this data: {_failed[snap]}")
        starter = ev is None
        if starter:
            ev = _running[snap] = threading.Event()
    if starter and wait:
        return _build(ws)
    if starter:
        threading.Thread(target=lambda: _safe_build(ws), name=f"analysis-{snap[:8]}", daemon=True).start()
        raise NotReady()
    if not wait:
        raise NotReady()
    ev.wait()
    with _lock:
        hit = _cache.get(snap)
        err = _failed.get(snap)
    if hit is None:
        raise RuntimeError(err or "analysis failed")
    return hit


def _safe_build(ws: Workspace) -> None:
    with contextlib.suppress(Exception):  # logged and remembered in _build
        _build(ws)


def finding_dict(f: Finding) -> dict:
    return asdict(f)
