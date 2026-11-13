"""Connectors against realistic API payloads served by a mock transport (no network)."""
import json
import uuid
from datetime import UTC, date, datetime

import httpx
import pytest

from app.connectors import hubspot, stripe

TENANT = str(uuid.uuid4())


def ts(y, m, d=1):
    return int(datetime(y, m, d, tzinfo=UTC).timestamp())


def line(amount, start, end, interval=None, count=1, proration=False, discounts=0, kind="subscription"):
    ln = {"id": f"il_{uuid.uuid4().hex[:8]}", "amount": amount, "type": kind, "proration": proration,
          "period": {"start": start, "end": end},
          "discount_amounts": [{"amount": discounts}] if discounts else []}
    if interval:
        ln["price"] = {"recurring": {"interval": interval, "interval_count": count}}
    return ln


INVOICES = [
    # monthly customer A: $500/mo Jan-Mar 2026, with a $50 discount in March
    {"id": "in_1", "customer": "cus_A", "status": "paid", "currency": "usd", "created": ts(2026, 1, 1),
     "lines": {"data": [line(50000, ts(2026, 1, 1), ts(2026, 2, 1), "month")], "has_more": False}},
    {"id": "in_2", "customer": "cus_A", "status": "paid", "currency": "usd", "created": ts(2026, 2, 1),
     "lines": {"data": [line(50000, ts(2026, 2, 1), ts(2026, 3, 1), "month")], "has_more": False}},
    {"id": "in_3", "customer": "cus_A", "status": "paid", "currency": "usd", "created": ts(2026, 3, 1),
     "lines": {"data": [line(50000, ts(2026, 3, 1), ts(2026, 4, 1), "month", discounts=5000),
                        line(20000, ts(2026, 3, 1), ts(2026, 3, 1), kind="invoiceitem")], "has_more": False}},
    # annual customer B: $12,000/yr from Feb 2026 -> $1,000/mo; has_more lines forces a second call
    {"id": "in_4", "customer": "cus_B", "status": "paid", "currency": "usd", "created": ts(2026, 2, 10),
     "lines": {"data": [], "has_more": True}},
    # quarterly customer C, new-API shape (no price object, parent.subscription_item_details): $900/qtr
    {"id": "in_5", "customer": "cus_C", "status": "open", "currency": "usd", "created": ts(2026, 1, 15),
     "lines": {"data": [{"id": "il_q", "amount": 90000, "period": {"start": ts(2026, 1, 15), "end": ts(2026, 4, 15)},
                         "parent": {"type": "subscription_item_details",
                                    "subscription_item_details": {"proration": False}}}], "has_more": False}},
    # proration and void are ignored; EUR invoice is skipped with a warning
    {"id": "in_6", "customer": "cus_A", "status": "paid", "currency": "usd", "created": ts(2026, 2, 15),
     "lines": {"data": [line(12345, ts(2026, 2, 15), ts(2026, 3, 1), "month", proration=True)], "has_more": False}},
    {"id": "in_7", "customer": "cus_D", "status": "void", "currency": "usd", "created": ts(2026, 1, 1),
     "lines": {"data": [line(99900, ts(2026, 1, 1), ts(2026, 2, 1), "month")], "has_more": False}},
    {"id": "in_8", "customer": "cus_E", "status": "paid", "currency": "eur", "created": ts(2026, 1, 1),
     "lines": {"data": [line(30000, ts(2026, 1, 1), ts(2026, 2, 1), "month")], "has_more": False}},
]
B_LINES = [line(1200000, ts(2026, 2, 10), ts(2027, 2, 10), "year")]
CUSTOMERS = [{"id": "cus_A", "name": "Acme Payments", "email": "ops@acmepay.io", "created": ts(2025, 12, 20),
              "address": {"country": "US"}},
             {"id": "cus_B", "name": None, "email": "finance@bigco.com", "created": ts(2026, 2, 1)},
             {"id": "cus_C", "name": "Clinic Net", "email": "ceo@gmail.com", "created": ts(2026, 1, 10)},
             {"id": "cus_E", "name": "Euro GmbH", "email": "x@euro.de", "created": ts(2026, 1, 1)}]


def stripe_transport(calls: list):
    state = {"rate_limited": False}

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        assert req.headers["authorization"] == "Bearer rk_test_123"
        path = req.url.path
        if path.endswith("/customers") and not state["rate_limited"]:
            state["rate_limited"] = True
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        if path.endswith("/customers"):
            after = req.url.params.get("starting_after")
            page = CUSTOMERS[:2] if not after else CUSTOMERS[2:]
            return httpx.Response(200, json={"data": page, "has_more": not after})
        if path.endswith("/invoices/in_4/lines"):
            return httpx.Response(200, json={"data": B_LINES, "has_more": False})
        if path.endswith("/invoices"):
            return httpx.Response(200, json={"data": INVOICES, "has_more": False})
        return httpx.Response(404, json={"error": {"message": "nope"}})
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(stripe.time, "sleep", lambda s: None)
    monkeypatch.setattr(hubspot.time, "sleep", lambda s: None)


def test_stripe_fetch_paginates_retries_and_expands_lines():
    calls: list = []
    client = stripe.StripeClient("rk_test_123", base="https://api.stripe.com/v1", transport=stripe_transport(calls))
    raw = stripe.fetch(client, since=date(2025, 1, 1))
    assert len(raw["customers"]) == 4  # two pages
    inv4 = next(i for i in raw["invoices"] if i["id"] == "in_4")
    assert inv4["lines"]["data"] == B_LINES  # has_more lines fetched
    assert any("starting_after=cus_B" in c for c in calls)


