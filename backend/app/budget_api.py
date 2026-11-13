"""Shared budgeting API: open a round, departments fill in and submit, admins approve or return, publish."""
from __future__ import annotations

import json
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import audit, budget
from .auth import Ctx, current, require_admin, tenant_row
from .config import get_settings
from .connectors.mapping import DEPARTMENTS
from .db import get_conn
from .provenance import _clean
from .workspace import load_workspace

log = logging.getLogger("fred.budget")
router = APIRouter(prefix="/budget")


def _round(conn: Connection, tid: str, rid: str) -> dict:
    try:
        row = conn.execute(text("SELECT * FROM budget_round WHERE id = CAST(:r AS UUID) AND tenant_id = :t"),
                           {"r": rid, "t": tid}).mappings().first()
    except Exception as e:
        raise HTTPException(404, "Budget round not found.") from e
    if not row:
        raise HTTPException(404, "Budget round not found.")
    return dict(row)


def _subs(conn: Connection, rid: str) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT s.department, s.owner_user_id::text, s.status, s.lines, s.hires, s.note, s.updated_at,
               u.name AS owner_name, u.email AS owner_email
        FROM budget_submission s LEFT JOIN app_user u ON u.id = s.owner_user_id
        WHERE s.round_id = CAST(:r AS UUID) ORDER BY s.department"""), {"r": rid}).mappings()]


def _event(conn: Connection, ctx: Ctx, rid: str, action: str, dept: str | None = None, note: str | None = None) -> None:
    conn.execute(text("""INSERT INTO budget_event (round_id, tenant_id, department, actor, action, note)
                         VALUES (CAST(:r AS UUID), :t, :d, :a, :ac, :n)"""),
                 {"r": rid, "t": ctx.tenant_id, "d": dept, "a": ctx.name, "ac": action, "n": note})


def _notify(to: str | None, subject: str, body: str) -> None:
    from .notify import channels

    if to and channels.email_available():
        try:
            channels.send_email(to, subject, body)
        except channels.DeliveryError as e:
            log.warning("budget email to %s failed: %s", to, e)


def _view(conn: Connection, ctx: Ctx, rnd: dict) -> dict:
    subs = _subs(conn, str(rnd["id"]))
    bctx = budget.context(conn, ctx.tenant_id)
    plan = budget.assemble(rnd, subs, bctx)
    events = conn.execute(text("""SELECT department, actor, action, note, created_at FROM budget_event
                                  WHERE round_id = :r ORDER BY created_at DESC LIMIT 50"""), {"r": rnd["id"]}).mappings().all()
    acc = {a["code"]: a for a in bctx["accounts"]}
    for s in subs:
        s["can_edit"] = rnd["status"] == "open" and s["status"] in ("draft", "returned") and (
            ctx.role in ("owner", "admin") or s["owner_user_id"] == ctx.user_id)
        s["mine"] = s["owner_user_id"] == ctx.user_id
        s["updated_at"] = str(s["updated_at"])
        s["staff"] = [{"name": e["name"], "title": e["title"], "salary_annual": e["salary_annual"],
                       "end_date": str(e["end_date"]) if e["end_date"] else None}
                      for e in bctx["staff"] if e["department"] == s["department"]]
        for ln in s["lines"]:
            a = acc.get(ln["account"], {})
            ln["name"], ln["last12"] = a.get("name", ln["account"]), a.get("last12", 0.0)
    return _clean({
        "round": {**{k: rnd[k] for k in ("fiscal_year", "name", "status", "revenue", "baseline")}, "id": str(rnd["id"]),
                  "due_date": str(rnd["due_date"]) if rnd["due_date"] else None,
                  "published_at": str(rnd["published_at"]) if rnd["published_at"] else None},
        "submissions": subs, "plan": {k: plan[k] for k in ("departments", "summary", "revenue")},
        "checks": budget.checks(rnd, plan, bctx, subs),
        "events": [{**dict(e), "created_at": str(e["created_at"])} for e in events],
        "can_admin": ctx.role in ("owner", "admin"),
        "ready_to_publish": rnd["status"] == "open" and all(s["status"] == "approved" for s in subs),
        "accounts": [{"code": a["code"], "name": a["name"]} for a in bctx["accounts"] if a["kind"] not in ("payroll", "revenue")],
    })


@router.get("")
def overview(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    rounds = conn.execute(text("""SELECT id::text, fiscal_year, name, status, due_date, created_at, published_at
                                  FROM budget_round WHERE tenant_id = :t ORDER BY created_at DESC"""),
                          {"t": ctx.tenant_id}).mappings().all()
    current_round = next((r for r in rounds if r["status"] == "open"), rounds[0] if rounds else None)
    members = conn.execute(text("""SELECT u.id::text, u.name, u.email FROM membership m JOIN app_user u ON u.id = m.user_id
                                   WHERE m.tenant_id = :t ORDER BY u.name"""), {"t": ctx.tenant_id}).mappings().all()
    has_books = conn.execute(text("SELECT 1 FROM gl_account WHERE tenant_id = :t LIMIT 1"), {"t": ctx.tenant_id}).first()
    return {"rounds": [{**dict(r), "due_date": str(r["due_date"]) if r["due_date"] else None, "created_at": str(r["created_at"]),
                        "published_at": str(r["published_at"]) if r["published_at"] else None} for r in rounds],
            "current": _view(conn, ctx, _round(conn, ctx.tenant_id, current_round["id"])) if current_round else None,
            "members": [dict(m) for m in members], "departments": DEPARTMENTS, "has_books": bool(has_books),
            "can_admin": ctx.role in ("owner", "admin"), "next_fiscal_year": date.today().year + 1}


class OpenIn(BaseModel):
    fiscal_year: int = Field(..., ge=2000, le=2100)
    due_date: date | None = None
    owners: dict[str, str | None] = Field(default_factory=dict)  # department -> user id


@router.post("/rounds")
def open_round(body: OpenIn, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    if conn.execute(text("SELECT 1 FROM budget_round WHERE tenant_id = :t AND status = 'open'"), {"t": ctx.tenant_id}).first():
        raise HTTPException(409, "A budget round is already open. Publish or cancel it first.")
    bctx = budget.context(conn, ctx.tenant_id)
    if not bctx["accounts"]:
        raise HTTPException(409, "Connect your accounting (or upload the P&L) first: the budget starts from your actuals.")
    ws = load_workspace(conn, tenant_row(conn, ctx))
    assignments = {a["code"]: budget.DEFAULT_DEPT.get(a["pl_group"], "G&A") for a in bctx["accounts"]
                   if a["kind"] not in ("payroll", "revenue")}
    rid = conn.execute(text("""INSERT INTO budget_round (tenant_id, fiscal_year, name, due_date, revenue, assignments,
                                                         baseline, created_by)
                               VALUES (:t, :fy, :n, :d, CAST(:r AS JSONB), CAST(:a AS JSONB), CAST(:b AS JSONB), :u)
                               RETURNING id::text"""),
                       {"t": ctx.tenant_id, "fy": body.fiscal_year, "n": f"FY{body.fiscal_year} budget", "d": body.due_date,
                        "r": json.dumps(budget.revenue_defaults(ws)), "a": json.dumps(assignments),
                        "b": json.dumps(budget.baseline(bctx)), "u": ctx.user_id}).scalar()
    members = {m: (n, e) for m, n, e in conn.execute(text("""SELECT u.id::text, u.name, u.email FROM membership m
        JOIN app_user u ON u.id = m.user_id WHERE m.tenant_id = :t"""), {"t": ctx.tenant_id})}
    lines = budget.prefill(bctx, assignments)
    link = f"{get_settings().public_url.rstrip('/')}/budget"
    for dept in DEPARTMENTS:
        owner = body.owners.get(dept)
        owner = owner if owner in members else None
        conn.execute(text("""INSERT INTO budget_submission (round_id, tenant_id, department, owner_user_id, lines)
                             VALUES (CAST(:r AS UUID), :t, :d, CAST(:o AS UUID), CAST(:l AS JSONB))"""),
                     {"r": rid, "t": ctx.tenant_id, "d": dept, "o": owner, "l": json.dumps(lines[dept])})
        if owner and owner != ctx.user_id:
            _notify(members[owner][1], f"Your {dept} budget for FY{body.fiscal_year}",
                    f"{ctx.name} asked you to plan {dept} for FY{body.fiscal_year}"
                    + (f", due {body.due_date:%b %d}" if body.due_date else "") + f".\n\nIt's prefilled from current spend "
                    f"and your team: adjust it and add hires here: {link}")
    _event(conn, ctx, rid, "opened")
    audit.by(conn, ctx, "budget_round_opened", rid, {"fiscal_year": body.fiscal_year})
    return {"id": rid}


class SubmissionIn(BaseModel):
    lines: list[dict] = Field(default_factory=list)
    hires: list[dict] = Field(default_factory=list)
    note: str | None = Field(None, max_length=2000)
    owner_user_id: str | None = None


def _clean_lines(lines: list[dict], allowed: set[str]) -> list[dict]:
    out = []
    for ln in lines:
        code = str(ln.get("account", ""))
        if code not in allowed:
            raise HTTPException(422, f"Unknown account {code}.")
        if "annual" in ln:
            monthly = budget.even(float(ln["annual"]))
        else:
            monthly = [float(x) for x in ln.get("monthly", [])]
        if len(monthly) != 12 or any(x < 0 or x > 1e9 for x in monthly):
            raise HTTPException(422, f"{code}: give 12 monthly amounts (or an annual amount), none negative.")
        out.append({"account": code, "monthly": [round(x, 2) for x in monthly]})
    return out


def _clean_hires(hires: list[dict], fy: int) -> list[dict]:
    out = []
    for h in hires:
        try:
            start = date.fromisoformat(str(h["planned_start"])[:10]).replace(day=1)
            sal = float(h["salary_annual"])
        except (KeyError, ValueError) as e:
            raise HTTPException(422, "Each hire needs a role, a start month and a salary.") from e
        role = str(h.get("role", "")).strip()[:120]
        if not role or sal <= 0 or sal > 5e6 or start.year != fy:
            raise HTTPException(422, f"Check the hire '{role or '?'}': role, a salary, and a start month in FY{fy}.")
        out.append({"role": role, "location": str(h.get("location") or "US")[:60], "planned_start": str(start),
                    "salary_annual": round(sal, 2)})
    return out


def _sub(conn: Connection, rid: str, dept: str) -> dict:
    row = conn.execute(text("""SELECT s.*, s.owner_user_id::text AS owner_id, u.email AS owner_email
                               FROM budget_submission s LEFT JOIN app_user u ON u.id = s.owner_user_id
                               WHERE s.round_id = CAST(:r AS UUID) AND s.department = :d"""),
                       {"r": rid, "d": dept}).mappings().first()
    if not row:
        raise HTTPException(404, "Department not in this round.")
    return dict(row)


def _may_edit(ctx: Ctx, rnd: dict, sub: dict) -> None:
    if rnd["status"] != "open":
        raise HTTPException(409, "This round is closed.")
    if ctx.role not in ("owner", "admin") and sub["owner_id"] != ctx.user_id:
        raise HTTPException(403, f"Only the {sub['department']} owner or an admin can change this budget.")


@router.put("/rounds/{rid}/departments/{dept}")
def save(rid: str, dept: str, body: SubmissionIn, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    rnd = _round(conn, ctx.tenant_id, rid)
    sub = _sub(conn, rid, dept)
    _may_edit(ctx, rnd, sub)
    if sub["status"] in ("submitted", "approved") and ctx.role not in ("owner", "admin"):
        raise HTTPException(409, "Already submitted. Ask an admin to send it back if it needs changes.")
    allowed = {a["code"] for a in budget.context(conn, ctx.tenant_id)["accounts"] if a["kind"] not in ("payroll", "revenue")}
    params = {"r": rid, "d": dept, "l": json.dumps(_clean_lines(body.lines, allowed)),
              "h": json.dumps(_clean_hires(body.hires, rnd["fiscal_year"])), "n": body.note}
    sets = "lines = CAST(:l AS JSONB), hires = CAST(:h AS JSONB), note = :n, updated_at = now()"
    if body.owner_user_id is not None and ctx.role in ("owner", "admin"):
        sets += ", owner_user_id = CAST(NULLIF(:o, '') AS UUID)"
        params["o"] = body.owner_user_id
    conn.execute(text(f"UPDATE budget_submission SET {sets} WHERE round_id = CAST(:r AS UUID) AND department = :d"), params)
    return {"ok": True}


class DecisionIn(BaseModel):
    note: str | None = Field(None, max_length=1000)


@router.post("/rounds/{rid}/departments/{dept}/submit")
def submit(rid: str, dept: str, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    rnd = _round(conn, ctx.tenant_id, rid)
    sub = _sub(conn, rid, dept)
    _may_edit(ctx, rnd, sub)
    conn.execute(text("""UPDATE budget_submission SET status = 'submitted', updated_at = now()
                         WHERE round_id = CAST(:r AS UUID) AND department = :d"""), {"r": rid, "d": dept})
    _event(conn, ctx, rid, "submitted", dept)
    audit.by(conn, ctx, "budget_submitted", f"{rnd['name']}: {dept}")
    return {"ok": True}


def _decide(rid: str, dept: str, status: str, body: DecisionIn, ctx: Ctx, conn: Connection) -> dict:
    rnd = _round(conn, ctx.tenant_id, rid)
    sub = _sub(conn, rid, dept)
    if rnd["status"] != "open":
        raise HTTPException(409, "This round is closed.")
    if status == "returned" and not (body.note or "").strip():
        raise HTTPException(422, "Say what needs to change.")
    conn.execute(text("""UPDATE budget_submission SET status = :s, updated_at = now()
                         WHERE round_id = CAST(:r AS UUID) AND department = :d"""), {"s": status, "r": rid, "d": dept})
    _event(conn, ctx, rid, status, dept, body.note)
    audit.by(conn, ctx, "budget_approved" if status == "approved" else "budget_returned", f"{rnd['name']}: {dept}")
    if status == "returned" and sub["owner_email"] and sub["owner_id"] != ctx.user_id:
        _notify(sub["owner_email"], f"Changes requested: {dept} budget",
                f"{ctx.name} sent the {dept} budget back:\n\n{body.note}\n\n{get_settings().public_url.rstrip('/')}/budget")
    return {"ok": True}


@router.post("/rounds/{rid}/departments/{dept}/approve")
def approve(rid: str, dept: str, body: DecisionIn, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    return _decide(rid, dept, "approved", body, ctx, conn)


@router.post("/rounds/{rid}/departments/{dept}/return")
def send_back(rid: str, dept: str, body: DecisionIn, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    return _decide(rid, dept, "returned", body, ctx, conn)


class RevenueIn(BaseModel):
    new_customers: float = Field(..., ge=0, le=100000)
    arpa_new: float = Field(..., ge=0, le=1e7)
    revenue_churn: float = Field(..., ge=0, le=1)
    net_expansion: float = Field(..., ge=-1, le=1)


@router.put("/rounds/{rid}/revenue")
def set_revenue(rid: str, body: RevenueIn, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    rnd = _round(conn, ctx.tenant_id, rid)
    if rnd["status"] != "open":
        raise HTTPException(409, "This round is closed.")
    conn.execute(text("UPDATE budget_round SET revenue = revenue || CAST(:r AS JSONB) WHERE id = CAST(:id AS UUID)"),
                 {"r": json.dumps(body.model_dump()), "id": rid})
    _event(conn, ctx, rid, "revenue_changed")
    return {"ok": True}


@router.post("/rounds/{rid}/publish")
def publish(rid: str, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    rnd = _round(conn, ctx.tenant_id, rid)
    subs = _subs(conn, rid)
    if rnd["status"] != "open":
        raise HTTPException(409, "This round is closed.")
    waiting = [s["department"] for s in subs if s["status"] != "approved"]
    if waiting:
        raise HTTPException(409, f"Approve every department first. Still open: {', '.join(waiting)}.")
    plan = budget.assemble(rnd, subs, budget.context(conn, ctx.tenant_id))
    pid = budget.publish(conn, ctx.tenant_id, rnd, plan, subs)
    conn.execute(text("UPDATE budget_round SET status = 'published', published_at = now(), plan_id = :p WHERE id = CAST(:r AS UUID)"),
                 {"p": pid, "r": rid})
    _event(conn, ctx, rid, "published")
    audit.by(conn, ctx, "budget_published", rnd["name"], {"plan_id": pid})
    return {"ok": True, "plan_id": pid}


@router.post("/rounds/{rid}/cancel")
def cancel(rid: str, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    rnd = _round(conn, ctx.tenant_id, rid)
    if rnd["status"] != "open":
        raise HTTPException(409, "This round is closed.")
    conn.execute(text("UPDATE budget_round SET status = 'cancelled' WHERE id = CAST(:r AS UUID)"), {"r": rid})
    _event(conn, ctx, rid, "cancelled")
    return {"ok": True}
