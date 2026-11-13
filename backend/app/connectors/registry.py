"""Every connectable source in one place: what it gives, how it authenticates, how it syncs.

A sync is split in two: fetch() talks to the provider outside any database transaction, write() stores the
result inside one, so a failed or slow API never leaves half-written data.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..config import get_settings
from . import accounting, hubspot, more_sources, oauth, pipeline, salesforce, stripe
from . import secrets as vault
from .http import Api, SourceError


@dataclass
class Source:
    kind: str
    name: str
    category: str  # billing | crm | accounting | payroll | ads | support
    gives: str
    auth: str  # key | oauth
    how: str
    key_label: str | None = None
    fields: list[dict] = field(default_factory=list)  # non-secret settings: [{"name", "label", "required"}]
    fetch: Callable[[str, dict, dict], Any] | None = None  # (tenant_id, secret, config) -> payload
    write: Callable[[Connection, str, Any], tuple[dict, list[str]]] | None = None
    check: Callable[[dict, dict], dict | None] | None = None  # validate key credentials -> extra config
    writeback: bool = False


def _window() -> tuple[date, date]:
    last = pipeline.last_closed_month()
    start = date(last.year - 2, last.month, 1)
    return start, date.today()


# ------------------------------------------------------------------ fetch / write per source
def _stripe_fetch(tid, secret, cfg):
    cap = pipeline.last_closed_month()
    return stripe.normalise(stripe.fetch(stripe.StripeClient(secret["key"], base=get_settings().stripe_api_base),
                                         since=date(cap.year - 3, cap.month, 1)), tid, cap)


def _billing_write(conn, tid, norm):
    return norm["stats"] | pipeline.write_billing(conn, tid, norm["accounts"], norm["mrr"]), list(norm["warnings"])


def _hubspot_fetch(tid, secret, cfg):
    return hubspot.normalise(hubspot.fetch(hubspot.HubSpotClient(secret["key"], base=get_settings().hubspot_api_base)), tid)


def _crm_write(conn, tid, norm):
    return norm["stats"] | pipeline.write_crm(conn, tid, norm["profiles"], norm["leads"]), []


def _qbo_fetch(tid, secret, cfg):
    token, cfg = oauth.access_token(tid, "quickbooks")
    return accounting.qbo_fetch(token, cfg["realm_id"], pipeline.last_closed_month())


def _xero_fetch(tid, secret, cfg):
    token, cfg = oauth.access_token(tid, "xero")
    return accounting.xero_fetch(token, cfg["tenant_id"], pipeline.last_closed_month())


def _books_write(conn, tid, payload):
    from ..ml.embeddings import embed

    stats = pipeline.write_gl(conn, tid, payload["gl"], embed=embed)
    warnings = []
    if payload["cash"]:
        stats |= {"cash_months": pipeline.write_cash(conn, tid, payload["cash"]).get("months", len(payload["cash"]))}
    else:
        warnings.append("No bank balances found; cash runway uses the last cash upload, if any.")
    return stats, warnings


def _sf_fetch(tid, secret, cfg):
    token, cfg = oauth.access_token(tid, "salesforce")
    return salesforce.normalise(salesforce.fetch(salesforce.client(token, cfg["instance_url"])), tid)


def _gusto_fetch(tid, secret, cfg):
    token, cfg = oauth.access_token(tid, "gusto")
    return more_sources.gusto_roster(more_sources.gusto_fetch(more_sources.gusto_client(token), cfg["company_uuid"]))


def _payroll_write(conn, tid, roster):
    return pipeline.write_payroll(conn, tid, roster), []


def _gads_fetch(tid, secret, cfg):
    token, cfg = oauth.access_token(tid, "google_ads")
    if not cfg.get("customer_id"):
        raise SourceError("Enter your Google Ads customer ID to finish connecting.")
    return more_sources.google_ads_fetch(token, cfg["customer_id"], *_window(), cfg.get("login_customer_id"))


def _meta_fetch(tid, secret, cfg):
    return more_sources.meta_fetch(secret["key"], cfg["ad_account_id"], *_window())


def _spend_writer(channel: str):
    def write(conn, tid, rows):
        return pipeline.write_spend(conn, tid, rows, channels=[channel]), ([] if rows else ["No spend in the last two years."])
    return write


def _zendesk_fetch(tid, secret, cfg):
    return more_sources.zendesk_fetch(more_sources.zendesk_client(cfg["subdomain"], cfg["email"], secret["key"]))


def _intercom_fetch(tid, secret, cfg):
    return more_sources.intercom_fetch(secret["key"])


def _feedback_writer(source: str):
    def write(conn, tid, rows):
        return pipeline.write_feedback(conn, tid, rows, source=source), []
    return write


# ------------------------------------------------------------------ credential checks for key-based sources
def _check_stripe(secret, cfg):
    stripe.StripeClient(secret["key"], base=get_settings().stripe_api_base).check()


def _check_hubspot(secret, cfg):
    hubspot.HubSpotClient(secret["key"], base=get_settings().hubspot_api_base).check()


def _check_zendesk(secret, cfg):
    me = more_sources.zendesk_client(cfg.get("subdomain", ""), cfg.get("email", ""), secret["key"]).get("/api/v2/users/me.json")
    if not (me.get("user") or {}).get("id"):
        raise SourceError("Zendesk didn't accept that email and token.")


def _check_intercom(secret, cfg):
    Api("Intercom", "https://api.intercom.io", headers={"Authorization": f"Bearer {secret['key']}",
                                                         "Intercom-Version": "2.11"}).get("/me")


def _check_meta(secret, cfg):
    acct = cfg.get("ad_account_id", "")
    acct = acct if acct.startswith("act_") else f"act_{acct}"
    Api("Meta", f"https://graph.facebook.com/{get_settings().meta_graph_version}",
        hints={400: "Meta didn't accept that token or ad account ID."}).get(f"/{acct}", params={"fields": "name",
                                                                                               "access_token": secret["key"]})


SOURCES: dict[str, Source] = {s.kind: s for s in [
    Source("stripe", "Stripe", "billing", "Customers and recurring revenue (MRR) from invoices", "key",
           "Stripe Dashboard → Developers → API keys → Create restricted key. Grant Read on Customers and Invoices.",
           key_label="Restricted key (rk_live_…)", fetch=_stripe_fetch, write=_billing_write, check=_check_stripe),
    Source("hubspot", "HubSpot", "crm", "Leads, company profiles and descriptions", "key",
           "HubSpot Settings → Integrations → Private apps → Create. Scopes: crm.objects.contacts.read and "
           "crm.objects.companies.read. For write-back also add the matching .write scopes and crm.schemas.contacts.write "
           "and crm.schemas.companies.write.",
           key_label="Private app token (pat-…)", fetch=_hubspot_fetch, write=_crm_write, check=_check_hubspot,
           writeback=True),
    Source("salesforce", "Salesforce", "crm", "Leads and accounts, with lead source and conversion", "oauth",
           "Sign in with a Salesforce user that can read Leads and Accounts.", fetch=_sf_fetch, write=_crm_write,
           writeback=True),
    Source("quickbooks", "QuickBooks Online", "accounting", "Monthly P&L by account and bank balances", "oauth",
           "Sign in with a QuickBooks user who can see reports.", fetch=_qbo_fetch, write=_books_write),
    Source("xero", "Xero", "accounting", "Monthly P&L by account and bank balances", "oauth",
           "Sign in and pick the organisation.", fetch=_xero_fetch, write=_books_write),
    Source("gusto", "Gusto", "payroll", "Team roster: titles, departments, start and end dates, pay", "oauth",
           "Sign in as a Gusto admin.", fetch=_gusto_fetch, write=_payroll_write),
    Source("google_ads", "Google Ads", "ads", "Daily paid-search spend", "oauth",
           "Sign in with Google, then enter the customer ID shown at the top of Google Ads (123-456-7890).",
           fields=[{"name": "customer_id", "label": "Customer ID", "required": True},
                   {"name": "login_customer_id", "label": "Manager account ID (if you use one)", "required": False}],
           fetch=_gads_fetch, write=_spend_writer("paid_search")),
    Source("meta_ads", "Meta Ads", "ads", "Daily Facebook and Instagram ad spend", "key",
           "Meta Business Settings → System users → Generate token with ads_read. The ad account ID is in Ads Manager.",
           key_label="System user token", fields=[{"name": "ad_account_id", "label": "Ad account ID", "required": True}],
           fetch=_meta_fetch, write=_spend_writer("meta"), check=_check_meta),
    Source("zendesk", "Zendesk", "support", "Support tickets and satisfaction comments, linked to customers", "key",
           "Zendesk Admin Center → Apps and integrations → Zendesk API → Add API token.", key_label="API token",
           fields=[{"name": "subdomain", "label": "Subdomain (yourco.zendesk.com)", "required": True},
                   {"name": "email", "label": "Admin email", "required": True}],
           fetch=_zendesk_fetch, write=_feedback_writer("zendesk"), check=_check_zendesk),
    Source("intercom", "Intercom", "support", "Conversations and ratings, linked to customers by email domain", "key",
           "Intercom Developer Hub → Your app → Authentication → Access token.", key_label="Access token",
           fetch=_intercom_fetch, write=_feedback_writer("intercom"), check=_check_intercom),
]}


def available(kind: str) -> bool:
    s = SOURCES[kind]
    return s.auth == "key" or oauth.configured(kind)


def load_credentials(conn: Connection, tid: str, kind: str) -> tuple[dict, dict]:
    row = conn.execute(text("SELECT secret_enc, config FROM connection WHERE tenant_id = :t AND kind = :k"),
                       {"t": tid, "k": kind}).first()
    if not row:
        raise SourceError("Not connected.")
    raw = vault.decrypt(row[0])
    try:
        secret = json.loads(raw)
        secret = secret if isinstance(secret, dict) else {"key": raw}
    except ValueError:
        secret = {"key": raw}  # Increment 6 stored bare API keys
    return secret, dict(row[1] or {})


def after_oauth(kind: str, tok: dict, params: dict) -> dict:
    """Context to keep from the authorization (which company / org / instance)."""
    if kind == "quickbooks":
        if not params.get("realmId"):
            raise SourceError("QuickBooks didn't say which company was connected.")
        return {"realm_id": params["realmId"]}
    if kind == "xero":
        return accounting.xero_tenant(tok["access_token"])
    if kind == "salesforce":
        return {"instance_url": tok["instance_url"]}
    if kind == "gusto":
        return more_sources.gusto_company(more_sources.gusto_client(tok["access_token"]))
    return {}
