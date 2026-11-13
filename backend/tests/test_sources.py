"""New connectors against realistic provider payloads. No network: httpx.MockTransport plays the provider."""
import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pandas as pd
import pytest

from app.connectors import accounting, http, mapping, more_sources, oauth, salesforce, writeback
from app.connectors.http import Api, SourceError


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http, "sleep", lambda s: None)


def mock(handler):
    return httpx.MockTransport(handler)


# ------------------------------------------------------------------ QuickBooks
def money_col(title, start):
    return {"ColTitle": title, "ColType": "Money", "MetaData": [{"Name": "StartDate", "Value": start}]}


QBO_PNL = {
    "Columns": {"Column": [{"ColTitle": "", "ColType": "Account"}, money_col("Sep 2026", "2026-09-01"),
                           money_col("Oct 2026", "2026-10-01"), {"ColTitle": "Total", "ColType": "Money"}]},
    "Rows": {"Row": [
        {"type": "Section", "group": "Income", "Header": {"ColData": [{"value": "Income"}]},
         "Rows": {"Row": [{"type": "Data", "ColData": [{"value": "4000 Subscription revenue", "id": "1"},
                                                       {"value": "100000.00"}, {"value": "104000.00"}, {"value": "204000"}]}]},
         "Summary": {"ColData": [{"value": "Total Income"}, {"value": "100000"}, {"value": "104000"}, {"value": "204000"}]}},
        {"type": "Section", "group": "COGS", "Header": {"ColData": [{"value": "Cost of Goods Sold"}]},
         "Rows": {"Row": [{"type": "Data", "ColData": [{"value": "Hosting (AWS)"}, {"value": "8000"}, {"value": "8500"},
                                                       {"value": "16500"}]}]}},
        {"type": "Section", "group": "Expenses", "Header": {"ColData": [{"value": "Expenses"}]},
         "Rows": {"Row": [
             {"type": "Section", "Header": {"ColData": [{"value": "Payroll", "id": "60"}]},  # parent with sub-accounts
              "Rows": {"Row": [{"type": "Data", "ColData": [{"value": "6000 Salaries: Sales"}, {"value": "30000"},
                                                            {"value": "31000"}, {"value": "61000"}]},
                               {"type": "Data", "ColData": [{"value": "6010 Salaries: Engineering"}, {"value": "50000"},
                                                            {"value": ""}, {"value": "50000"}]}]},
              "Summary": {"ColData": [{"value": "Total Payroll"}, {"value": "80000"}, {"value": "31000"}, {"value": "111000"}]}},
             {"type": "Data", "ColData": [{"value": "Google Ads"}, {"value": "5000"}, {"value": "6000"}, {"value": "11000"}]}]}},
    ]},
}


def test_qbo_pnl_flattens_subaccounts_and_ignores_totals():
    g = accounting.parse_qbo_pnl(QBO_PNL)
    by = {(r["account"], r["month"]): r["amount"] for r in g["amounts"]}
    assert by[("4000", date(2026, 10, 1))] == 104000
    assert by[("6000", date(2026, 9, 1))] == 30000
    assert ("6010", date(2026, 10, 1)) not in by  # blank cell is not zero revenue... or anything
    assert sum(r["amount"] for r in g["amounts"]) == 204000 + 16500 + 61000 + 50000 + 11000  # no totals double-counted
    sections = {a["code"]: a["section"] for a in g["accounts"]}
    assert sections["4000"] == "Income" and sections["Hosting (AWS)"] == "Cost of Goods Sold"
    assert sections["6000"] == "Expenses"  # nested sections keep the top-level group


def test_qbo_accounts_map_correctly():
    g = accounting.parse_qbo_pnl(QBO_PNL)
    m = mapping.classify([a["name"] for a in g["accounts"]], [a["section"] for a in g["accounts"]])
    got = {a["code"]: (x["pl_group"], x["kind"], x["department"]) for a, x in zip(g["accounts"], m)}
    assert got["4000"] == ("revenue", "revenue", None)
    assert got["6000"] == ("sm", "payroll", "Sales")
    assert got["6010"] == ("rnd", "payroll", "Engineering")
    assert got["Hosting (AWS)"][0] == "cogs" and got["Google Ads"][:2] == ("sm", "nonpayroll")


