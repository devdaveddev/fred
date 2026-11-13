"""Payroll (Gusto), ads (Google Ads, Meta) and support (Zendesk, Intercom).

Each returns the same rows the matching CSV importer produces, so the write path is shared and tested once.
"""
from __future__ import annotations

import base64
import re
from datetime import UTC, date, datetime, timedelta

from ..config import get_settings
from .csv_import import DEPTS
from .http import Api, SourceError
from .mapping import _dept as dept_from_title
from .stripe import FREE_MAIL, domain_of

# ------------------------------------------------------------------ Gusto (payroll roster)
ANNUAL = {"year": 1, "month": 12, "week": 52, "hour": 2080, "paycheck": 24}


def gusto_base() -> str:
    return "https://api.gusto-demo.com" if get_settings().gusto_demo else "https://api.gusto.com"


def gusto_client(token: str) -> Api:
    return Api("Gusto", gusto_base(), headers={"Authorization": f"Bearer {token}", "X-Gusto-API-Version": "2024-04-01"})


def gusto_company(api: Api) -> dict:
    info = api.get("/v1/token_info")
    res = info.get("resource") or {}
    if res.get("type") != "Company":
        raise SourceError("This Gusto authorization isn't for a company.")
    return {"company_uuid": res["uuid"]}


def gusto_fetch(api: Api, company_uuid: str) -> list[dict]:
    """Current and former employees (former ones matter: departures explain payroll variances)."""
    seen: dict[str, dict] = {}
    for terminated in ("false", "true"):
        page = 1
        while True:
            batch = api.get(f"/v1/companies/{company_uuid}/employees",
                            params={"terminated": terminated, "page": page, "per": 100})
            for e in batch:
                seen.setdefault(e["uuid"], e)
            if len(batch) < 100:
                break
            page += 1
    return list(seen.values())


def _dept(e: dict, title: str) -> str:
    d = DEPTS.get((e.get("department") or "").strip().lower())
    return d or dept_from_title(f"{e.get('department') or ''} {title}") or "G&A"


def gusto_roster(employees: list[dict]) -> list[dict]:
    roster = []
    for e in employees:
        jobs = sorted(e.get("jobs") or [], key=lambda j: j.get("hire_date") or "")
        if not jobs:
            continue
        job = jobs[-1]
        comp = max(job.get("compensations") or [{"rate": job.get("rate"), "payment_unit": job.get("payment_unit")}], key=lambda c: c.get("effective_date") or "")
        try:
            rate = float(comp.get("rate") or 0)
        except (TypeError, ValueError):
            rate = 0.0
        salary = rate * ANNUAL.get((comp.get("payment_unit") or "Year").lower(), 1)
        if salary <= 0:
            continue
        ends = [t.get("effective_date") for t in (e.get("terminations") or []) if t.get("effective_date")]
        title = job.get("title") or "Employee"
        roster.append({"name": " ".join(v for v in (e.get("first_name"), e.get("last_name")) if v) or e["uuid"],
                       "title": title, "department": _dept(e, title), "location": (job.get("location") or {}).get("state") or "US",
                       "start_date": date.fromisoformat(jobs[0].get("hire_date") or e.get("date_of_hire") or "2020-01-01"),
                       "end_date": date.fromisoformat(max(ends)) if e.get("terminated") and ends else None,
                       "salary_annual": round(salary, 2), "loaded_factor": 1.2})
    if not roster:
        raise SourceError("Gusto returned no employees with compensation.")
    return roster


