"""Accounts, workspaces and sessions.

* Passwords: scrypt (stdlib), per-user salt, constant-time compare.
* Sessions: 256-bit random token in an httpOnly, SameSite=Lax cookie; only its SHA-256 is stored.
* Every data endpoint resolves the tenant from the session, never from the request body.
* Brute force: failed logins are throttled per email + IP, in the database (survives restarts, shared by replicas).
* Every security-relevant action is written to the audit log.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import audit, modules
from .config import get_settings
from .db import engine, get_conn

COOKIE = "fred_session"
router = APIRouter(prefix="/auth")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ---------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, dk = stored.split("$")
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(dk))
    except Exception:
        return False


COMMON = {"password12", "password123", "1234567890", "qwertyuiop", "letmein123", "welcome123", "iloveyou12",
          "changeme12", "administrator", "passw0rd123", "1q2w3e4r5t", "qwerty1234"}


def check_password(pw: str, email: str) -> None:
    """Length is enforced by the schema; also refuse well-known passwords and ones built from the email."""
    low = pw.lower()
    local = email.split("@")[0].lower()
    if low in COMMON or len(set(pw)) < 4 or (len(local) >= 4 and local in low):
        raise HTTPException(422, "Choose a less guessable password: avoid common passwords and your email name.")


# ---------------- sessions
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(conn: Connection, response: Response, user_id: str, tenant_id: str, ua: str | None = None,
                  method: str = "password") -> None:
    s = get_settings()
    token = secrets.token_urlsafe(32)
    meta = audit.request_meta.get() or {}
    conn.execute(text("""INSERT INTO user_session (token_hash, user_id, tenant_id, expires_at, user_agent, ip,
                                                   last_seen_at, auth_method)
                         VALUES (:h, :u, :t, :e, :ua, :ip, now(), :m)"""),
                 {"h": _token_hash(token), "u": user_id, "t": tenant_id,
                  "e": datetime.now(UTC) + timedelta(days=s.session_days), "ua": (ua or meta.get("ua") or "")[:200],
                  "ip": meta.get("ip"), "m": method})
    response.set_cookie(COOKIE, token, max_age=s.session_days * 86400, httponly=True, samesite="lax",
                        secure=s.cookie_secure, path="/")


class Ctx(BaseModel):
    user_id: str
    email: str
    name: str
    tenant_id: str
    tenant_name: str
    role: str
    session_id: str | None = None
    auth_method: str = "password"


def current(request: Request, conn: Connection = Depends(get_conn)) -> Ctx:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Sign in to continue")
    row = conn.execute(text("""
        SELECT u.id::text AS user_id, u.email, u.name, t.id::text AS tenant_id, t.name AS tenant_name, m.role,
               s.id::text AS session_id, s.auth_method, s.last_seen_at < now() - interval '5 minutes' AS stale
        FROM user_session s
        JOIN app_user u ON u.id = s.user_id
        JOIN tenant t ON t.id = s.tenant_id
        JOIN membership m ON m.user_id = u.id AND m.tenant_id = t.id
        WHERE s.token_hash = :h AND s.expires_at > now()"""), {"h": _token_hash(token)}).mappings().first()
    if not row:
        raise HTTPException(401, "Your session has expired. Sign in again.")
    row = dict(row)
    if row.pop("stale") is not False:  # cheap: at most one write per session every five minutes
        conn.execute(text("UPDATE user_session SET last_seen_at = now() WHERE id = CAST(:s AS UUID)"),
                     {"s": row["session_id"]})
    return Ctx(**row)


def require_admin(ctx: Ctx = Depends(current)) -> Ctx:
    if ctx.role not in ("owner", "admin"):
        raise HTTPException(403, "Only workspace owners and admins can do this.")
    return ctx


def require_owner(ctx: Ctx = Depends(current)) -> Ctx:
    if ctx.role != "owner":
        raise HTTPException(403, "Only the workspace owner can do this.")
    return ctx


def tenant_row(conn: Connection, ctx: Ctx) -> dict:
    row = conn.execute(text("""SELECT id::text AS id, slug, name, currency, gross_margin, annual_wacc, data_source
                               FROM tenant WHERE id = :t"""), {"t": ctx.tenant_id}).mappings().first()
    return dict(row)


def current_tenant(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    return tenant_row(conn, ctx)


def require_module(module_id: str):
    def dep(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> Ctx:
        if module_id not in modules.hired(conn, ctx.tenant_id):
            raise HTTPException(403, f"Hire the {modules.MODULES[module_id].title} to use this page.")
        return ctx
    return dep


# ---------------- throttling
def _throttle_key(request: Request, email: str) -> str:
    return f"{email.lower()}|{request.client.host if request.client else '-'}"


def _check_throttle(conn: Connection, key: str) -> None:
    n = conn.execute(text("SELECT count(*) FROM login_attempt WHERE key = :k AND at > now() - interval '15 minutes'"),
                     {"k": key}).scalar()
    if n >= 5:
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")


def _fail(key: str) -> None:
    """Recorded in its own transaction: the request's transaction rolls back when we raise 401."""
    with engine.begin() as c:
        c.execute(text("INSERT INTO login_attempt (key) VALUES (:k)"), {"k": key})
        c.execute(text("DELETE FROM login_attempt WHERE at < now() - interval '1 day'"))