def test_qbo_cash_from_balance_sheet():
    bs = {"Columns": QBO_PNL["Columns"], "Rows": {"Row": [{"type": "Section", "group": "TotalAssets", "Rows": {"Row": [
        {"type": "Section", "group": "BankAccounts", "Rows": {"Row": []},
         "Summary": {"ColData": [{"value": "Total Bank Accounts"}, {"value": "2500000"}, {"value": "2410000.50"},
                                 {"value": ""}]}}]}}]}}
    assert accounting.parse_qbo_cash(bs) == [{"month": date(2026, 9, 1), "cash": 2500000},
                                             {"month": date(2026, 10, 1), "cash": 2410000.5}]


# ------------------------------------------------------------------ Xero
def xero_report(rows):
    return {"Reports": [{"Rows": rows}]}


XERO_PNL = xero_report([
    {"RowType": "Header", "Cells": [{"Value": ""}, {"Value": "31 Oct 2026"}, {"Value": "30 Sep 2026"}]},
    {"RowType": "Section", "Title": "Income", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "Sales"}, {"Value": "104000.00"}, {"Value": "100000.00"}]},
        {"RowType": "SummaryRow", "Cells": [{"Value": "Total Income"}, {"Value": "104000"}, {"Value": "100000"}]}]},
    {"RowType": "Section", "Title": "Less Cost of Sales", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "Cloud hosting"}, {"Value": "8500"}, {"Value": "8000"}]}]},
    {"RowType": "Section", "Title": "Gross Profit", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "Gross Profit"}, {"Value": "95500"}, {"Value": "92000"}]}]},
    {"RowType": "Section", "Title": "Less Operating Expenses", "Rows": [
        {"RowType": "Row", "Cells": [{"Value": "Wages and Salaries - Engineering"}, {"Value": "50000"}, {"Value": "48000"}]}]},
])


def test_xero_pnl_newest_first_columns_and_sections():
    accounts, recs = {}, []
    accounting.parse_xero_pnl(XERO_PNL, accounts, recs)
    by = {(r["account"], r["month"]): r["amount"] for r in recs}
    assert by[("Sales", date(2026, 10, 1))] == 104000 and by[("Sales", date(2026, 9, 1))] == 100000
    assert accounts["Cloud hosting"]["section"] == "Cost of Sales"
    assert accounts["Wages and Salaries - Engineering"]["section"] == "Operating Expenses"
    m = mapping.classify(["Wages and Salaries - Engineering"], ["Operating Expenses"])[0]
    assert (m["kind"], m["department"]) == ("payroll", "Engineering")
    # "Gross Profit" sits in its own section in Xero: it's an account row there, which we must not mistake
    # for an expense. It gets section "Gross Profit"; the mapping screen shows it for review.


def test_xero_cash_from_bank_section():
    bs = xero_report([{"RowType": "Header", "Cells": [{"Value": ""}, {"Value": "31 Oct 2026"}, {"Value": "30 Sep 2026"}]},
                      {"RowType": "Section", "Title": "Bank", "Rows": [
                          {"RowType": "Row", "Cells": [{"Value": "Checking"}, {"Value": "2000000"}, {"Value": "2100000"}]},
                          {"RowType": "SummaryRow", "Cells": [{"Value": "Total Bank"}, {"Value": "2410000"}, {"Value": "2500000"}]}]}])
    assert accounting.parse_xero_cash(bs) == [{"month": date(2026, 10, 1), "cash": 2410000},
                                              {"month": date(2026, 9, 1), "cash": 2500000}]


# ------------------------------------------------------------------ Salesforce
TID = "6f1c2b4e-1111-4222-8333-944455556666"


