"""HTTP surface for the BizOps / strategic finance job."""
from __future__ import annotations

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Connection

from . import team
from .auth import Ctx, current, require_module, tenant_row
from .db import get_conn
from .finance.bva import headcount, review
from .finance.data import load_finance
from .provenance import Receipt, _clean, save_receipts
from .workspace import Workspace, load_workspace

router = APIRouter(prefix="/finance")


def _money(v: float) -> str:
    v = abs(v)
    return f"${v / 1e6:.2f}M" if v >= 1e6 else f"${v / 1e3:.0f}K"


def finance_review(conn: Connection, ws: Workspace, month: str | None = None) -> tuple[dict, dict] | None:
    fd = load_finance(conn, ws.tenant["id"])
    if fd is None:
        return None
    m = pd.Timestamp(month + "-01") if month and len(month) == 7 else None
    r = review(fd, ws.mrr_frame(), m)
    receipts = []
    for f in r["findings"]:
        rec = Receipt(metric="bva_variance", value=f["variance"], snapshot_id=ws.snapshot_id,
                      inputs={"area": f["area"], "plan": r["plan"]["id"], "period": f"YTD through {r['month']}",
                              "months": r["ytd_months"], "components": f["components"], "method": f["method"]})
        f["receipt_id"] = rec.id
        receipts.append(rec)
    for row in r["pnl"]:
        rec = Receipt(metric="bva_line", value=row["var_ytd"], snapshot_id=ws.snapshot_id,
                      inputs={"line": row["label"], "plan_ytd": row["plan_ytd"], "actual_ytd": row["actual_ytd"],
                              "plan": r["plan"]["id"], "months": r["ytd_months"]})
        row["receipt_id"] = rec.id
        receipts.append(rec)
    save_receipts(conn, ws.tenant["id"], receipts)
    return r, {"headcount": headcount(fd, pd.Timestamp(r["month"]))}


def team_findings(r: dict, limit: int = 5) -> list[team.Finding]:
    """Turn the monthly review into briefing items for the Strategy & finance role."""
    out = []
    for f in r["findings"][:limit]:
        comps = f["components"] if isinstance(f["components"], dict) else {}
        annual = abs(comps.get("annualized", 0.0))
        big = max(abs(f["variance"]), annual)
        sev = "info" if f["favorable"] and not f.get("early_warning") else ("high" if big >= 100_000 else "medium")
        actions = {
            "Revenue": ["Re-forecast new-logo volume for Q4 with sales leadership",
                        "Decide: close the gap with pipeline, pricing or a lower plan"],
        }.get(f["area"], ["Confirm the cause with the budget owner this week",
                          "Decide: absorb, cut elsewhere, or re-forecast"])
        if comps.get("pattern") == "run_rate":
            actions = ["Find what changed in the month it started and who owns it",
                       f"Either cut it back or add about {_money(comps.get('annualized', 0))}/yr to the forecast"]
        if comps.get("pattern") == "one_off":
            actions = ["Tag as one-off in the board pack so it is not extrapolated"]
        if f["key"].startswith("payroll_"):
            actions = ["Check open roles against the hiring plan; re-date or cancel stale requisitions",
                       "Re-forecast payroll for the rest of the year from the actual roster"]
        out.append(team.Finding(
            id=f"bva_{f['key']}", role="founders_office", severity=sev, title=f["title"], summary=f["explanation"],
            method=team.Method("rule", f["method"]), actions=actions, impact_usd=big,
            impact_label="a year at this run-rate" if annual > abs(f["variance"]) else "vs plan YTD",
            receipts=[f["receipt_id"]], link="/finance"))
    return out


def workspace(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> Workspace:
    return load_workspace(conn, tenant_row(conn, ctx))


@router.get("/bva", dependencies=[Depends(require_module("founders_office"))])
def bva(month: str | None = None, conn: Connection = Depends(get_conn), ws: Workspace = Depends(workspace)) -> dict:
    out = finance_review(conn, ws, month)
    if out is None:
        raise HTTPException(404, "No approved plan yet. Upload or build an operating plan first.")
    r, extra = out
    return _clean({"snapshot_id": ws.snapshot_id, "llm_calls": 0, **r, **extra})
