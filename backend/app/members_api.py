"""People in a workspace: invite by email, accept, change roles, remove.

Invite links carry a 256-bit token; only its SHA-256 is stored. Links expire after 7 days and work once.
If the server can send email the invite is emailed; the link is also returned once so an admin can share
it another way.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import audit
from .auth import (
    COOKIE,
    EMAIL_RE,
    Ctx,
    _token_hash,
    check_password,
    current,
    hash_password,
    require_admin,
    start_session,
)
from .config import get_settings
from .db import get_conn

router = APIRouter()
INVITE_DAYS = 7


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@router.get("/members")
def members(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    people = conn.execute(text("""
        SELECT u.id::text, u.name, u.email, m.role, m.created_at, u.last_login_at,
               u.password_hash LIKE 'sso$%' AS sso_only
        FROM membership m JOIN app_user u ON u.id = m.user_id WHERE m.tenant_id = :t
        ORDER BY (m.role = 'owner') DESC, u.name"""), {"t": ctx.tenant_id}).mappings().all()
    invites = conn.execute(text("""
        SELECT i.id::text, i.email, i.role, i.created_at, i.expires_at, u.name AS invited_by
        FROM invite i LEFT JOIN app_user u ON u.id = i.invited_by
        WHERE i.tenant_id = :t AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now()
        ORDER BY i.created_at DESC"""), {"t": ctx.tenant_id}).mappings().all()
    s = lambda v: str(v) if v else None
    return {"members": [{**dict(p), "created_at": s(p["created_at"]), "last_login_at": s(p["last_login_at"]),
                         "you": p["id"] == ctx.user_id} for p in people],
            "invites": [{**dict(i), "created_at": s(i["created_at"]), "expires_at": s(i["expires_at"])} for i in invites],
            "can_manage": ctx.role in ("owner", "admin"), "is_owner": ctx.role == "owner"}


class InviteIn(BaseModel):
    email: str = Field(..., max_length=254)
    role: str = Field("member", pattern="^(admin|member)$")


@router.post("/members/invite")
def invite(body: InviteIn, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    if conn.execute(text("""SELECT 1 FROM membership m JOIN app_user u ON u.id = m.user_id
                            WHERE m.tenant_id = :t AND lower(u.email) = :e"""), {"t": ctx.tenant_id, "e": email}).first():
        raise HTTPException(409, "That person is already in this workspace.")
    if body.role == "admin" and ctx.role != "owner":
        raise HTTPException(403, "Only the owner can invite admins.")
    # a new invite replaces any pending one for the same address
    conn.execute(text("""UPDATE invite SET revoked_at = now() WHERE tenant_id = :t AND lower(email) = :e
                         AND accepted_at IS NULL AND revoked_at IS NULL"""), {"t": ctx.tenant_id, "e": email})
    token = secrets.token_urlsafe(32)
    iid = conn.execute(text("""INSERT INTO invite (tenant_id, email, role, token_hash, invited_by, expires_at)
                               VALUES (:t, :e, :r, :h, :u, :x) RETURNING id::text"""),
                       {"t": ctx.tenant_id, "e": email, "r": body.role, "h": _hash(token), "u": ctx.user_id,
                        "x": datetime.now(UTC) + timedelta(days=INVITE_DAYS)}).scalar()
    link = f"{get_settings().public_url.rstrip('/')}/invite/{token}"
    emailed = False
    from .notify import channels

    if channels.email_available():
        try:
            channels.send_email(email, f"{ctx.name} invited you to {ctx.tenant_name} on Fred",
                                f"{ctx.name} invited you to join {ctx.tenant_name} on Fred.\n\nAccept: {link}\n\n"
                                f"The link works once and expires in {INVITE_DAYS} days.")
            emailed = True
        except channels.DeliveryError:
            emailed = False
    audit.by(conn, ctx, "invite_sent", email, {"role": body.role, "emailed": emailed})
    return {"id": iid, "link": link, "emailed": emailed}


@router.delete("/members/invites/{invite_id}")
def revoke_invite(invite_id: str, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    row = conn.execute(text("""UPDATE invite SET revoked_at = now() WHERE id = CAST(:i AS UUID) AND tenant_id = :t
                               AND accepted_at IS NULL RETURNING email"""), {"i": invite_id, "t": ctx.tenant_id}).first()
    if not row:
        raise HTTPException(404, "Invite not found.")
    audit.by(conn, ctx, "invite_revoked", row[0])
    return {"ok": True}


def _owners(conn: Connection, tid: str) -> int:
    return conn.execute(text("SELECT count(*) FROM membership WHERE tenant_id = :t AND role = 'owner'"), {"t": tid}).scalar()


class RoleIn(BaseModel):
    role: str = Field(..., pattern="^(owner|admin|member)$")


@router.put("/members/{user_id}/role")
def change_role(user_id: str, body: RoleIn, ctx: Ctx = Depends(require_admin), conn: Connection = Depends(get_conn)) -> dict:
    cur = conn.execute(text("SELECT role FROM membership WHERE tenant_id = :t AND user_id = CAST(:u AS UUID)"),
                       {"t": ctx.tenant_id, "u": user_id}).scalar()
    if cur is None:
        raise HTTPException(404, "Not a member of this workspace.")
    if ctx.role != "owner" and ("owner" in (cur, body.role) or "admin" in (cur, body.role)):
        raise HTTPException(403, "Only the owner can change admins and owners.")
    if cur == "owner" and body.role != "owner" and _owners(conn, ctx.tenant_id) == 1:
        raise HTTPException(409, "A workspace needs at least one owner. Make someone else owner first.")
    conn.execute(text("UPDATE membership SET role = :r WHERE tenant_id = :t AND user_id = CAST(:u AS UUID)"),
                 {"r": body.role, "t": ctx.tenant_id, "u": user_id})
    audit.by(conn, ctx, "role_changed", user_id, {"from": cur, "to": body.role})
    return {"ok": True}


@router.delete("/members/{user_id}")
def remove(user_id: str, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    """Admins remove people; anyone can leave."""
    leaving = user_id == ctx.user_id
    if not leaving and ctx.role not in ("owner", "admin"):
        raise HTTPException(403, "Only workspace owners and admins can remove people.")
    cur = conn.execute(text("SELECT role FROM membership WHERE tenant_id = :t AND user_id = CAST(:u AS UUID)"),
                       {"t": ctx.tenant_id, "u": user_id}).scalar()
    if cur is None:
        raise HTTPException(404, "Not a member of this workspace.")
    if cur == "owner" and _owners(conn, ctx.tenant_id) == 1:
        raise HTTPException(409, "The last owner can't leave. Make someone else owner first.")
    if cur in ("owner", "admin") and ctx.role != "owner" and not leaving:
        raise HTTPException(403, "Only the owner can remove admins.")
    conn.execute(text("DELETE FROM membership WHERE tenant_id = :t AND user_id = CAST(:u AS UUID)"),
                 {"t": ctx.tenant_id, "u": user_id})
    conn.execute(text("DELETE FROM user_session WHERE tenant_id = :t AND user_id = CAST(:u AS UUID)"),
                 {"t": ctx.tenant_id, "u": user_id})  # signed out of this workspace immediately
    conn.execute(text("UPDATE task SET owner_user_id = NULL WHERE tenant_id = :t AND owner_user_id = CAST(:u AS UUID)"),
                 {"t": ctx.tenant_id, "u": user_id})
    audit.by(conn, ctx, "member_removed", user_id, {"role": cur, "left": leaving})
    return {"ok": True}


# ------------------------------------------------------------------ accepting (public, token-based)
def _invite(conn: Connection, token: str) -> dict:
    row = conn.execute(text("""
        SELECT i.id::text, i.tenant_id::text, i.email, i.role, t.name AS workspace, u.name AS invited_by
        FROM invite i JOIN tenant t ON t.id = i.tenant_id LEFT JOIN app_user u ON u.id = i.invited_by
        WHERE i.token_hash = :h AND i.accepted_at IS NULL AND i.revoked_at IS NULL AND i.expires_at > now()"""),
        {"h": _hash(token)}).mappings().first()
    if not row:
        raise HTTPException(404, "This invite link is invalid, used or expired. Ask for a new one.")
    return dict(row)


@router.get("/auth/invite/{token}")
def invite_info(token: str, conn: Connection = Depends(get_conn)) -> dict:
    i = _invite(conn, token)
    has_account = conn.execute(text("SELECT 1 FROM app_user WHERE lower(email) = lower(:e)"), {"e": i["email"]}).first()
    return {"workspace": i["workspace"], "email": i["email"], "role": i["role"], "invited_by": i["invited_by"],
            "has_account": bool(has_account)}


class AcceptIn(BaseModel):
    name: str | None = Field(None, max_length=120)
    password: str | None = Field(None, max_length=200)


@router.post("/auth/invite/{token}/accept")
def accept(token: str, body: AcceptIn, request: Request, response: Response,
           conn: Connection = Depends(get_conn)) -> dict:
    """New people set a name and password. People with an account prove it's them, either by already being
    signed in as the invited address or by entering their password."""
    from .auth import verify_password

    i = _invite(conn, token)
    user = conn.execute(text("SELECT id::text, password_hash FROM app_user WHERE lower(email) = lower(:e)"),
                        {"e": i["email"]}).first()
    if user:
        signed_in = False
        cookie = request.cookies.get(COOKIE)
        if cookie:
            signed_in = conn.execute(text("""SELECT 1 FROM user_session WHERE token_hash = :h AND user_id = :u
                                             AND expires_at > now()"""), {"h": _token_hash(cookie), "u": user[0]}).first()
        if not signed_in and not (body.password and verify_password(body.password, user[1])):
            raise HTTPException(401, "Enter the password for this account to accept.")
        uid = user[0]
    else:
        if not body.name or not body.password or len(body.password) < 10:
            raise HTTPException(422, "Enter your name and a password of at least 10 characters.")
        check_password(body.password, i["email"])
        uid = str(uuid.uuid4())
        conn.execute(text("INSERT INTO app_user (id, email, name, password_hash) VALUES (:id, :e, :n, :p)"),
                     {"id": uid, "e": i["email"], "n": body.name.strip(), "p": hash_password(body.password)})
    conn.execute(text("""INSERT INTO membership (user_id, tenant_id, role) VALUES (:u, :t, :r)
                         ON CONFLICT (user_id, tenant_id) DO NOTHING"""), {"u": uid, "t": i["tenant_id"], "r": i["role"]})
    conn.execute(text("UPDATE invite SET accepted_at = now() WHERE id = CAST(:i AS UUID)"), {"i": i["id"]})
    start_session(conn, response, uid, i["tenant_id"], request.headers.get("user-agent"))
    audit.record(conn, "invite_accepted", tenant_id=i["tenant_id"], user_id=uid, actor=i["email"],
                 detail={"role": i["role"], "new_account": not user})
    return {"ok": True}