def test_salesforce_normalise():
    now = datetime(2026, 11, 13, tzinfo=UTC)
    raw = {"accounts": [{"Id": "001A", "Name": "Acme", "Website": "https://www.Acme.com/about", "Industry": "Banking",
                         "NumberOfEmployees": 240, "BillingCountry": "US", "Description": "Payments API", "AccountSource": "Partner Referral"}],
           "leads": [
               {"Id": "00Q1", "Company": "Acme", "FirstName": "Ana", "LastName": "Li", "Title": "VP Finance",
                "LeadSource": "Partner Referral", "IsConverted": True, "ConvertedDate": "2026-06-01",
                "ConvertedAccountId": "001A", "CreatedDate": "2026-05-01T10:00:00.000+0000", "Status": "Closed - Converted"},
               {"Id": "00Q2", "Company": "Beta", "Email": "bo@beta.io", "LeadSource": "Google AdWords", "IsConverted": False,
                "Status": "Unqualified", "CreatedDate": "2026-10-01T10:00:00.000+0000"},
               {"Id": "00Q3", "Company": "Gamma", "Email": "x@gmail.com", "LeadSource": "Web", "IsConverted": False,
                "Status": "Working", "CreatedDate": (now - timedelta(days=10)).isoformat()},
               {"Id": "00Q4", "Company": "Old", "LeadSource": None, "IsConverted": False, "Status": "Open",
                "CreatedDate": "2026-01-01T00:00:00Z"}]}
    n = salesforce.normalise(raw, TID, now)
    by = {x["external_id"]: x for x in n["leads"]}
    assert by["00Q1"]["status"] == "converted" and by["00Q1"]["domain"] == "acme.com"
    assert by["00Q1"]["source_channel"] == "referral" and by["00Q1"]["seniority"] == "exec"
    assert by["00Q2"]["status"] == "lost" and by["00Q2"]["source_channel"] == "paid_search" and by["00Q2"]["domain"] == "beta.io"
    assert by["00Q3"]["status"] == "open" and by["00Q3"]["domain"] is None  # free mail is not a company
    assert by["00Q4"]["status"] == "lost"  # open 300+ days
    assert all(x["crm"] == "salesforce" for x in n["leads"])
    p = n["profiles"]["acme.com"]
    assert p["crm_id"] == "001A" and p["industry"] == "fintech" and p["acquisition_channel"] == "referral"


def test_salesforce_query_follows_pages():
    pages = {"/services/data/v62.0/query": {"records": [{"Id": 1}], "done": False, "nextRecordsUrl": "/next/2"},
             "/next/2": {"records": [{"Id": 2}], "done": True}}
    http.transport = mock(lambda r: httpx.Response(200, json=pages[r.url.path]))
    try:
        assert [r["Id"] for r in salesforce.query(salesforce.client("t", "https://x.my.salesforce.com"), "SELECT Id FROM Lead")] == [1, 2]
    finally:
        http.transport = None


# ------------------------------------------------------------------ Gusto
def test_gusto_roster_annualises_and_keeps_leavers():
    emps = [
        {"uuid": "a", "first_name": "Ana", "last_name": "Li", "department": "Engineering", "terminated": False,
         "jobs": [{"title": "Senior Engineer", "hire_date": "2024-03-01",
                   "compensations": [{"rate": "150000.00", "payment_unit": "Year", "effective_date": "2024-03-01"},
                                     {"rate": "165000.00", "payment_unit": "Year", "effective_date": "2025-03-01"}]}]},
        {"uuid": "b", "first_name": "Bo", "last_name": "Ng", "department": None, "terminated": True,
         "terminations": [{"effective_date": "2026-08-15"}],
         "jobs": [{"title": "Account Executive", "hire_date": "2025-01-06",
                   "compensations": [{"rate": "40.00", "payment_unit": "Hour", "effective_date": "2025-01-06"}]}]},
        {"uuid": "c", "first_name": "No", "last_name": "Pay", "jobs": [{"title": "Advisor", "hire_date": "2025-01-01",
                                                                       "compensations": [{"rate": "0", "payment_unit": "Year"}]}]},
    ]
    r = {x["name"]: x for x in more_sources.gusto_roster(emps)}
    assert r["Ana Li"]["salary_annual"] == 165000 and r["Ana Li"]["department"] == "Engineering"
    assert r["Bo Ng"]["salary_annual"] == 40 * 2080 and r["Bo Ng"]["department"] == "Sales"  # from the title
    assert r["Bo Ng"]["end_date"] == date(2026, 8, 15)
    assert "No Pay" not in r