def test_stripe_mrr_normalisation():
    raw = {"customers": CUSTOMERS, "invoices": [dict(i) for i in INVOICES]}
    next(i for i in raw["invoices"] if i["id"] == "in_4")["lines"] = {"data": B_LINES, "has_more": False}
    out = stripe.normalise(raw, TENANT, last_closed=date(2026, 10, 1))
    ids = {a["external_id"]: a["id"] for a in out["accounts"]}
    mrr = {(r["a"], r["m"]): r["v"] for r in out["mrr"]}
    A, B, C = ids["cus_A"], ids["cus_B"], ids["cus_C"]
    assert mrr[(A, date(2026, 1, 1))] == 500 and mrr[(A, date(2026, 3, 1))] == 450  # discount applied
    assert (A, date(2026, 4, 1)) not in mrr  # no invoice -> no MRR
    assert mrr[(B, date(2026, 2, 1))] == 1000 and mrr[(B, date(2026, 10, 1))] == 1000  # annual / 12
    assert (B, date(2026, 11, 1)) not in mrr  # capped at last closed month
    assert [mrr[(C, date(2026, m, 1))] for m in (1, 2, 3)] == [300, 300, 300]  # quarterly, new API shape
    assert "cus_D" not in ids and "cus_E" not in ids  # void invoice; other currency
    assert out["warnings"] and "EUR" in out["warnings"][0]
    assert out["stats"]["excluded_one_time_lines"] == 2  # one-time item + proration
    acc = {a["external_id"]: a for a in out["accounts"]}
    assert acc["cus_B"]["name"] == "bigco.com" and acc["cus_B"]["domain"] == "bigco.com"
    assert acc["cus_C"]["domain"] is None  # free-mail domains never become company domains


def test_stripe_auth_errors_are_explained():
    t = httpx.MockTransport(lambda r: httpx.Response(401, json={"error": {"message": "Invalid API Key"}}))
    with pytest.raises(stripe.StripeError, match="restricted key"):
        stripe.StripeClient("bad", transport=t).check()


def hs_transport():
    companies = [
        {"id": "1", "properties": {"name": "Acme Payments", "domain": "acmepay.io", "industry": "FINANCIAL_SERVICES",
                                   "numberofemployees": "240", "description": "Payments API for online merchants.",
                                   "country": "United States"}},
        {"id": "2", "properties": {"name": "Learnly", "domain": "learnly.com", "industry": "E_LEARNING",
                                   "numberofemployees": "35", "description": None}},
    ]
    contacts = [
        {"id": "11", "properties": {"email": "cfo@acmepay.io", "firstname": "Dana", "lastname": "Lee", "jobtitle": "CFO",
                                    "lifecyclestage": "customer", "hs_analytics_source": "PAID_SEARCH",
                                    "createdate": "2025-09-01T10:00:00Z", "associatedcompanyid": "1",
                                    "hs_lifecyclestage_customer_date": "2025-10-15T00:00:00Z",
                                    "hs_analytics_num_page_views": "14", "num_conversion_events": "2", "hs_email_open": "9"}},
        {"id": "12", "properties": {"email": "ops@learnly.com", "jobtitle": "Operations Manager", "lifecyclestage": "lead",
                                    "hs_analytics_source": "REFERRALS", "createdate": "2026-10-20T10:00:00Z",
                                    "associatedcompanyid": "2"}},
        {"id": "13", "properties": {"email": "x@old.com", "lifecyclestage": "lead", "hs_analytics_source": "OFFLINE",
                                    "createdate": "2025-01-05T10:00:00Z"}},
    ]

    def handler(req):
        assert req.headers["authorization"] == "Bearer pat-123"
        obj = req.url.path.rsplit("/", 1)[-1]
        data = companies if obj == "companies" else contacts
        after = req.url.params.get("after")
        page = data[:1] if not after else data[1:]
        return httpx.Response(200, json={"results": page, "paging": None if after else {"next": {"after": "1"}}})
    return httpx.MockTransport(handler)


def test_hubspot_fetch_and_normalise():
    raw = hubspot.fetch(hubspot.HubSpotClient("pat-123", transport=hs_transport()))
    assert len(raw["companies"]) == 2 and len(raw["contacts"]) == 3
    out = hubspot.normalise(raw, TENANT, now=datetime(2026, 11, 12, tzinfo=UTC))
    by = {lead["contact_name"]: lead for lead in out["leads"]}
    dana = by["Dana Lee"]
    assert dana["status"] == "converted" and dana["seniority"] == "exec" and dana["source_channel"] == "paid_search"
    assert dana["industry"] == "fintech" and dana["employees"] == 240 and dana["demo_requested"]
    assert dana["pricing_views"] == 10 and dana["email_engagement"] == 5  # clipped like the model's features
    assert by["ops@learnly.com"]["status"] == "open" and by["ops@learnly.com"]["seniority"] == "manager"
    assert by["x@old.com"]["status"] == "lost" and by["x@old.com"]["source_channel"] == "events"
    prof = out["profiles"]["acmepay.io"]
    assert prof["description"].startswith("Payments API") and prof["acquisition_channel"] == "paid_search"
    assert json.dumps(out, default=str)  # serialisable
