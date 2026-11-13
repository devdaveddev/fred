"""Salesforce: Leads -> leads, Accounts -> company profiles (same shapes as HubSpot), plus write-back.

Lead status: converted when IsConverted; lost when the status says so ("Unqualified", "Closed - Not Converted",
...) or when open for 120+ days; otherwise open. Lead source picklist values map to Fred's channels.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from .http import Api, SourceError
from .hubspot import industry, seniority
from .stripe import domain_of

API = "v62.0"
SOURCE = [(r"paid|ppc|adwords|google ads|search ad|advertis", "paid_search"), (r"linkedin", "linkedin"),
          (r"referr|partner|word of mouth", "referral"), (r"trade show|event|conference|webinar|seminar", "events"),
          (r"content|blog|ebook|whitepaper|social|newsletter|email", "content"),
          (r"web|organic|seo|inbound|direct|chat", "organic")]
LOST = re.compile(r"unqualif|not converted|disqualif|closed|lost|junk|dead", re.IGNORECASE)
LEAD_SOQL = ("SELECT Id, Company, FirstName, LastName, Title, Email, Website, LeadSource, Industry, NumberOfEmployees, "
             "Country, Status, IsConverted, ConvertedDate, ConvertedAccountId, CreatedDate FROM Lead "
             "WHERE CreatedDate = LAST_N_DAYS:730")
ACCOUNT_SOQL = "SELECT Id, Name, Website, Industry, NumberOfEmployees, BillingCountry, Description, AccountSource FROM Account"


def site_domain(v: str | None) -> str | None:
    """'https://www.Acme.com/about' -> 'acme.com'; emails go through domain_of."""
    if not v:
        return None
    if "@" in v:
        return domain_of(v)
    host = re.sub(r"^[a-z]+://", "", v.strip().lower()).split("/")[0].split(":")[0]
    host = host.removeprefix("www.")
    return host if "." in host else None


def channel(v: str | None) -> str:
    t = (v or "").lower()
    for pat, ch in SOURCE:
        if re.search(pat, t):
            return ch
    return "organic"


def client(token: str, instance_url: str) -> Api:
    return Api("Salesforce", instance_url.rstrip("/"), headers={"Authorization": f"Bearer {token}"},
               hints={401: "Salesforce rejected the session. Reconnect Salesforce.",
                      403: "Salesforce refused access. The connected user needs API access and read access to "
                           "Leads and Accounts."})


def query(api: Api, soql: str) -> list[dict]:
    out, page = [], api.get(f"/services/data/{API}/query", params={"q": soql})
    while True:
        out += page.get("records", [])
        if page.get("done", True) or not page.get("nextRecordsUrl"):
            return out
        page = api.get(page["nextRecordsUrl"])


def fetch(api: Api) -> dict:
    return {"leads": query(api, LEAD_SOQL), "accounts": query(api, ACCOUNT_SOQL)}


def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    v = v.replace("+0000", "+00:00").replace("Z", "+00:00")
    d = datetime.fromisoformat(v)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def normalise(raw: dict, tenant_id: str, now: datetime | None = None) -> dict:
    ns, now = uuid.UUID(tenant_id), now or datetime.now(UTC)
    accounts = {}
    for a in raw["accounts"]:
        dom = site_domain(a.get("Website"))
        accounts[a["Id"]] = {"crm_id": a["Id"], "name": a.get("Name"), "domain": dom, "industry": industry(a.get("Industry")),
                             "employees": a.get("NumberOfEmployees") or None, "country": a.get("BillingCountry"),
                             "description": (a.get("Description") or "").strip() or None,
                             "acquisition_channel": channel(a.get("AccountSource")) if a.get("AccountSource") else None}
    leads = []
    for x in raw["leads"]:
        created = _ts(x.get("CreatedDate"))
        if created is None:
            continue
        if x.get("IsConverted"):
            status = "converted"
        elif LOST.search(x.get("Status") or "") or created < now - timedelta(days=120):
            status = "lost"
        else:
            status = "open"
        acc = accounts.get(x.get("ConvertedAccountId") or "", {})
        dom = acc.get("domain") or site_domain(x.get("Website")) or domain_of(x.get("Email"))
        won = _ts(x.get("ConvertedDate")) if status == "converted" else None
        leads.append({
            "id": str(uuid.uuid5(ns, f"salesforce-lead:{x['Id']}")), "external_id": x["Id"], "crm": "salesforce",
            "created_at": created, "source_channel": channel(x.get("LeadSource")),
            "company": x.get("Company") or dom or "Unknown",
            "contact_name": " ".join(v for v in (x.get("FirstName"), x.get("LastName")) if v) or None,
            "industry": industry(x.get("Industry")) or acc.get("industry"),
            "employees": x.get("NumberOfEmployees") or acc.get("employees") or 0, "country": x.get("Country"),
            "seniority": seniority(x.get("Title")), "pricing_views": 0, "docs_views": 0, "email_engagement": 0,
            "demo_requested": False, "webinar": channel(x.get("LeadSource")) == "events" and
            "webinar" in (x.get("LeadSource") or "").lower(),
            "status": status, "converted_at": won or (created if status == "converted" else None),
            "description": acc.get("description"), "domain": dom})
    profiles = {a["domain"]: a | {"crm": "salesforce"} for a in accounts.values() if a["domain"]}
    return {"profiles": profiles, "leads": leads,
            "stats": {"accounts": len(accounts), "leads": len(leads),
                      "converted": sum(1 for x in leads if x["status"] == "converted")}}


# ------------------------------------------------------------------ write-back
LEAD_FIELDS = ("Fred_Score__c", "Fred_Reason__c")
ACCOUNT_FIELDS = ("Fred_Churn_Risk__c", "Fred_Churn_Reason__c")


def missing_fields(api: Api) -> list[str]:
    missing = []
    for obj, fields in (("Lead", LEAD_FIELDS), ("Account", ACCOUNT_FIELDS)):
        have = {f["name"] for f in api.get(f"/services/data/{API}/sobjects/{obj}/describe").get("fields", [])}
        missing += [f"{obj}.{f}" for f in fields if f not in have]
    return missing


def push(api: Api, obj: str, records: list[dict]) -> dict:
    """records: [{"id": ..., field: value}]. Composite collections, 200 per call, partial success allowed."""
    ok = failed = 0
    errors: list[str] = []
    for i in range(0, len(records), 200):
        batch = [{"attributes": {"type": obj}, **r} for r in records[i:i + 200]]
        res = api.request("PATCH", f"/services/data/{API}/composite/sobjects",
                          json={"allOrNone": False, "records": batch}).json()
        for r in res:
            if r.get("success"):
                ok += 1
            else:
                failed += 1
                errors += [e.get("message", "") for e in r.get("errors", [])][:1]
    if failed and not ok:
        raise SourceError(f"Salesforce rejected every update: {errors[0] if errors else 'unknown error'}")
    return {"updated": ok, "failed": failed, "errors": sorted(set(errors))[:3]}
