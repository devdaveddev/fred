"""Notification settings: Slack webhook, email digest, daily sync and its hour; a preview of the next digest."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import audit
from .auth import Ctx, current
from .config import get_settings
from .connectors import secrets
from .db import get_conn
from .notify import channels, jobs
from .sources_api import admin

log = logging.getLogger("fred.notify")
router = APIRouter(prefix="/notifications")


def _me(conn: Connection, ctx: Ctx) -> dict:
    return dict(conn.execute(text("""SELECT u.email, u.name, m.email_digest FROM app_user u
                                     JOIN membership m ON m.user_id = u.id AND m.tenant_id = :t WHERE u.id = :u"""),
                             {"t": ctx.tenant_id, "u": ctx.user_id}).mappings().one())


@router.get("")
def overview(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    cfg = jobs.settings_row(conn, ctx.tenant_id)
    now = datetime.now(UTC)
    nxt = jobs.next_run(now, cfg["digest_hour"], cfg["timezone"], jobs.today_done(conn, ctx.tenant_id, now, cfg["timezone"]))
    try:
        d = jobs.compose(conn, ctx.tenant_id, now=now, touch=False)
        preview = None if d is None else {
            "empty": d.empty, "summary": d.counts(), "findings": d.findings, "more_findings": d.more_findings,
            "task_updates": d.task_updates, "overdue": d.overdue, "sync_problems": d.sync_problems}
    except Exception as e:  # the preview is a convenience; settings must still load
        log.warning("digest preview failed for %s: %s", ctx.tenant_id, e)
        preview = None
    log_rows = conn.execute(text("""SELECT kind, channel, recipient, subject, status, error, created_at
                                    FROM notification WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 20"""),
                            {"t": ctx.tenant_id}).mappings().all()
    last_job = conn.execute(text("""SELECT period, finished_at, outcome FROM scheduled_job
                                    WHERE tenant_id = :t AND job = 'daily' ORDER BY claimed_at DESC LIMIT 1"""),
                            {"t": ctx.tenant_id}).mappings().first()
    me = _me(conn, ctx)
    return {
        "slack": {"connected": bool(cfg["slack_enc"]), "hint": cfg["slack_hint"]},
        "email": {"available": channels.email_available(), "enabled": cfg["email_enabled"],
                  "mine": me["email_digest"], "address": me["email"]},
        "daily_sync": cfg["daily_sync"], "digest_hour": cfg["digest_hour"], "timezone": cfg["timezone"],
        "next_run": nxt.isoformat(), "preview": preview,
        "last_job": {**dict(last_job), "finished_at": str(last_job["finished_at"]) if last_job["finished_at"] else None}
        if last_job else None,
        "log": [{**dict(r), "created_at": str(r["created_at"])} for r in log_rows],
        "can_store_keys": bool(get_settings().secret_key), "can_edit": ctx.role in ("owner", "admin"),
    }


class SettingsIn(BaseModel):
    email_enabled: bool | None = None
    daily_sync: bool | None = None
    digest_hour: int | None = Field(None, ge=0, le=23)
    timezone: str | None = Field(None, max_length=64)


def _upsert(conn: Connection, tid: str, **values) -> None:
    cols = ", ".join(values)
    conn.execute(text(f"""INSERT INTO notify_setting (tenant_id, {cols}) VALUES (:t, {', '.join(':' + k for k in values)})
                          ON CONFLICT (tenant_id) DO UPDATE SET {', '.join(f'{k} = EXCLUDED.{k}' for k in values)},
                            updated_at = now()"""), {"t": tid, **values})


@router.put("")
def update(body: SettingsIn, ctx: Ctx = Depends(admin), conn: Connection = Depends(get_conn)) -> dict:
    values = body.model_dump(exclude_none=True)
    if "timezone" in values and jobs.zone(values["timezone"]).key != values["timezone"]:
        raise HTTPException(422, "Unknown time zone.")
    if not values:
        raise HTTPException(422, "Nothing to update.")
    _upsert(conn, ctx.tenant_id, **values)
    audit.by(conn, ctx, "notifications_changed", None, values)
    return {"ok": True}


class MineIn(BaseModel):
    email_digest: bool


@router.put("/me")
def update_mine(body: MineIn, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    conn.execute(text("UPDATE membership SET email_digest = :v WHERE tenant_id = :t AND user_id = :u"),
                 {"v": body.email_digest, "t": ctx.tenant_id, "u": ctx.user_id})
    return {"ok": True}


class SlackIn(BaseModel):
    url: str = Field(..., min_length=20, max_length=300)


@router.post("/slack")
def connect_slack(body: SlackIn, ctx: Ctx = Depends(admin), conn: Connection = Depends(get_conn)) -> dict:
    if not get_settings().secret_key:
        raise HTTPException(503, "This server has no FRED_SECRET_KEY set, so it cannot store the webhook safely.")
    try:
        url = channels.check_webhook(body.url)
        name = conn.execute(text("SELECT name FROM tenant WHERE id = :t"), {"t": ctx.tenant_id}).scalar()
        channels.send_slack(url, {"text": f"Fred is connected to this channel for {name}. "
                                          "The daily digest will arrive here."})
    except channels.DeliveryError as e:
        raise HTTPException(422, str(e)) from e
    _upsert(conn, ctx.tenant_id, slack_enc=secrets.encrypt(url), slack_hint=url[-4:])
    audit.by(conn, ctx, "slack_connected", f"…{url[-4:]}")
    return {"ok": True}


@router.delete("/slack")
def disconnect_slack(ctx: Ctx = Depends(admin), conn: Connection = Depends(get_conn)) -> dict:
    conn.execute(text("UPDATE notify_setting SET slack_enc = NULL, slack_hint = NULL WHERE tenant_id = :t"),
                 {"t": ctx.tenant_id})
    audit.by(conn, ctx, "slack_disconnected")
    return {"ok": True}


class TestIn(BaseModel):
    channel: str = Field(..., pattern="^(slack|email)$")


@router.post("/test")
def test(body: TestIn, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    me = _me(conn, ctx)
    try:
        jobs.send_test(ctx.tenant_id, body.channel, user=me)
    except channels.DeliveryError as e:
        raise HTTPException(422, str(e)) from e
    return {"ok": True, "to": "Slack" if body.channel == "slack" else me["email"]}


@router.post("/send-now")
def send_now(ctx: Ctx = Depends(admin)) -> dict:
    """Send the digest now even if there's nothing new (to see it). Marks what it carried as told."""
    return jobs.send_digest(ctx.tenant_id, force=True)
