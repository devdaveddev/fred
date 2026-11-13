"""Security & compliance surface: audit log, sessions, data export, workspace deletion."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import audit
from .auth import COOKIE, Ctx, current, require_admin, require_owner
from .db import get_conn

router = APIRouter(prefix="/security")
# Columns never exported or shown, even to owners: credentials and token hashes
SECRET_COLUMNS = {"secret_enc", "slack_enc", "token_hash", "password_hash", "verifier", "nonce", "state_hash"}
SKIP_TABLES = {"sso_state", "login_attempt", "user_session", "alembic_version"}


@router.get("/audit")
def audit_log(ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn),
              group: str | None = Query(None), before: int | None = Query(None), limit: int = Query(50, le=200)) -> dict:
    actions = audit.ACTIONS.get(group) if group else None
    rows = conn.execute(text("""
        SELECT id, actor, action, target, detail, ip, user_agent, created_at FROM audit_event
        WHERE tenant_id = :t AND (CAST(:a AS TEXT[]) IS NULL OR action = ANY(CAST(:a AS TEXT[])))
          AND (CAST(:b AS BIGINT) IS NULL OR id < :b)
        ORDER BY id DESC LIMIT :n"""), {"t": ctx.tenant_id, "a": actions, "b": before, "n": limit}).mappings().all()
    return {"events": [{**dict(r), "created_at": str(r["created_at"])} for r in rows], "groups": list(audit.ACTIONS),
            "next": rows[-1]["id"] if len(rows) == limit else None}


@router.get("/sessions")
def sessions(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    rows = conn.execute(text("""
        SELECT s.id::text, t.name AS workspace, s.created_at, s.last_seen_at, s.expires_at, s.ip, s.user_agent,
               s.auth_method, s.id = CAST(:me AS UUID) AS current
        FROM user_session s JOIN tenant t ON t.id = s.tenant_id
        WHERE s.user_id = :u AND s.expires_at > now() ORDER BY s.last_seen_at DESC NULLS LAST"""),
        {"u": ctx.user_id, "me": ctx.session_id}).mappings().all()
    return {"sessions": [{**dict(r), **{k: str(r[k]) if r[k] else None for k in ("created_at", "last_seen_at", "expires_at")}}
                         for r in rows]}


@router.delete("/sessions/{session_id}")
def revoke(session_id: str, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    n = conn.execute(text("DELETE FROM user_session WHERE id = CAST(:s AS UUID) AND user_id = :u"),
                     {"s": session_id, "u": ctx.user_id}).rowcount
    if not n:
        raise HTTPException(404, "Session not found.")
    audit.by(conn, ctx, "session_revoked", session_id)
    return {"ok": True}


@router.post("/sessions/revoke-others")
def revoke_others(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    n = conn.execute(text("DELETE FROM user_session WHERE user_id = :u AND id <> CAST(:s AS UUID)"),
                     {"u": ctx.user_id, "s": ctx.session_id}).rowcount
    audit.by(conn, ctx, "session_revoked", "all other sessions", {"count": n})
    return {"ok": True, "revoked": n}


# ------------------------------------------------------------------ export & deletion (owner)
def tenant_tables(conn: Connection) -> list[str]:
    return [r[0] for r in conn.execute(text("""
        SELECT c.table_name FROM information_schema.columns c
        JOIN information_schema.tables t ON t.table_name = c.table_name AND t.table_schema = c.table_schema
        WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id' AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name""")) if r[0] not in SKIP_TABLES]


def _csv(conn: Connection, table: str, tid: str) -> str:
    cols = [r[0] for r in conn.execute(text("""SELECT column_name FROM information_schema.columns
                                              WHERE table_schema = 'public' AND table_name = :t ORDER BY ordinal_position"""),
                                       {"t": table}) if r[0] not in SECRET_COLUMNS]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    sel = ", ".join(f'"{c}"' for c in cols)
    for row in conn.execute(text(f'SELECT {sel} FROM "{table}" WHERE tenant_id = :t'), {"t": tid}):
        w.writerow([json.dumps(v, default=str) if isinstance(v, dict | list) else v for v in row])
    return out.getvalue()


@router.get("/export")
def export(ctx: Ctx = Depends(require_owner), conn: Connection = Depends(get_conn)) -> Response:
    """Everything this workspace holds, as CSV files in a zip. Credentials are never included."""
    buf = io.BytesIO()
    tables = tenant_tables(conn)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for t in tables:
            z.writestr(f"{t}.csv", _csv(conn, t, ctx.tenant_id))
        people = conn.execute(text("""SELECT u.name, u.email, m.role, m.created_at FROM membership m
                                      JOIN app_user u ON u.id = m.user_id WHERE m.tenant_id = :t"""), {"t": ctx.tenant_id})
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["name", "email", "role", "joined"])
        w.writerows(people)
        z.writestr("members.csv", out.getvalue())
        z.writestr("README.txt", f"Export of {ctx.tenant_name} from Fred, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.\n"
                                 "One CSV per table. Stored credentials (API keys, webhooks, SSO secrets) are excluded.\n")
    audit.by(conn, ctx, "workspace_exported", None, {"tables": len(tables)})
    name = f"fred-export-{datetime.now(UTC):%Y%m%d}.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


class DeleteIn(BaseModel):
    confirm: str


@router.post("/delete-workspace")
def delete_workspace(body: DeleteIn, response: Response, ctx: Ctx = Depends(require_owner),
                     conn: Connection = Depends(get_conn)) -> dict:
    """Permanently delete the workspace and all its data. People keep their accounts only if they belong to
    another workspace."""
    if body.confirm.strip() != ctx.tenant_name:
        raise HTTPException(422, "Type the workspace name exactly to confirm.")
    tid = ctx.tenant_id
    users = conn.execute(text("SELECT user_id::text FROM membership WHERE tenant_id = :t"), {"t": tid}).scalars().all()
    from .seed import wipe_tenant_data

    wipe_tenant_data(conn, tid)
    remaining = [t for t in tenant_tables(conn) if t not in ("tenant",)]
    for _ in range(3):  # a few passes so foreign-key order doesn't matter
        for t in remaining:
            conn.execute(text("SAVEPOINT del"))
            try:
                conn.execute(text(f'DELETE FROM "{t}" WHERE tenant_id = :t'), {"t": tid})
                conn.execute(text("RELEASE SAVEPOINT del"))
            except Exception:
                conn.execute(text("ROLLBACK TO SAVEPOINT del"))
    conn.execute(text("DELETE FROM user_session WHERE tenant_id = :t"), {"t": tid})
    conn.execute(text("DELETE FROM tenant WHERE id = :t"), {"t": tid})
    orphans = conn.execute(text("""DELETE FROM app_user u WHERE u.id = ANY(CAST(:ids AS UUID[]))
                                   AND NOT EXISTS (SELECT 1 FROM membership m WHERE m.user_id = u.id) RETURNING 1"""),
                           {"ids": users}).rowcount
    audit.record(conn, "workspace_deleted", user_id=ctx.user_id, actor=ctx.email, target=tid,
                 detail={"name": ctx.tenant_name, "accounts_deleted": orphans})
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}