# ------------------------------------------------------------------ Google Ads
def google_ads_fetch(token: str, customer_id: str, start: date, end: date, login_customer_id: str | None = None) -> list[dict]:
    s = get_settings()
    cid = re.sub(r"\D", "", customer_id)
    headers = {"Authorization": f"Bearer {token}", "developer-token": s.google_ads_developer_token or ""}
    if login_customer_id:
        headers["login-customer-id"] = re.sub(r"\D", "", login_customer_id)
    api = Api("Google Ads", f"https://googleads.googleapis.com/{s.google_ads_api_version}", headers=headers,
              hints={403: "Google Ads refused access. Check the customer ID, and that your developer token is approved "
                          "for production accounts."})
    q = ("SELECT segments.date, metrics.cost_micros FROM customer "
         f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'")
    rows, body = [], {"query": q}
    while True:
        page = api.post(f"/customers/{cid}/googleAds:search", json=body)
        for r in page.get("results", []):
            cost = int(r.get("metrics", {}).get("costMicros", 0)) / 1e6
            if cost:
                rows.append({"date": date.fromisoformat(r["segments"]["date"]), "channel": "paid_search", "amount": cost})
        if not page.get("nextPageToken"):
            return rows
        body = {"query": q, "pageToken": page["nextPageToken"]}


# ------------------------------------------------------------------ Meta Ads
def meta_fetch(token: str, ad_account: str, start: date, end: date) -> list[dict]:
    acct = ad_account if ad_account.startswith("act_") else f"act_{re.sub(r'\D', '', ad_account)}"
    api = Api("Meta", f"https://graph.facebook.com/{get_settings().meta_graph_version}",
              hints={400: "Meta refused the request: check the ad account ID and that the token has ads_read."})
    params = {"access_token": token, "fields": "spend", "level": "account", "time_increment": 1, "limit": 500,
              "time_range": f'{{"since":"{start.isoformat()}","until":"{end.isoformat()}"}}'}
    rows, page = [], api.get(f"/{acct}/insights", params=params)
    while True:
        for r in page.get("data", []):
            v = float(r.get("spend") or 0)
            if v:
                rows.append({"date": date.fromisoformat(r["date_start"]), "channel": "meta", "amount": v})
        nxt = (page.get("paging") or {}).get("next")
        if not nxt:
            return rows
        page = api.get(nxt)


# ------------------------------------------------------------------ support: Zendesk, Intercom
TAG = re.compile(r"<[^>]+>")


def _clean(t: str | None) -> str:
    return re.sub(r"\s+", " ", TAG.sub(" ", t or "")).strip()


def zendesk_client(subdomain: str, email: str, token: str) -> Api:
    sub = re.sub(r"[^a-z0-9-]", "", subdomain.lower().replace(".zendesk.com", ""))
    if not sub:
        raise SourceError("Enter your Zendesk subdomain (the part before .zendesk.com).")
    auth = base64.b64encode(f"{email}/token:{token}".encode()).decode()
    return Api("Zendesk", f"https://{sub}.zendesk.com", headers={"Authorization": f"Basic {auth}"},
               hints={401: "Zendesk rejected the email and API token.", 403: "That Zendesk user can't read tickets."})


def zendesk_fetch(api: Api, days: int = 180, cap: int = 5000) -> list[dict]:
    since = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
    orgs: dict[int, str] = {}
    url, params = "/api/v2/organizations.json", {"page[size]": 100}
    while url:
        page = api.get(url, params=params)
        for o in page.get("organizations", []):
            if o.get("domain_names"):
                orgs[o["id"]] = o["domain_names"][0].lower()
        url, params = ((page.get("links") or {}).get("next") if (page.get("meta") or {}).get("has_more") else None), None
    rows = []
    page = api.get("/api/v2/incremental/tickets/cursor.json", params={"start_time": since})
    while True:
        for t in page.get("tickets", []):
            text = _clean(f"{t.get('subject') or ''}. {t.get('description') or ''}")[:1500]
            if text.strip(". "):
                rows.append({"date": date.fromisoformat(t["created_at"][:10]), "customer_id": orgs.get(t.get("organization_id")),
                             "kind": "support_ticket", "score": None, "text": text})
        if page.get("end_of_stream") or len(rows) >= cap or not page.get("after_url"):
            break
        page = api.get(page["after_url"])
    page = api.get("/api/v2/satisfaction_ratings.json", params={"score": "received_with_comment", "start_time": since})
    for r in page.get("satisfaction_ratings", []):
        if r.get("comment"):
            rows.append({"date": date.fromisoformat(r["created_at"][:10]), "customer_id": orgs.get(r.get("organization_id")),
                         "kind": "nps", "score": 9 if r.get("score") == "good" else 3, "text": _clean(r["comment"])[:1500]})
    return rows[:cap]


def intercom_fetch(token: str, days: int = 180, cap: int = 5000) -> list[dict]:
    api = Api("Intercom", "https://api.intercom.io",
              headers={"Authorization": f"Bearer {token}", "Intercom-Version": "2.11", "Accept": "application/json"})
    since = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
    body: dict = {"query": {"field": "created_at", "operator": ">", "value": since}, "pagination": {"per_page": 150}}
    rows = []
    while len(rows) < cap:
        page = api.post("/conversations/search", json=body)
        for c in page.get("conversations", []):
            src = c.get("source") or {}
            email = ((src.get("author") or {}).get("email") or "").lower()
            dom = domain_of(email)
            text = _clean(src.get("body") or src.get("subject"))[:1500]
            rating = c.get("conversation_rating") or {}
            if rating.get("remark"):
                rows.append({"date": datetime.fromtimestamp(rating.get("created_at") or c["created_at"], UTC).date(),
                             "customer_id": dom, "kind": "nps", "score": {1: 1, 2: 3, 3: 6, 4: 8, 5: 10}.get(rating.get("rating")),
                             "text": _clean(rating["remark"])[:1500]})
            if text:
                rows.append({"date": datetime.fromtimestamp(c["created_at"], UTC).date(),
                             "customer_id": dom if dom and dom not in FREE_MAIL else None,
                             "kind": "support_ticket", "score": None, "text": text})
        nxt = ((page.get("pages") or {}).get("next") or {}).get("starting_after")
        if not nxt:
            break
        body["pagination"]["starting_after"] = nxt
    return rows[:cap]
