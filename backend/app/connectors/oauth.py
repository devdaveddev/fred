"""OAuth 2.0 authorization-code connections (QuickBooks, Xero, Salesforce, Gusto, Google Ads).

Tokens are stored encrypted in connection.secret_enc as JSON; non-secret context the provider returns (QuickBooks
realm, Xero tenant, Salesforce instance URL, Gusto company) goes in connection.config. Access tokens are refreshed
shortly before they expire; providers that rotate refresh tokens (QuickBooks, Xero) get the new one saved at once.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import httpx
from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..config import get_settings
from . import http
from . import secrets as vault


@dataclass
class Provider:
    kind: str
    name: str
    authorize_url: str
    token_url: str
    scopes: list[str]
    extra_auth: dict = field(default_factory=dict)
    pkce: bool = True
    basic_auth: bool = True  # client credentials in the Authorization header (else in the form body)


def providers() -> dict[str, Provider]:
    s = get_settings()
    return {p.kind: p for p in [
        Provider("quickbooks", "QuickBooks", "https://appcenter.intuit.com/connect/oauth2",
                 "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer", ["com.intuit.quickbooks.accounting"],
                 pkce=False),
        Provider("xero", "Xero", "https://login.xero.com/identity/connect/authorize",
                 "https://identity.xero.com/connect/token",
                 ["openid", "profile", "email", "offline_access", "accounting.reports.read", "accounting.settings.read"]),
        Provider("salesforce", "Salesforce", f"{s.salesforce_login_url}/services/oauth2/authorize",
                 f"{s.salesforce_login_url}/services/oauth2/token", ["api", "refresh_token"], basic_auth=False),
        Provider("gusto", "Gusto", f"{'https://api.gusto-demo.com' if s.gusto_demo else 'https://api.gusto.com'}/oauth/authorize",
                 f"{'https://api.gusto-demo.com' if s.gusto_demo else 'https://api.gusto.com'}/oauth/token", [],
                 pkce=False, basic_auth=False),
        Provider("google_ads", "Google Ads", "https://accounts.google.com/o/oauth2/v2/auth",
                 "https://oauth2.googleapis.com/token", ["https://www.googleapis.com/auth/adwords"],
                 extra_auth={"access_type": "offline", "prompt": "consent"}, basic_auth=False),
    ]}


def client_creds(kind: str) -> tuple[str | None, str | None]:
    s = get_settings()
    return getattr(s, f"{kind}_client_id", None), getattr(s, f"{kind}_client_secret", None)


def configured(kind: str) -> bool:
    cid, sec = client_creds(kind)
    if kind == "google_ads" and not get_settings().google_ads_developer_token:
        return False
    return bool(cid and sec)


def redirect_uri() -> str:
    return f"{get_settings().public_url.rstrip('/')}/api/sources/oauth/callback"


def start(conn: Connection, tid: str, user_id: str, kind: str) -> str:
    p = providers()[kind]
    cid, _ = client_creds(kind)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    conn.execute(text("DELETE FROM oauth_state WHERE created_at < now() - interval '1 hour'"))
    conn.execute(text("INSERT INTO oauth_state (state_hash, tenant_id, kind, user_id, verifier) VALUES (:h, :t, :k, :u, :v)"),
                 {"h": hashlib.sha256(state.encode()).hexdigest(), "t": tid, "k": kind, "u": user_id, "v": verifier})
    q = {"response_type": "code", "client_id": cid, "redirect_uri": redirect_uri(), "state": state, **p.extra_auth}
    if p.scopes:
        q["scope"] = " ".join(p.scopes)
    if p.pkce:
        q["code_challenge"] = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q["code_challenge_method"] = "S256"
    return f"{p.authorize_url}?{urlencode(q)}"


def take_state(conn: Connection, state: str) -> tuple[str, str, str | None, str] | None:
    row = conn.execute(text("""DELETE FROM oauth_state WHERE state_hash = :h AND created_at > now() - interval '15 minutes'
                               RETURNING tenant_id::text, kind, user_id::text, verifier"""),
                       {"h": hashlib.sha256(state.encode()).hexdigest()}).first()
    return tuple(row) if row else None


def _token_call(kind: str, form: dict) -> dict:
    p = providers()[kind]
    cid, sec = client_creds(kind)
    with httpx.Client(timeout=30, transport=http.transport) as h:
        if p.basic_auth:
            r = h.post(p.token_url, data=form, auth=(cid, sec), headers={"Accept": "application/json"})
        else:
            r = h.post(p.token_url, data=form | {"client_id": cid, "client_secret": sec},
                       headers={"Accept": "application/json"})
    if r.status_code != 200:
        try:
            msg = r.json().get("error_description") or r.json().get("error")
        except ValueError:
            msg = r.text[:150]
        raise http.SourceError(f"{p.name} refused the authorization: {msg}")
    tok = r.json()
    tok["expires_at"] = time.time() + float(tok.get("expires_in") or 3600)
    return tok


def exchange(kind: str, code: str, verifier: str) -> dict:
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()}
    if providers()[kind].pkce:
        form["code_verifier"] = verifier
    return _token_call(kind, form)


def save(conn: Connection, tid: str, kind: str, tok: dict, config: dict, user_id: str | None) -> None:
    keep = {k: tok.get(k) for k in ("access_token", "refresh_token", "expires_at") if tok.get(k) is not None}
    conn.execute(text("""INSERT INTO connection (tenant_id, kind, secret_enc, secret_hint, config, created_by)
                         VALUES (:t, :k, :s, 'OAuth', CAST(:c AS JSONB), :u)
                         ON CONFLICT (tenant_id, kind) DO UPDATE SET secret_enc = EXCLUDED.secret_enc,
                           secret_hint = 'OAuth', config = connection.config || EXCLUDED.config, created_at = now()"""),
                 {"t": tid, "k": kind, "s": vault.encrypt(json.dumps(keep)), "c": json.dumps(config), "u": user_id})


def access_token(tid: str, kind: str) -> tuple[str, dict]:
    """A valid access token (refreshed and persisted if needed) plus the connection's config."""
    from ..db import engine

    with engine.begin() as conn:
        row = conn.execute(text("SELECT secret_enc, config FROM connection WHERE tenant_id = :t AND kind = :k FOR UPDATE"),
                           {"t": tid, "k": kind}).first()
        if not row:
            raise http.SourceError("Not connected.")
        tok, cfg = json.loads(vault.decrypt(row[0])), dict(row[1] or {})
        if tok.get("expires_at", 0) > time.time() + 120:
            return tok["access_token"], cfg
        if not tok.get("refresh_token"):
            raise http.SourceError(f"The {providers()[kind].name} connection expired. Reconnect it.")
        new = _token_call(kind, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        new.setdefault("refresh_token", tok["refresh_token"])  # rotated if the provider sent one; else keep ours
        conn.execute(text("UPDATE connection SET secret_enc = :s WHERE tenant_id = :t AND kind = :k"),
                     {"s": vault.encrypt(json.dumps({k: new.get(k) for k in ("access_token", "refresh_token", "expires_at")})),
                      "t": tid, "k": kind})
        return new["access_token"], cfg
