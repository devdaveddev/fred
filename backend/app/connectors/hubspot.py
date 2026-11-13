"""HubSpot: companies + contacts -> company profiles (firmographics, descriptions) and leads.

Uses a private-app token with read scopes for companies and contacts. Mapping:
  * contact -> lead; converted when lifecycle stage is customer (date from the stage timestamp),
    lost when older than 120 days without converting, otherwise open
  * source channel from HubSpot's original source; seniority from job title
  * intent proxies HubSpot exposes: page views, conversion events (form fills, demo requests),
    email opens. Fields HubSpot has no equivalent for stay zero, and the lead model learns from what exists
  * companies enrich billing accounts by domain (industry, size, country, description)
"""
from __future__ import annotations

import re
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx

COMPANY_PROPS = ["name", "domain", "industry", "numberofemployees", "description", "country", "lifecyclestage",
                 "createdate"]
CONTACT_PROPS = ["email", "firstname", "lastname", "jobtitle", "lifecyclestage", "hs_analytics_source", "createdate",
                 "associatedcompanyid", "hs_lifecyclestage_customer_date", "hs_analytics_num_page_views",
                 "num_conversion_events", "hs_email_open"]
SOURCE = {"PAID_SEARCH": "paid_search", "PAID_SOCIAL": "linkedin", "ORGANIC_SEARCH": "organic",
          "DIRECT_TRAFFIC": "organic", "REFERRALS": "referral", "SOCIAL_MEDIA": "content", "EMAIL_MARKETING": "content",
          "OTHER_CAMPAIGNS": "paid_search", "OFFLINE": "events", "AI_REFERRALS": "organic"}
INDUSTRY = [
    (r"software|information technology|internet|computer", "saas"),
    (r"financ|bank|insurance|capital|invest|accounting", "fintech"),
    (r"retail|consumer|apparel|e-?commerce|wholesale", "ecommerce"),
    (r"health|medical|hospital|pharma|biotech", "healthcare"),
    (r"media|publish|broadcast|entertainment|marketing and advertising", "media"),
    (r"manufactur|industrial|machinery|automotive|electronics", "manufacturing"),
    (r"education|e-?learning|school", "education"),
]


class HubSpotError(RuntimeError):
    pass


class HubSpotClient:
    def __init__(self, token: str, base: str = "https://api.hubapi.com", transport: httpx.BaseTransport | None = None):
        self.http = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=30,
                                 transport=transport)

    def get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(5):
            r = self.http.get(path, params=params)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(min(2 ** attempt, 20))
                continue
            if r.status_code == 401:
                raise HubSpotError("HubSpot rejected the token. Create a private app token with crm.objects.companies.read "
                                   "and crm.objects.contacts.read scopes.")
            if r.status_code == 403:
                raise HubSpotError("The token is missing a scope: it needs read access to companies and contacts.")
            if r.status_code >= 400:
                raise HubSpotError(f"HubSpot error {r.status_code}: {r.text[:200]}")
            return r.json()
        raise HubSpotError("HubSpot kept rate-limiting the sync; try again in a few minutes.")

    def paginate(self, obj: str, props: list[str]) -> Iterator[dict]:
        params = {"limit": 100, "properties": ",".join(props), "archived": "false"}
        while True:
            page = self.get(f"/crm/v3/objects/{obj}", params)
            yield from page.get("results", [])
            nxt = ((page.get("paging") or {}).get("next") or {}).get("after")
            if not nxt:
                return
            params["after"] = nxt

    def check(self) -> None:
        self.get("/crm/v3/objects/companies", {"limit": 1})
        self.get("/crm/v3/objects/contacts", {"limit": 1})


def fetch(client: HubSpotClient) -> dict:
    return {"companies": list(client.paginate("companies", COMPANY_PROPS)),
            "contacts": list(client.paginate("contacts", CONTACT_PROPS))}


# ------------------------------------------------------------------ normalisation (pure)
def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    if v.isdigit():
        return datetime.fromtimestamp(int(v) / 1000, tz=UTC)
    return datetime.fromisoformat(v)


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def industry(v: str | None) -> str | None:
    if not v:
        return None
    t = v.replace("_", " ").lower()
    for pat, ind in INDUSTRY:
        if re.search(pat, t):
            return ind
    return t


def seniority(title: str | None) -> str:
    t = (title or "").lower()
    if re.search(r"\b(ceo|cfo|coo|cto|cro|chief|founder|owner|president|vp|vice president)\b", t):
        return "exec"
    if re.search(r"\b(director|head of)\b", t):
        return "director"
    if re.search(r"\b(manager|lead|principal)\b", t):
        return "manager"
    return "ic"


def normalise(raw: dict, tenant_id: str, now: datetime | None = None) -> dict:
    ns = uuid.UUID(tenant_id)
    now = now or datetime.now(UTC)
    companies = {}
    for c in raw["companies"]:
        p = c.get("properties") or {}
        companies[c["id"]] = {
            "hubspot_id": c["id"], "crm_id": c["id"], "crm": "hubspot", "name": p.get("name") or p.get("domain") or c["id"],
            "domain": (p.get("domain") or "").lower().strip() or None, "industry": industry(p.get("industry")),
            "employees": _int(p.get("numberofemployees"), 0) or None, "country": p.get("country"),
            "description": (p.get("description") or "").strip() or None,
        }
    leads, first_source = [], {}
    for ct in raw["contacts"]:
        p = ct.get("properties") or {}
        created = _ts(p.get("createdate"))
        if created is None:
            continue
        comp = companies.get(str(p.get("associatedcompanyid") or ""), {})
        stage = (p.get("lifecyclestage") or "").lower()
        won_at = _ts(p.get("hs_lifecyclestage_customer_date"))
        if stage == "customer" or won_at:
            status = "converted"
        elif created < now - timedelta(days=120):
            status = "lost"
        else:
            status = "open"
        src = SOURCE.get((p.get("hs_analytics_source") or "").upper(), "organic")
        if comp.get("domain"):
            prev = first_source.get(comp["domain"])
            if prev is None or created < prev[0]:
                first_source[comp["domain"]] = (created, src)
        name = " ".join(x for x in (p.get("firstname"), p.get("lastname")) if x) or p.get("email") or ct["id"]
        leads.append({
            "id": str(uuid.uuid5(ns, f"hubspot-contact:{ct['id']}")), "external_id": ct["id"], "crm": "hubspot",
            "created_at": created, "source_channel": src,
            "company": comp.get("name") or (p.get("email") or "").split("@")[-1] or "Unknown",
            "contact_name": name, "industry": comp.get("industry"), "employees": comp.get("employees") or 0,
            "country": comp.get("country"), "seniority": seniority(p.get("jobtitle")),
            "pricing_views": min(_int(p.get("hs_analytics_num_page_views")), 10),
            "docs_views": 0, "email_engagement": min(_int(p.get("hs_email_open")), 5),
            "demo_requested": _int(p.get("num_conversion_events")) > 0, "webinar": False,
            "status": status, "converted_at": won_at if status == "converted" else None,
            "description": comp.get("description"), "domain": comp.get("domain"),
        })
    profiles = {c["domain"]: {**c, "acquisition_channel": first_source.get(c["domain"], (None, None))[1]}
                for c in companies.values() if c["domain"]}
    return {"profiles": profiles, "leads": leads,
            "stats": {"companies": len(companies), "contacts": len(raw["contacts"]), "leads": len(leads),
                      "converted": sum(1 for x in leads if x["status"] == "converted"),
                      "with_description": sum(1 for c in companies.values() if c["description"])}}
