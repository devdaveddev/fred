from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import revcore as rc
from .accuracy_api import router as accuracy_router
from .auth import current_tenant, require_module, team_router
from .auth import router as auth_router
from .budget_api import router as budget_router
from .catalog import METRICS, ORDER, status_for
from .config import get_settings
from .db import get_conn
from .finance_api import router as finance_router
from .members_api import router as members_router
from .metrics import compute_metrics, ensure_snapshot, load_tenant_data
from .notify_api import router as notify_router
from .provenance import Receipt, get_receipt, save_receipts
from .schemas import (
    MetricOut,
    OverviewOut,
    ReceiptOut,
    ScenarioIn,
    ScenarioOut,
    SeriesPoint,
    SimOutcome,
    TenantOut,
)
from .security_api import router as security_router
from .sources_api import router as sources_router
from .sso import router as sso_router
from .team_api import router as team_api_router
from .team_api import warm
from .work_api import router as work_router

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper())

@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        from .auth import resume_pending_demos

        resume_pending_demos()
    except Exception:
        logging.getLogger("fred").exception("could not resume demo setups")
    if settings.warm_on_start:
        warm()
    stop = None
    if settings.embedded_worker:
        import threading

        from .notify.jobs import tick

        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(settings.worker_interval_s):
                try:
                    tick()
                except Exception:
                    logging.getLogger("fred.worker").exception("scheduler pass failed")

        threading.Thread(target=loop, name="fred-scheduler", daemon=True).start()
    yield
    if stop:
        stop.set()


# Served behind the proxy at /api (prefix stripped); uvicorn --root-path keeps /docs links correct.
app = FastAPI(title="Fred Revenue Intelligence API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def _request_meta(request, call_next):
    """Who is calling, for the audit log (client IP comes from the proxy via --proxy-headers)."""
    from .audit import request_meta

    token = request_meta.set({"ip": request.client.host if request.client else None,
                              "ua": request.headers.get("user-agent")})
    try:
        return await call_next(request)
    finally:
        request_meta.reset(token)
app.include_router(auth_router)
app.include_router(team_router)
app.include_router(team_api_router)
app.include_router(finance_router)
app.include_router(work_router)
app.include_router(sources_router)
app.include_router(notify_router)
app.include_router(members_router)
app.include_router(security_router)
app.include_router(sso_router)
app.include_router(accuracy_router)
app.include_router(budget_router)


@app.get("/health")
def health(conn: Connection = Depends(get_conn)) -> dict:
    conn.execute(text("SELECT 1"))
    return {"status": "ok", "formula_version": rc.FORMULA_VERSION}


@app.get("/overview", response_model=OverviewOut, dependencies=[Depends(require_module("business_analyst"))])
def overview(conn: Connection = Depends(get_conn), tenant: dict = Depends(current_tenant)) -> OverviewOut:
    data = load_tenant_data(conn, tenant)
    ensure_snapshot(conn, data)
    ms = compute_metrics(data)
    save_receipts(conn, tenant["id"], list(ms.receipts.values()) + ms.mrr_series)

    metrics = []
    for mid in ORDER:
        r = ms.receipts.get(mid)
        if r is None:
            continue
        label, group, unit, bench, _, help_ = METRICS[mid]
        pub = r.public()
        metrics.append(MetricOut(id=mid, label=label, group=group, unit=unit, value=pub["value"],
                                 status=status_for(mid, r.value), benchmark=bench, help=help_,
                                 receipt=ReceiptOut(**pub)))
    return OverviewOut(
        tenant=TenantOut(slug=tenant["slug"], name=tenant["name"], currency=tenant["currency"]),
        as_of=str(ms.as_of), snapshot_id=ms.snapshot_id, warnings=ms.warnings, data_coverage=ms.data_coverage,
        metrics=metrics,
        mrr_series=[SeriesPoint(month=r.inputs["month"], value=r.value, receipt_id=r.id) for r in ms.mrr_series],
    )


SIM_METRICS = {
    "sim_arr_end_p50": lambda s: s["arr_end"]["p50"],
    "sim_nrr_12m_p50": lambda s: s["nrr_12m"]["p50"],
    "sim_p_cash_out": lambda s: s["p_cash_out"],
    "sim_cac": lambda s: s["cac"],
    "sim_cac_payback_months": lambda s: s["cac_payback_months"],
    "sim_ltv_cac": lambda s: s["ltv_cac_wacc"],
}


def _outcome(sim: dict, variant: str, snapshot_id: str) -> tuple[SimOutcome, list[Receipt]]:
    inputs = {"variant": variant, "months": sim["months"], "runs": sim["runs"], **sim["inputs"]}
    receipts = [Receipt(metric=m, value=f(sim), snapshot_id=snapshot_id, inputs=inputs, seed=sim["seed"])
                for m, f in SIM_METRICS.items()]
    pubs = {r.metric: ReceiptOut(**r.public()) for r in receipts}
    return SimOutcome(arr_end=sim["arr_end"], arr_path=sim["arr_path"], nrr_12m=sim["nrr_12m"],
                      p_cash_out=sim["p_cash_out"], runway_months_p50=sim["runway_months_p50"],
                      cac=pubs["sim_cac"].value, cac_payback_months=pubs["sim_cac_payback_months"].value,
                      ltv_cac_wacc=pubs["sim_ltv_cac"].value, receipts=pubs), receipts


@app.post("/scenarios", response_model=ScenarioOut, dependencies=[Depends(require_module("founders_office"))])
def run_scenario(body: ScenarioIn, conn: Connection = Depends(get_conn),
                 tenant: dict = Depends(current_tenant)) -> ScenarioOut:
    data = load_tenant_data(conn, tenant)
    ensure_snapshot(conn, data)
    ms = compute_metrics(data)
    if ms.baseline is None:
        raise HTTPException(422, "Not enough data to build a baseline.")
    base = replace(ms.baseline, price_elasticity=body.price_elasticity)
    shock = rc.Shock(**body.shock.model_dump())
    res = rc.compare(base, shock, months=body.months, runs=body.runs, seed=body.seed)
    b_out, b_r = _outcome(res["baseline"], "baseline", ms.snapshot_id)
    s_out, s_r = _outcome(res["scenario"], "scenario", ms.snapshot_id)
    save_receipts(conn, tenant["id"], b_r + s_r)

    warnings = list(ms.warnings)
    if body.price_elasticity == -0.6:
        warnings.append("Price elasticity is the default (-0.6), not fitted to your history yet.")
    return ScenarioOut(
        snapshot_id=ms.snapshot_id, warnings=warnings, data_coverage=ms.data_coverage,
        assumptions={"baseline": res["baseline"]["inputs"]["baseline"], "shock": body.shock.model_dump(),
                     "price_elasticity": body.price_elasticity, "months": body.months, "runs": body.runs,
                     "seed": body.seed, "formula_version": rc.FORMULA_VERSION},
        baseline=b_out, scenario=s_out,
    )


@app.get("/receipts/{receipt_id}")
def explain_receipt(receipt_id: str, conn: Connection = Depends(get_conn),
                    tenant: dict = Depends(current_tenant)) -> dict:
    r = get_receipt(conn, tenant["id"], receipt_id)
    if not r:
        raise HTTPException(404, "Receipt not found")
    return r
