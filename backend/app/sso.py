"""Single sign-on with any OpenID Connect provider (Google Workspace, Okta, Microsoft Entra ID, OneLogin...).

Authorization-code flow with PKCE and a nonce. The ID token's RS256 signature is checked against the
provider's published keys (JWKS) with `cryptography`, then iss / aud / exp / nonce / email_verified.

Safety rules:
  * an email domain belongs to at most one workspace, and the owner's own domain must be one of them
  * "enforce SSO" (no passwords for the domain) is only allowed after one successful SSO sign-in, so a
    misconfiguration can't lock the company out
  * a session created by SSO is bound to that workspace: it can't switch into other workspaces the same
    email belongs to (another company's IdP must not grant access to them)
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import audit
from .auth import Ctx, current, require_owner, start_session
from .config import get_settings
from .connectors import secrets as vault
from .db import get_conn

router = APIRouter()
transport: httpx.BaseTransport | None = None  # tests inject a fake identity provider here
_cache: dict[str, tuple[float, dict]] = {}
STATE_TTL_S = 600


class SSOError(Exception):
    pass


def _http() -> httpx.Client:
    return httpx.Client(timeout=10, transport=transport, follow_redirects=False)


def _get_json(url: str) -> dict:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        with _http() as h:
            r = h.get(url)
    except httpx.HTTPError as e:
        raise SSOError(f"Could not reach the identity provider: {type(e).__name__}") from e
    if r.status_code != 200:
        raise SSOError(f"The identity provider answered {r.status_code} for {url}")
    data = r.json()
    _cache[url] = (time.time(), data)
    return data


def discover(issuer: str) -> dict:
    issuer = issuer.rstrip("/")
    if not issuer.startswith("https://"):
        raise SSOError("The issuer must be an https URL, for example https://accounts.google.com")
    doc = _get_json(f"{issuer}/.well-known/openid-configuration")
    if doc.get("issuer", "").rstrip("/") != issuer:
        raise SSOError("The provider's discovery document names a different issuer.")
    for k in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not str(doc.get(k, "")).startswith("https://"):
            raise SSOError(f"The provider's discovery document has no https {k}.")
    return doc


def callback_url() -> str:
    return f"{get_settings().public_url.rstrip('/')}/api/auth/sso/callback"


# ------------------------------------------------------------------ ID token verification
def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_id_token(token: str, jwks: dict, issuer: str, client_id: str, nonce: str, now: float | None = None) -> dict:
    try:
        h64, p64, s64 = token.split(".")
        header, claims = json.loads(_b64(h64)), json.loads(_b64(p64))
    except ValueError as e:
        raise SSOError("Malformed ID token.") from e
    if header.get("alg") != "RS256":
        raise SSOError(f"Unsupported token algorithm {header.get('alg')!r}; configure RS256.")
    keys = [k for k in jwks.get("keys", []) if k.get("kty") == "RSA"]
    key = next((k for k in keys if k.get("kid") == header.get("kid")), keys[0] if len(keys) == 1 else None)
    if key is None:
        raise SSOError("The token was signed with a key the provider doesn't publish.")
    pub = rsa.RSAPublicNumbers(int.from_bytes(_b64(key["e"]), "big"), int.from_bytes(_b64(key["n"]), "big")).public_key()
    try:
        pub.verify(_b64(s64), f"{h64}.{p64}".encode(), padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise SSOError("The ID token signature is invalid.") from e
    now = now or time.time()
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if str(claims.get("iss", "")).rstrip("/") != issuer.rstrip("/"):
        raise SSOError("The token was issued by a different provider.")
    if client_id not in auds or (len(auds) > 1 and claims.get("azp") != client_id):
        raise SSOError("The token was issued to a different application.")
    if float(claims.get("exp", 0)) < now - 60:
        raise SSOError("The sign-in took too long. Try again.")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SSOError("The sign-in response doesn't match this request. Try again.")
    if claims.get("email_verified") is False:
        raise SSOError("Your identity provider hasn't verified this email address.")
    email = claims.get("email") or (claims.get("preferred_username") if "@" in str(claims.get("preferred_username")) else None)
    if not email:
        raise SSOError("The identity provider didn't share an email address. Add the 'email' scope/claim.")
    claims["_email"] = str(email).strip().lower()
    return claims


# ------------------------------------------------------------------ configuration (owner)
class SSOIn(BaseModel):
    issuer: str = Field(..., max_length=300)
    client_id: str = Field(..., min_length=3, max_length=300)
    client_secret: str | None = Field(None, max_length=500)
    domains: list[str] = Field(..., min_length=1, max_length=10)
    default_role: str = Field("member", pattern="^(admin|member)$")
    enforce: bool = False
    enabled: bool = True


@router.get("/sso")
def get_config(ctx: Ctx = Depends(current), conn: Connection = Depends(get_conn)) -> dict:
    row = conn.execute(text("""SELECT issuer, client_id, domains, default_role, enforce, enabled, verified_at
                               FROM sso_config WHERE tenant_id = :t"""), {"t": ctx.tenant_id}).mappings().first()
    cfg = None if not row else {**dict(row), "verified_at": str(row["verified_at"]) if row["verified_at"] else None}
    return {"config": cfg, "callback_url": callback_url(), "can_edit": ctx.role == "owner",
            "your_domain": ctx.email.rsplit("@", 1)[-1], "can_store_keys": bool(get_settings().secret_key)}


@router.put("/sso")
def put_config(body: SSOIn, ctx: Ctx = Depends(require_owner), conn: Connection = Depends(get_conn)) -> dict:
    domains = sorted({d.strip().lower().lstrip("@") for d in body.domains if d.strip()})
    mine = ctx.email.rsplit("@", 1)[-1]
    if mine not in domains:
        raise HTTPException(422, f"Include your own email domain ({mine}). Domains are how Fred knows who belongs here.")
    taken = conn.execute(text("SELECT domain FROM sso_domain WHERE domain = ANY(:d) AND tenant_id <> :t"),
                         {"d": domains, "t": ctx.tenant_id}).scalars().all()
    if taken:
        raise HTTPException(409, f"Already used for single sign-on by another workspace: {', '.join(taken)}")
    existing = conn.execute(text("SELECT secret_enc, verified_at, issuer, client_id FROM sso_config WHERE tenant_id = :t"),
                            {"t": ctx.tenant_id}).first()
    if not body.client_secret and not existing:
        raise HTTPException(422, "Enter the client secret.")
    try:
        discover(body.issuer)
    except SSOError as e:
        raise HTTPException(422, str(e)) from e
    changed_idp = existing is not None and (existing[2] != body.issuer.rstrip("/") or existing[3] != body.client_id)
    verified = None if (existing is None or changed_idp or body.client_secret) else existing[1]
    if body.enforce and not verified:
        raise HTTPException(409, "Sign in with SSO once before enforcing it, so a mistake can't lock everyone out.")
    secret_enc = vault.encrypt(body.client_secret) if body.client_secret else existing[0]
    conn.execute(text("""
        INSERT INTO sso_config (tenant_id, issuer, client_id, secret_enc, domains, default_role, enforce, enabled,
                                verified_at)
        VALUES (:t, :i, :c, :s, :d, :r, :e, :en, :v)
        ON CONFLICT (tenant_id) DO UPDATE SET issuer = :i, client_id = :c, secret_enc = :s, domains = :d,
          default_role = :r, enforce = :e, enabled = :en, verified_at = :v, updated_at = now()"""),
        {"t": ctx.tenant_id, "i": body.issuer.rstrip("/"), "c": body.client_id.strip(), "s": secret_enc, "d": domains,
         "r": body.default_role, "e": body.enforce, "en": body.enabled, "v": verified})
    conn.execute(text("DELETE FROM sso_domain WHERE tenant_id = :t"), {"t": ctx.tenant_id})
    for d in domains:
        conn.execute(text("INSERT INTO sso_domain (domain, tenant_id) VALUES (:d, :t)"), {"d": d, "t": ctx.tenant_id})
    audit.by(conn, ctx, "sso_configured", body.issuer, {"domains": domains, "enforce": body.enforce,
                                                       "enabled": body.enabled, "secret_changed": bool(body.client_secret)})
    return {"ok": True, "verified": bool(verified)}


@router.delete("/sso")
def delete_config(ctx: Ctx = Depends(require_owner), conn: Connection = Depends(get_conn)) -> dict:
    conn.execute(text("DELETE FROM sso_domain WHERE tenant_id = :t"), {"t": ctx.tenant_id})
    conn.execute(text("DELETE FROM sso_config WHERE tenant_id = :t"), {"t": ctx.tenant_id})
    audit.by(conn, ctx, "sso_removed")
    return {"ok": True}


# ------------------------------------------------------------------ sign-in flow (public)
class StartIn(BaseModel):
    email: str = Field(..., max_length=254)


def _config_for_domain(conn: Connection, domain: str) -> dict | None:
    row = conn.execute(text("""SELECT c.* , c.tenant_id::text AS tid FROM sso_domain d
                               JOIN sso_config c ON c.tenant_id = d.tenant_id WHERE d.domain = :d AND c.enabled"""),
                       {"d": domain}).mappings().first()
    return dict(row) if row else None


@router.post("/auth/sso/start")
def start(body: StartIn, conn: Connection = Depends(get_conn)) -> dict:
    domain = body.email.strip().lower().rsplit("@", 1)[-1]
    cfg = _config_for_domain(conn, domain)
    if not cfg:
        raise HTTPException(404, "Single sign-on isn't set up for this email domain. Sign in with your password.")
    try:
        doc = discover(cfg["issuer"])
    except SSOError as e:
        raise HTTPException(502, str(e)) from e
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    conn.execute(text("DELETE FROM sso_state WHERE created_at < now() - interval '1 hour'"))
    conn.execute(text("INSERT INTO sso_state (state_hash, tenant_id, nonce, verifier) VALUES (:h, :t, :n, :v)"),
                 {"h": hashlib.sha256(state.encode()).hexdigest(), "t": cfg["tid"], "n": nonce, "v": verifier})
    q = {"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": callback_url(),
         "scope": "openid email profile", "state": state, "nonce": nonce, "code_challenge": challenge,
         "code_challenge_method": "S256", "login_hint": body.email.strip()}
    return {"redirect": f"{doc['authorization_endpoint']}?{urlencode(q)}"}


def _fail(msg: str) -> RedirectResponse:
    return RedirectResponse(f"{get_settings().public_url.rstrip('/')}/login?{urlencode({'sso_error': msg})}", 303)


@router.get("/auth/sso/callback")
def callback(request: Request, code: str | None = Query(None), state: str | None = Query(None),
             error: str | None = Query(None), error_description: str | None = Query(None),
             conn: Connection = Depends(get_conn)):
    if error:
        return _fail(error_description or error)
    if not code or not state:
        return _fail("The sign-in response was incomplete.")
    st = conn.execute(text("""DELETE FROM sso_state WHERE state_hash = :h RETURNING tenant_id::text, nonce, verifier,
                              created_at > now() - make_interval(secs => :ttl) AS fresh"""),
                      {"h": hashlib.sha256(state.encode()).hexdigest(), "ttl": STATE_TTL_S}).first()
    if not st or not st[3]:
        return _fail("This sign-in link expired. Start again.")
    tid, nonce, verifier, _ = st
    cfg = conn.execute(text("SELECT * FROM sso_config WHERE tenant_id = :t AND enabled"), {"t": tid}).mappings().first()
    if not cfg:
        return _fail("Single sign-on was turned off for this workspace.")
    try:
        doc = discover(cfg["issuer"])
        secret = vault.decrypt(cfg["secret_enc"])
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": callback_url(),
                "code_verifier": verifier, "client_id": cfg["client_id"]}
        basic = "client_secret_basic" in doc.get("token_endpoint_auth_methods_supported", ["client_secret_basic"])
        with _http() as h:
            r = h.post(doc["token_endpoint"], data=form if basic else form | {"client_secret": secret},
                       auth=(cfg["client_id"], secret) if basic else None)
        if r.status_code != 200:
            raise SSOError(f"The identity provider refused the sign-in ({r.status_code}): "
                           f"{r.json().get('error_description', r.text[:120]) if r.headers.get('content-type', '').startswith('application/json') else r.text[:120]}")
        claims = verify_id_token(r.json().get("id_token", ""), _get_json(doc["jwks_uri"]), cfg["issuer"],
                                 cfg["client_id"], nonce)
    except SSOError as e:
        audit.record(conn, "login_failed", tenant_id=tid, detail={"method": "sso", "reason": str(e)})
        return _fail(str(e))
    email = claims["_email"]
    if email.rsplit("@", 1)[-1] not in cfg["domains"]:
        return _fail(f"{email} isn't in a domain this workspace allows.")
    user = conn.execute(text("SELECT id::text FROM app_user WHERE lower(email) = :e"), {"e": email}).scalar()
    created = False
    if not user:
        user, created = str(uuid.uuid4()), True
        name = claims.get("name") or " ".join(x for x in (claims.get("given_name"), claims.get("family_name")) if x) or email
        conn.execute(text("INSERT INTO app_user (id, email, name, password_hash) VALUES (:id, :e, :n, :p)"),
                     {"id": user, "e": email, "n": str(name)[:120], "p": "sso$" + secrets.token_hex(16)})
    joined = conn.execute(text("""INSERT INTO membership (user_id, tenant_id, role) VALUES (:u, :t, :r)
                                  ON CONFLICT (user_id, tenant_id) DO NOTHING RETURNING 1"""),
                          {"u": user, "t": tid, "r": cfg["default_role"]}).first() is not None
    conn.execute(text("UPDATE sso_config SET verified_at = coalesce(verified_at, now()) WHERE tenant_id = :t"), {"t": tid})
    conn.execute(text("UPDATE app_user SET last_login_at = now() WHERE id = :u"), {"u": user})
    response = RedirectResponse(f"{get_settings().public_url.rstrip('/')}/", 303)
    start_session(conn, response, user, tid, request.headers.get("user-agent"), method="sso")
    audit.record(conn, "sso_login", tenant_id=tid, user_id=user, actor=email,
                 detail={"new_account": created, "joined_workspace": joined, "subject": claims.get("sub")})
    return response
