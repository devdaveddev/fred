"""Audit log: who did what, when, from where. Append-only; never stores secrets.

Request metadata (IP, user agent) arrives through a context variable set by middleware, so any code path
can record an event without threading the Request object through.
"""
from __future__ import annotations

import json
import logging
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.engine import Connection

log = logging.getLogger("fred.audit")
request_meta: ContextVar[dict | None] = ContextVar("request_meta", default=None)

# Actions shown in the UI, grouped for filtering
ACTIONS = {
    "auth": ["login", "login_failed", "logout", "signup", "sso_login", "session_revoked", "password_login_blocked"],
    "people": ["invite_sent", "invite_revoked", "invite_accepted", "role_changed", "member_removed"],
    "data": ["source_connected", "source_disconnected", "file_imported", "mapping_changed", "writeback_changed",
             "workspace_exported", "workspace_deleted"],
    "settings": ["role_hired", "role_released", "notifications_changed", "slack_connected", "slack_disconnected",
                 "sso_configured", "sso_removed"],
    "work": ["document_created", "budget_round_opened", "budget_submitted", "budget_approved", "budget_returned",
             "budget_published"],
}


def record(conn: Connection, action: str, *, tenant_id: str | None = None, user_id: str | None = None,
           actor: str | None = None, target: str | None = None, detail: dict | None = None) -> None:
    meta = request_meta.get() or {}
    conn.execute(text("""INSERT INTO audit_event (tenant_id, user_id, actor, action, target, detail, ip, user_agent)
                         VALUES (:t, :u, :a, :ac, :tg, CAST(:d AS JSONB), :ip, :ua)"""),
                 {"t": tenant_id, "u": user_id, "a": actor, "ac": action, "tg": target,
                  "d": json.dumps(detail or {}, default=str), "ip": meta.get("ip"), "ua": (meta.get("ua") or "")[:200]})


def by(conn: Connection, ctx, action: str, target: str | None = None, detail: dict | None = None) -> None:
    """Record an action taken by the signed-in user (ctx: auth.Ctx)."""
    record(conn, action, tenant_id=ctx.tenant_id, user_id=ctx.user_id, actor=ctx.email, target=target, detail=detail)