# ------------------------------------------------------------------ ads
def test_google_ads_pages_and_micros(monkeypatch):
    monkeypatch.setattr(more_sources.get_settings(), "google_ads_developer_token", "dev-tok")
    seen = []

    def h(req):
        body = json.loads(req.content)
        seen.append((req.url.path, req.headers.get("developer-token"), req.headers.get("login-customer-id"), body.get("pageToken")))
        if body.get("pageToken"):
            return httpx.Response(200, json={"results": [{"segments": {"date": "2026-10-02"}, "metrics": {"costMicros": "2500000"}}]})
        return httpx.Response(200, json={"results": [{"segments": {"date": "2026-10-01"}, "metrics": {"costMicros": "123450000"}},
                                                     {"segments": {"date": "2026-10-03"}, "metrics": {"costMicros": "0"}}],
                                         "nextPageToken": "p2"})
    http.transport = mock(h)
    try:
        rows = more_sources.google_ads_fetch("tok", "123-456-7890", date(2026, 10, 1), date(2026, 10, 31), "999-000-1111")
    finally:
        http.transport = None
    assert rows == [{"date": date(2026, 10, 1), "channel": "paid_search", "amount": 123.45},
                    {"date": date(2026, 10, 2), "channel": "paid_search", "amount": 2.5}]
    assert seen[0][0].endswith("/customers/1234567890/googleAds:search") and seen[0][1] == "dev-tok" and seen[0][2] == "9990001111"


def test_meta_insights_paging():
    def h(req):
        if "after" in str(req.url):
            return httpx.Response(200, json={"data": [{"spend": "10.5", "date_start": "2026-10-02"}]})
        assert req.url.path.endswith("/act_123/insights")
        return httpx.Response(200, json={"data": [{"spend": "99.99", "date_start": "2026-10-01"}, {"spend": "0", "date_start": "2026-10-03"}],
                                         "paging": {"next": "https://graph.facebook.com/v21.0/act_123/insights?after=x"}})
    http.transport = mock(h)
    try:
        rows = more_sources.meta_fetch("tok", "123", date(2026, 10, 1), date(2026, 10, 31))
    finally:
        http.transport = None
    assert [(r["date"].day, r["amount"], r["channel"]) for r in rows] == [(1, 99.99, "meta"), (2, 10.5, "meta")]


# ------------------------------------------------------------------ support
def test_zendesk_tickets_and_csat_link_to_org_domains():
    def h(req):
        p = req.url.path
        if p.endswith("/organizations.json"):
            return httpx.Response(200, json={"organizations": [{"id": 7, "domain_names": ["acme.com"]}], "meta": {"has_more": False}})
        if "incremental/tickets" in p:
            return httpx.Response(200, json={"tickets": [
                {"subject": "SSO broken", "description": "<p>Can't log in</p>", "created_at": "2026-10-01T10:00:00Z", "organization_id": 7},
                {"subject": "", "description": "", "created_at": "2026-10-02T10:00:00Z"}], "end_of_stream": True})
        if p.endswith("/satisfaction_ratings.json"):
            return httpx.Response(200, json={"satisfaction_ratings": [
                {"score": "bad", "comment": "Too slow to respond", "created_at": "2026-10-03T00:00:00Z", "organization_id": 7}]})
        return httpx.Response(404)
    http.transport = mock(h)
    try:
        rows = more_sources.zendesk_fetch(more_sources.zendesk_client("acme", "a@acme.com", "tok"))
    finally:
        http.transport = None
    assert rows[0] == {"date": date(2026, 10, 1), "customer_id": "acme.com", "kind": "support_ticket", "score": None,
                       "text": "SSO broken. Can't log in"}
    assert len(rows) == 2 and rows[1]["kind"] == "nps" and rows[1]["score"] == 3


def test_zendesk_auth_header_and_subdomain_cleanup():
    api = more_sources.zendesk_client("Acme.zendesk.com", "a@acme.com", "tok")
    assert str(api.http.base_url).startswith("https://acme.zendesk.com")
    assert api.http.headers["authorization"].startswith("Basic ")
    with pytest.raises(SourceError):
        more_sources.zendesk_client("../", "a", "b")


def test_intercom_conversations_and_ratings():
    def h(req):
        body = json.loads(req.content)
        if body["pagination"].get("starting_after"):
            return httpx.Response(200, json={"conversations": []})
        return httpx.Response(200, json={"conversations": [
            {"created_at": 1790000000, "source": {"body": "<p>Export to CSV fails</p>", "author": {"email": "ops@acme.com"}},
             "conversation_rating": {"rating": 2, "remark": "Slow fix", "created_at": 1790100000}},
            {"created_at": 1790000500, "source": {"body": "hi", "author": {"email": "me@gmail.com"}}}],
            "pages": {"next": {"starting_after": "abc"}}})
    http.transport = mock(h)
    try:
        rows = more_sources.intercom_fetch("tok")
    finally:
        http.transport = None
    kinds = [(r["kind"], r["customer_id"], r["score"]) for r in rows]
    assert ("nps", "acme.com", 3) in kinds and ("support_ticket", "acme.com", None) in kinds
    assert ("support_ticket", None, None) in kinds  # free-mail author: not linked to a company


