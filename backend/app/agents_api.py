"""The Crew page: role agents, who they report to, what they did, and drafts waiting for approval."""
from __future__ import annotations

import json
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import audit, modules
from .agents import runner, writer
from .auth import Ctx, current, require_admin
from .config import get_settings
from .db import get_conn
from .notify import channels

router = APIRouter(prefix="/crew")


@router.get("")
def overview(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    cfg = runner.settings_row(conn, ctx.tenant_id)
    hired = modules.hired(conn, ctx.tenant_id)
    default = runner.default_owner(conn, ctx.tenant_id)
    members = [dict(r) for r in conn.execute(text("""SELECT u.id::text, u.name, u.email FROM membership m
        JOIN app_user u ON u.id = m.user_id WHERE m.tenant_id = :t ORDER BY u.name"""), {"t": ctx.tenant_id}).mappings()]
    stats = {r[0]: {"week": r[1], "drafts": r[2], "last": str(r[3]) if r[3] else None} for r in conn.execute(text("""
        SELECT role, count(*) FILTER (WHERE created_at > now() - interval '7 days' AND status IN ('sent','done')),
               count(*) FILTER (WHERE status = 'draft'), max(created_at)
        FROM agent_activity WHERE tenant_id = :t GROUP BY role"""), {"t": ctx.tenant_id})}
    rows = conn.execute(text("""
        SELECT a.id::text, a.role, a.kind, a.status, a.subject, a.body, a.writer, a.error, a.created_at, a.decided_at,
               a.recipient_email, u.name AS recipient
        FROM agent_activity a LEFT JOIN app_user u ON u.id = a.recipient_user_id
        WHERE a.tenant_id = :t ORDER BY (a.status = 'draft') DESC, a.created_at DESC LIMIT 60"""),
        {"t": ctx.tenant_id}).mappings().all()
    owners = cfg["owners"] or {}
    return {
        "mode": cfg["mode"], "weekly_day": cfg["weekly_day"],
        "last_run_at": str(cfg["last_run_at"]) if cfg["last_run_at"] else None,
        "writer": {"llm": writer.llm_enabled(), "model": get_settings().agent_llm},
        "email_available": channels.email_available(), "can_edit": ctx.role in ("owner", "admin"),
        "members": members,
        "agents": [{"id": m.id, "title": m.title, "tagline": m.tagline, "replaces": m.replaces,
                    "owner": owners.get(m.id) or default, "owner_is_default": not owners.get(m.id),
                    **stats.get(m.id, {"week": 0, "drafts": 0, "last": None})}
                   for m in modules.MODULES.values() if m.id in hired],
        "activity": [{**dict(r), "created_at": str(r["created_at"]),
                      "decided_at": str(r["decided_at"]) if r["decided_at"] else None} for r in rows],
    }


class SettingsIn(BaseModel):
    mode: str | None = Field(None, pattern="^(off|draft|auto)$")
    owners: dict[str, str | None] | None = None
    weekly_day: int | None = Field(None, ge=0, le=6)


@router.put("")
def update(body: SettingsIn, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    cfg = runner.settings_row(conn, ctx.tenant_id)
    owners = dict(cfg["owners"] or {})
    if body.owners is not None:
        valid = {r[0] for r in conn.execute(text("SELECT user_id::text FROM membership WHERE tenant_id = :t"),
                                             {"t": ctx.tenant_id})}
        for role, uid in body.owners.items():
            if role not in modules.MODULES:
                raise HTTPException(422, f"Unknown role {role}.")
            if uid and uid not in valid:
                raise HTTPException(422, "That person isn't in this workspace.")
            owners[role] = uid or None
    conn.execute(text("""INSERT INTO agent_setting (tenant_id, mode, owners, weekly_day)
                         VALUES (:t, :m, CAST(:o AS JSONB), :w)
                         ON CONFLICT (tenant_id) DO UPDATE SET mode = :m, owners = CAST(:o AS JSONB), weekly_day = :w,
                           updated_at = now()"""),
                 {"t": ctx.tenant_id, "m": body.mode or cfg["mode"], "o": json.dumps(owners),
                  "w": cfg["weekly_day"] if body.weekly_day is None else body.weekly_day})
    audit.by(conn, ctx, "notifications_changed", "crew", body.model_dump(exclude_none=True))
    return {"ok": True}


@router.post("/run")
def run_now(ctx: Ctx = Depends(require_admin)) -> dict:
    """Run the crew now, in the background (it may need to finish the analysis first)."""
    threading.Thread(target=runner.run, args=(ctx.tenant_id,), name=f"crew-{ctx.tenant_id[:8]}", daemon=True).start()
    return {"ok": True, "started": True}


class EditIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=200)
    body: str = Field(..., min_length=1, max_length=5000)


def _draft(conn: Connection, tid: str, aid: str) -> dict:
    try:
        row = conn.execute(text("SELECT id::text, status FROM agent_activity WHERE id = CAST(:i AS UUID) AND tenant_id = :t"),
                           {"i": aid, "t": tid}).mappings().first()
    except Exception as e:
        raise HTTPException(404, "Not found.") from e
    if not row:
        raise HTTPException(404, "Not found.")
    if row["status"] != "draft":
        raise HTTPException(409, "This has already been handled.")
    return dict(row)


@router.put("/activity/{aid}")
def edit(aid: str, body: EditIn, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    _draft(conn, ctx.tenant_id, aid)
    conn.execute(text("""UPDATE agent_activity SET subject = :s, body = :b, writer = writer || ' (edited)'
                         WHERE id = CAST(:i AS UUID)"""), {"s": body.subject, "b": body.body, "i": aid})
    return {"ok": True}


@router.post("/activity/{aid}/approve")
def approve(aid: str, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    _draft(conn, ctx.tenant_id, aid)
    status = runner.execute(conn, ctx.tenant_id, aid, decided_by=ctx.user_id)
    err = conn.execute(text("SELECT error FROM agent_activity WHERE id = CAST(:i AS UUID)"), {"i": aid}).scalar()
    return {"ok": status in ("sent", "done"), "status": status, "note": err}


@router.post("/activity/{aid}/dismiss")
def dismiss(aid: str, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    _draft(conn, ctx.tenant_id, aid)
    conn.execute(text("""UPDATE agent_activity SET status = 'dismissed', decided_at = now(), decided_by = CAST(:u AS UUID)
                         WHERE id = CAST(:i AS UUID)"""), {"u": ctx.user_id, "i": aid})
    return {"ok": True}