# ---------------- endpoints
class SignupIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=10, max_length=200)
    company: str = Field(..., min_length=1, max_length=120)
    demo_data: bool = True


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/signup")
def signup(body: SignupIn, request: Request, response: Response, background: BackgroundTasks,
           conn: Connection = Depends(get_conn)) -> dict:
    if not get_settings().allow_signup:
        raise HTTPException(403, "Sign-ups are closed on this instance.")
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Enter a valid email address.")
    if conn.execute(text("SELECT 1 FROM app_user WHERE lower(email) = :e"), {"e": email}).first():
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    check_password(body.password, email)
    uid, tid = str(uuid.uuid4()), str(uuid.uuid4())
    slug = re.sub(r"[^a-z0-9]+", "-", body.company.lower()).strip("-")[:40] + "-" + tid[:6]
    conn.execute(text("""INSERT INTO tenant (id, slug, name, currency, gross_margin, annual_wacc, data_source)
                         VALUES (:id, :slug, :name, 'USD', 0.78, 0.14, :src)"""),
                 {"id": tid, "slug": slug, "name": body.company.strip(), "src": "demo" if body.demo_data else "empty"})
    conn.execute(text("INSERT INTO app_user (id, email, name, password_hash) VALUES (:id, :e, :n, :p)"),
                 {"id": uid, "e": email, "n": body.name.strip(), "p": hash_password(body.password)})
    conn.execute(text("INSERT INTO membership (user_id, tenant_id, role) VALUES (:u, :t, 'owner')"),
                 {"u": uid, "t": tid})
    if body.demo_data:
        from .seed import seed_workspace

        seed_workspace(conn, tid)
        from .team_api import warm_tenant

        background.add_task(warm_tenant, tid)  # runs after the transaction commits
    start_session(conn, response, uid, tid, request.headers.get("user-agent"))
    audit.record(conn, "signup", tenant_id=tid, user_id=uid, actor=email)
    return {"ok": True, "onboarding": True}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, conn: Connection = Depends(get_conn)) -> dict:
    email = body.email.strip().lower()
    key = _throttle_key(request, email)
    _check_throttle(conn, key)
    domain = email.rsplit("@", 1)[-1]
    if conn.execute(text("""SELECT 1 FROM sso_domain d JOIN sso_config c ON c.tenant_id = d.tenant_id
                            WHERE d.domain = :d AND c.enabled AND c.enforce"""), {"d": domain}).first():
        with engine.begin() as c:  # survives the 403 rollback
            audit.record(c, "password_login_blocked", actor=email)
        raise HTTPException(403, "Your company signs in with single sign-on. Use “Sign in with SSO”.")
    row = conn.execute(text("""SELECT u.id::text, u.password_hash, m.tenant_id::text FROM app_user u
                               JOIN membership m ON m.user_id = u.id
                               WHERE lower(u.email) = :e
                               ORDER BY (SELECT max(s.created_at) FROM user_session s
                                         WHERE s.user_id = u.id AND s.tenant_id = m.tenant_id) DESC NULLS LAST,
                                        m.created_at LIMIT 1"""), {"e": email}).first()
    if not row or not verify_password(body.password, row[1]):
        _fail(key)
        with engine.begin() as c:  # survives the 401 rollback
            audit.record(c, "login_failed", actor=email, tenant_id=row[2] if row else None)
        raise HTTPException(401, "Email or password is incorrect.")
    conn.execute(text("DELETE FROM login_attempt WHERE key = :k"), {"k": key})
    conn.execute(text("UPDATE app_user SET last_login_at = now() WHERE id = :u"), {"u": row[0]})
    start_session(conn, response, row[0], row[2], request.headers.get("user-agent"))
    audit.record(conn, "login", tenant_id=row[2], user_id=row[0], actor=email)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, conn: Connection = Depends(get_conn)) -> dict:
    token = request.cookies.get(COOKIE)
    if token:
        row = conn.execute(text("""DELETE FROM user_session WHERE token_hash = :h
                                   RETURNING user_id::text, tenant_id::text"""), {"h": _token_hash(token)}).first()
        if row:
            audit.record(conn, "logout", tenant_id=row[1], user_id=row[0])
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    t = tenant_row(conn, ctx)
    h = modules.hired(conn, ctx.tenant_id)
    spaces = conn.execute(text("""SELECT t.id::text AS id, t.name, m.role FROM membership m
                                  JOIN tenant t ON t.id = m.tenant_id WHERE m.user_id = :u ORDER BY t.name"""),
                          {"u": ctx.user_id}).mappings().all()
    return {"user": {"id": ctx.user_id, "email": ctx.email, "name": ctx.name, "role": ctx.role,
                     "auth_method": ctx.auth_method},
            "workspace": {"id": t["id"], "name": t["name"], "data_source": t["data_source"]},
            "workspaces": [dict(w) for w in spaces],
            "hired": sorted(h),
            "nav": [{"module": m.id, "title": m.title, "pages": m.pages} for m in modules.MODULES.values() if m.id in h]}


