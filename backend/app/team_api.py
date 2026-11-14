"""HTTP surface for the analyst team. One endpoint per screen; all numbers come from team.Analysis."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import knowledge as kb
from . import modules, team
from .analysis import planner
from .auth import Ctx, current, require_module, tenant_row
from .db import engine, get_conn
from .ml.embeddings import MODEL_CARD
from .provenance import Receipt, _clean, save_receipts
from .workspace import load_workspace

log = logging.getLogger("fred.team")
router = APIRouter()


def analysis(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> tuple[team.Analysis, dict]:
    tenant = tenant_row(conn, ctx)
    has_data = conn.execute(text("SELECT 1 FROM subscription_mrr WHERE tenant_id = :t LIMIT 1"),
                            {"t": tenant["id"]}).first()
    if not has_data:
        if tenant["data_source"] == "preparing":
            raise HTTPException(409, "Your demo company is being set up. This takes about a minute; the page will "
                                     "refresh on its own.")
        raise HTTPException(409, "No data yet. Connect billing and CRM, or load the demo company in Settings.")
    ws = load_workspace(conn, tenant)
    return team.get(conn, ws), {"ws": ws, "tenant": tenant, "ctx": ctx}


def need(a: team.Analysis, job: str, value) -> None:
    """409 with a precise 'what data is missing' message when a job could not run."""
    if value is None or (hasattr(value, "__len__") and len(value) == 0):
        r = a.readiness.get(job, {})
        raise HTTPException(409, f"Waiting for data. This needs {r.get('needs', 'more data')} "
                                 f"(you have {r.get('have', 'none')}). Add it under Settings → Data sources.")


def envelope(a: team.Analysis, **body) -> dict:
    return _clean({"snapshot_id": a.snapshot_id, "as_of": a.as_of, "llm_calls": a.llm_calls, **body})


def collect_findings(conn: Connection, a: team.Analysis, ctx: dict,
                     keep_muted: bool = False) -> tuple[list, list[dict], dict | None]:
    """All findings for hired roles, minus the ones the team marked as expected. One source of truth for the
    briefing, the action tracker and the documents."""
    from .finance_api import finance_review, team_findings

    tid = ctx["tenant"]["id"]
    hired = modules.hired(conn, tid)
    fin = finance_review(conn, ctx["ws"]) if "founders_office" in hired else None
    # Copy, never mutate: a.findings belongs to the shared per-snapshot cache.
    findings = [replace(f, role=modules.ROLE_TO_MODULE.get(f.role, f.role))
                for f in list(a.findings) + (team_findings(fin[0]) if fin else [])]
    findings = [f for f in findings if f.role in hired]
    notes = [dict(r) for r in conn.execute(text("""
        SELECT DISTINCT ON (finding_id) finding_id, note, suppress_until, created_at FROM finding_note
        WHERE tenant_id = :t AND suppress_until >= current_date ORDER BY finding_id, created_at DESC"""),
        {"t": tid}).mappings()]
    muted = {n["finding_id"] for n in notes}
    if not keep_muted:
        findings = [f for f in findings if f.id not in muted]
    findings.sort(key=lambda f: (-team.SEVERITY[f.severity], -(f.impact_usd or 0)))
    return findings, notes, fin


@router.get("/briefing")
def briefing(conn: Connection = Depends(get_conn), dep=Depends(analysis)) -> dict:
    a, ctx = dep
    hired = modules.hired(conn, ctx["tenant"]["id"])
    findings, notes, _ = collect_findings(conn, a, ctx)
    open_tasks = conn.execute(text("""SELECT count(*) FILTER (WHERE status IN ('open','in_progress')),
                                             count(*) FILTER (WHERE status IN ('open','in_progress') AND due_date < current_date)
                                      FROM task WHERE tenant_id = :t"""), {"t": ctx["tenant"]["id"]}).first()
    tasked = {r[0] for r in conn.execute(text("""SELECT finding_id FROM task WHERE tenant_id = :t
                                                 AND status IN ('open','in_progress')"""), {"t": ctx["tenant"]["id"]})}
    roles = []
    for mid, m in modules.MODULES.items():
        if mid not in hired:
            continue
        fs = [f for f in findings if f.role == mid]
        roles.append({"id": mid, "label": m.title, "replaces": m.replaces, "tagline": m.tagline,
                      "findings": len(fs), "pages": m.pages,
                      "top_severity": max((f.severity for f in fs), key=lambda s: team.SEVERITY[s], default="info")})
    by_kind: dict[str, int] = {}
    for f in findings:
        by_kind[f.method.kind] = by_kind.get(f.method.kind, 0) + 1
    return envelope(a, roles=roles,
                    findings=[team.finding_dict(f) | {"tasked": f.id in tasked} for f in findings], methods=by_kind,
                    compute_ms=a.timings_ms, stage=a.stage, benchmarks=a.benchmarks,
                    knowledge=kb.staleness(), warnings=a.warnings, hired=sorted(hired),
                    follow_ups={"open": open_tasks[0], "overdue": open_tasks[1]}, muted=len(notes))


@router.get("/leads", dependencies=[Depends(require_module("lead_generator"))])
def leads(limit: int = 100, lane: str | None = None, dep=Depends(analysis)) -> dict:
    a, _ = dep
    need(a, "lead_model", a.lead_model)
    s = a.scored_leads
    if lane:
        s = s[s["lane"] == lane]
    cols = ["lead_id", "company", "contact_name", "industry", "employees", "country", "seniority", "source_channel",
            "created_at", "demo_requested", "pricing_views", "p", "expected_value", "first_year_value", "segment",
            "lane", "next_action", "reasons", "risks"]
    rows = s.head(limit)[cols].assign(created_at=lambda d: d["created_at"].astype(str)).to_dict("records")
    lanes = a.scored_leads["lane"].value_counts().to_dict()
    return envelope(a, model=a.lead_model.card.public(), lanes=lanes, total_open=len(a.scored_leads),
                    thresholds=kb.benchmarks()["routing"], icp=a.icp, leads=rows)


@router.get("/accounts/at-risk", dependencies=[Depends(require_module("business_analyst"))])
def at_risk(limit: int = 50, dep=Depends(analysis)) -> dict:
    a, _ = dep
    need(a, "churn_model", a.churn_model)
    s = a.at_risk
    cols = ["account_id", "name", "segment", "industry", "acquisition_channel", "mrr", "p", "mrr_at_risk",
            "util_now", "util_trend", "tenure_months", "reasons", "play"]
    return envelope(a, model=a.churn_model.card.public(),
                    totals={"accounts": len(s), "high_risk": int((s["p"] >= 0.25).sum()),
                            "mrr_at_risk": float(s["mrr_at_risk"].sum()), "mrr": float(s["mrr"].sum())},
                    accounts=s.head(limit)[cols].to_dict("records"))


@router.get("/marketing", dependencies=[Depends(require_module("marketing_analyst"))])
def marketing(dep=Depends(analysis)) -> dict:
    a, _ = dep
    need(a, "marketing", a.channels)
    return envelope(a, channels=a.channels, curves=a.curves, reallocation=a.realloc, anomalies=a.anomalies,
                    arpa_new=a.arpa_new)


@router.get("/forecast", dependencies=[Depends(require_module("founders_office"))])
def forecast(dep=Depends(analysis)) -> dict:
    a, _ = dep
    need(a, "forecast", a.forecast.get("forecast"))
    f = a.forecast
    return envelope(a, method=f["method"], backtest_mape=f["backtest_mape"], history=f["history"],
                    forecast=f["forecast"], drivers=f["drivers"], default_goal=f["goal"], plan=f["plan"])


class PlanIn(BaseModel):
    target_arr: float = Field(..., gt=0)
    months: int = Field(18, ge=3, le=60)


@router.post("/plan", dependencies=[Depends(require_module("founders_office"))])
def plan(body: PlanIn, conn: Connection = Depends(get_conn), dep=Depends(analysis)) -> dict:
    a, ctx = dep
    need(a, "forecast", a.forecast.get("forecast"))
    ws = ctx["ws"]
    ms = ws.metrics
    R = ms.receipts
    drv = a.forecast["drivers"]
    el = [c["b"] for c in a.curves.values() if c["r2"] >= 0.3]
    p = planner.plan(target_arr=body.target_arr, months=body.months, mrr0=R["mrr"].value,
                     revenue_churn=drv["revenue_churn"], net_expansion=drv["net_expansion"],
                     new_customers_now=R["new_customers"].value / 3, arpa_new=a.arpa_new,
                     sm_monthly=ms.baseline.sm_spend_monthly, opex_monthly=ms.baseline.other_opex_monthly,
                     cash=ms.baseline.cash, gross_margin=ws.data.gross_margin,
                     spend_elasticity=sum(el) / len(el) if el else 0.7,
                     max_sm_observed=max(ws.data.sm.values()) if ws.data.sm else None)
    r = Receipt(metric="plan_sm_needed", value=p["lever_spend"]["sm_monthly_needed"], snapshot_id=a.snapshot_id,
                inputs={"as_of": a.as_of, "target_arr": body.target_arr, "months": body.months,
                        "new_customers_needed": p["lever_spend"]["new_customers_needed"],
                        "elasticity": p["lever_spend"]["spend_elasticity"]})
    save_receipts(conn, ctx["tenant"]["id"], [r])
    return envelope(a, plan=p, receipt_id=r.id)


@router.get("/market", dependencies=[Depends(require_module("market_researcher"))])
def market(dep=Depends(analysis)) -> dict:
    a, _ = dep
    need(a, "segments", a.segments)
    seg = {k: v for k, v in a.segments.items() if k != "assignments"}
    return envelope(a, **seg, model=MODEL_CARD, warnings=a.warnings)


@router.get("/voice", dependencies=[Depends(require_module("business_analyst"))])
def voice(dep=Depends(analysis)) -> dict:
    a, _ = dep
    need(a, "voice", a.voc)
    return envelope(a, **a.voc, model=MODEL_CARD)


@router.get("/models")
def models(conn: Connection = Depends(get_conn), dep=Depends(analysis)) -> dict:
    a, ctx = dep
    history = conn.execute(text("""
        SELECT id, kind, snapshot_id, metrics, n_train, n_test, trained_at FROM model_run
        WHERE tenant_id = :t ORDER BY trained_at DESC LIMIT 20"""), {"t": ctx["tenant"]["id"]}).mappings().all()
    return envelope(a, models=[m.card.public() for m in (a.lead_model, a.churn_model) if m is not None],
                    pretrained=MODEL_CARD, readiness=a.readiness,
                    history=[{**dict(h), "trained_at": str(h["trained_at"])} for h in history],
                    knowledge={"status": kb.staleness(), "benchmarks": kb.benchmarks(),
                               "playbooks": kb.playbooks()["playbooks"]},
                    compute_ms=a.timings_ms,
                    methods_policy=[
                        {"tier": 1, "kind": "rule", "used_for": "Routing, playbooks, goal-seeking, cash paths", "cost": "free"},
                        {"tier": 2, "kind": "statistics", "used_for": "Forecasts, response curves, anomalies, ICP tests", "cost": "free"},
                        {"tier": 3, "kind": "ml", "used_for": "Trained on your data: lead scoring, churn prediction. "
                         "Pre-trained (open model): customer segments, feedback themes", "cost": "seconds of CPU per snapshot"},
                        {"tier": 4, "kind": "llm", "used_for": "Language only: parsing questions, drafting prose. Never numbers.",
                         "cost": "per call; 0 calls in this analysis"},
                    ])


def warm() -> None:
    """Train models for the current snapshot in the background so the first visitor does not wait."""
    def _go() -> None:
        for attempt in range(10):
            try:
                with engine.begin() as conn:
                    # warm the workspaces people used most recently
                    rows = conn.execute(text("""
                        SELECT t.id::text, t.slug, t.name, t.currency, t.gross_margin, t.annual_wacc, t.data_source
                        FROM tenant t JOIN user_session s ON s.tenant_id = t.id
                        WHERE EXISTS (SELECT 1 FROM subscription_mrr m WHERE m.tenant_id = t.id)
                        GROUP BY t.id ORDER BY max(s.created_at) DESC LIMIT 4""")).mappings().all()
                    for tenant in rows:
                        t = time.perf_counter()
                        team.get(conn, load_workspace(conn, dict(tenant)))
                        log.info("warmed %s in %.1fs", tenant["slug"], time.perf_counter() - t)
                return
            except Exception as e:  # DB not ready yet, etc.
                log.warning("warm-up attempt %d failed: %s", attempt + 1, e)
                time.sleep(3)
    threading.Thread(target=_go, daemon=True).start()


def warm_tenant(tenant_id: str) -> None:
    """Train one workspace's models now, so its first briefing is instant."""
    try:
        with engine.begin() as conn:
            row = conn.execute(text("""SELECT id::text AS id, slug, name, currency, gross_margin, annual_wacc,
                                              data_source FROM tenant WHERE id = :t"""), {"t": tenant_id}).mappings().first()
            if row:
                t = time.perf_counter()
                team.get(conn, load_workspace(conn, dict(row)))
                log.info("warmed new workspace %s in %.1fs", row["slug"], time.perf_counter() - t)
    except Exception as e:
        log.warning("warm-up for %s failed: %s", tenant_id, e)