# ------------------------------------------------------------------ HTTP client & OAuth
def test_api_retries_rate_limits_then_explains_auth_errors():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else httpx.Response(200, json={"ok": True})
    http.transport = mock(h)
    try:
        assert Api("X", "https://x.test").get("/a") == {"ok": True} and len(calls) == 3
        http.transport = mock(lambda r: httpx.Response(401))
        with pytest.raises(SourceError, match="rejected the credentials"):
            Api("X", "https://x.test").get("/a")
    finally:
        http.transport = None


def test_oauth_start_urls(monkeypatch):
    s = oauth.get_settings()
    for k in ("quickbooks", "xero"):
        monkeypatch.setattr(s, f"{k}_client_id", f"{k}-id")
        monkeypatch.setattr(s, f"{k}_client_secret", "sec")

    class Conn:
        def __init__(self):
            self.rows = []

        def execute(self, stmt, params=None):
            self.rows.append(params)

    c = Conn()
    q = parse_qs(urlparse(oauth.start(c, TID, "u", "xero")).query)
    assert q["client_id"] == ["xero-id"] and q["code_challenge_method"] == ["S256"] and "offline_access" in q["scope"][0]
    assert q["redirect_uri"][0].endswith("/api/sources/oauth/callback")
    q = parse_qs(urlparse(oauth.start(c, TID, "u", "quickbooks")).query)
    assert "code_challenge" not in q and q["scope"] == ["com.intuit.quickbooks.accounting"]
    assert any(r and r.get("v") and r.get("k") == "quickbooks" for r in c.rows)  # PKCE verifier stored with the state


def test_google_ads_needs_developer_token(monkeypatch):
    s = oauth.get_settings()
    monkeypatch.setattr(s, "google_ads_client_id", "id")
    monkeypatch.setattr(s, "google_ads_client_secret", "sec")
    monkeypatch.setattr(s, "google_ads_developer_token", None)
    assert not oauth.configured("google_ads")
    monkeypatch.setattr(s, "google_ads_developer_token", "dev")
    assert oauth.configured("google_ads")


# ------------------------------------------------------------------ write-back
def test_hubspot_writeback_creates_properties_then_batches():
    seen = []

    def h(req):
        seen.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if "/properties/" in req.url.path:
            return httpx.Response(409 if "contacts" in req.url.path else 201, json={})
        return httpx.Response(200, json={"status": "COMPLETE"})
    http.transport = mock(h)
    try:
        lp = [{"id": str(i), "score": 50, "reason": "Demo requested"} for i in range(150)]
        ap = [{"id": "c1", "risk": 42, "reason": "Usage falling"}]
        out = writeback.hubspot_push("tok", "https://api.hubapi.com", lp, ap)
    finally:
        http.transport = None
    assert out == {"contacts": 150, "companies": 1}
    batches = [b for m, p, b in seen if p.endswith("/batch/update")]
    assert [len(b["inputs"]) for b in batches] == [100, 50, 1]
    assert batches[-1]["inputs"][0]["properties"] == {"fred_churn_risk": 42, "fred_churn_reason": "Usage falling"}


def test_writeback_payload_only_touches_records_from_that_crm():
    class Conn:
        def execute(self, stmt, params):
            sql = str(stmt)

            class R:
                def all(self_inner):
                    if "FROM lead" in sql:
                        return [("L1", "00Q1")] if params["c"] == "salesforce" else []
                    return [("A1", "001A")] if params["c"] == "salesforce" else []
            return R()
    scored = pd.DataFrame([{"lead_id": "L1", "p": 0.734, "reasons": ["Demo requested", "Exec buyer", "Fintech"]},
                           {"lead_id": "L9", "p": 0.9, "reasons": []}])
    risk = pd.DataFrame([{"account_id": "A1", "p": 0.31, "reasons": ["Seats used fell 40%"]}])
    lp, ap = writeback.payload(Conn(), TID, "salesforce", scored, risk)
    assert lp == [{"id": "00Q1", "score": 73, "reason": "Demo requested; Exec buyer"}]
    assert ap == [{"id": "001A", "risk": 31, "reason": "Seats used fell 40%"}]
    assert writeback.payload(Conn(), TID, "hubspot", scored, risk) == ([], [])