class SwitchIn(BaseModel):
    tenant_id: str


@router.post("/switch")
def switch(body: SwitchIn, request: Request, response: Response, ctx: Ctx = Depends(current),
           conn: Connection = Depends(get_conn)) -> dict:
    """Move this browser to another workspace you belong to. A session that came from one company's SSO
    can't hop into other workspaces: sign in to those separately."""
    if ctx.auth_method == "sso" and body.tenant_id != ctx.tenant_id:
        raise HTTPException(403, "You signed in with your company's SSO. Sign out and sign in again to open "
                                 "another workspace.")
    ok = conn.execute(text("SELECT 1 FROM membership WHERE user_id = :u AND tenant_id = CAST(:t AS UUID)"),
                      {"u": ctx.user_id, "t": body.tenant_id}).first()
    if not ok:
        raise HTTPException(404, "Workspace not found.")
    conn.execute(text("DELETE FROM user_session WHERE id = CAST(:s AS UUID)"), {"s": ctx.session_id})
    start_session(conn, response, ctx.user_id, body.tenant_id, request.headers.get("user-agent"))
    return {"ok": True}


# ---------------- the team (hiring board)
team_router = APIRouter(prefix="/team")


@team_router.get("")
def team_roster(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    return {"roles": modules.roster(conn, ctx.tenant_id)}


@team_router.post("/{module_id}/hire")
def hire(module_id: str, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    if ctx.role not in ("owner", "admin"):
        raise HTTPException(403, "Only workspace owners and admins can change the team.")
    modules.set_hired(conn, ctx.tenant_id, module_id, True)
    audit.by(conn, ctx, "role_hired", module_id)
    return {"roles": modules.roster(conn, ctx.tenant_id)}


@team_router.post("/{module_id}/release")
def release(module_id: str, ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    if ctx.role not in ("owner", "admin"):
        raise HTTPException(403, "Only workspace owners and admins can change the team.")
    modules.set_hired(conn, ctx.tenant_id, module_id, False)
    audit.by(conn, ctx, "role_released", module_id)
    return {"roles": modules.roster(conn, ctx.tenant_id)}
