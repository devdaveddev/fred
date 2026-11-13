"""QuickBooks Online and Xero: monthly Profit & Loss and bank balances, straight from the ledger.

Both parse into exactly what the CSV importer produces ({"accounts", "amounts"} for the P&L, [{"month","cash"}]
for cash), so account mapping, user overrides and spend derivation work identically for every source.
"""
from __future__ import annotations

from datetime import date, datetime

from ..config import get_settings
from .csv_import import parse_amount
from .http import Api, SourceError
from .mapping import parse_code

MONTHS_BACK = 24


def _month_add(m: date, n: int) -> date:
    y, mo = divmod(m.month - 1 + n, 12)
    return date(m.year + y, mo + 1, 1)


def window(last_closed: date, months: int = MONTHS_BACK) -> tuple[date, date]:
    first = _month_add(last_closed, -(months - 1))
    nxt = _month_add(last_closed, 1)
    return first, date.fromordinal(nxt.toordinal() - 1)


def _account(accounts: dict, label: str, section: str | None) -> str:
    code, name = parse_code(label)
    key = code or name
    accounts.setdefault(key, {"code": key, "name": name, "section": section})
    return key


# ------------------------------------------------------------------ QuickBooks
QBO_SECTION = {"Income": "Income", "COGS": "Cost of Goods Sold", "Expenses": "Expenses",
               "OtherIncome": "Other Income", "OtherExpenses": "Other Expenses"}


def qbo_base() -> str:
    return "https://sandbox-quickbooks.api.intuit.com" if get_settings().quickbooks_sandbox \
        else "https://quickbooks.api.intuit.com"


def qbo_months(report: dict) -> list[tuple[int, date | None]]:
    out = []
    for j, col in enumerate(report.get("Columns", {}).get("Column", [])):
        meta = {m.get("Name"): m.get("Value") for m in col.get("MetaData", []) or []}
        start = meta.get("StartDate")
        out.append((j, datetime.strptime(start, "%Y-%m-%d").date().replace(day=1)
                    if start and col.get("ColType") == "Money" and col.get("ColTitle", "").lower() != "total" else None))
    return out


def parse_qbo_pnl(report: dict) -> dict:
    months = [(j, m) for j, m in qbo_months(report) if m]
    accounts: dict[str, dict] = {}
    recs: list[dict] = []

    def walk(rows: list, section: str | None) -> None:
        for r in rows:
            if r.get("type") == "Section" or "Rows" in r:
                sec = section or QBO_SECTION.get(r.get("group", ""), (r.get("Header", {}).get("ColData") or [{}])[0].get("value"))
                walk(r.get("Rows", {}).get("Row", []), sec)
            elif r.get("type") == "Data" or "ColData" in r:
                cols = r["ColData"]
                label = cols[0].get("value", "").strip()
                if not label:
                    continue
                key = _account(accounts, label, section)
                for j, m in months:
                    v = parse_amount(cols[j].get("value", "")) if j < len(cols) else None
                    if v:
                        recs.append({"month": m, "account": key, "amount": v})

    walk(report.get("Rows", {}).get("Row", []), None)
    if not recs:
        raise SourceError("QuickBooks returned a Profit and Loss report with no amounts for these months.")
    return {"accounts": list(accounts.values()), "amounts": recs, "format": "quickbooks"}


def parse_qbo_cash(report: dict) -> list[dict]:
    months = [(j, m) for j, m in qbo_months(report) if m]

    def find(rows: list) -> dict | None:
        for r in rows:
            if r.get("group") == "BankAccounts":
                return r
            hit = find(r.get("Rows", {}).get("Row", []))
            if hit:
                return hit
        return None

    bank = find(report.get("Rows", {}).get("Row", []))
    if not bank or "Summary" not in bank:
        return []
    cols = bank["Summary"]["ColData"]
    return [{"month": m, "cash": parse_amount(cols[j].get("value", "")) or 0.0} for j, m in months if j < len(cols)]


def qbo_fetch(token: str, realm: str, last_closed: date) -> dict:
    api = Api("QuickBooks", f"{qbo_base()}/v3/company/{realm}",
              headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    start, end = window(last_closed)
    params = {"start_date": start.isoformat(), "end_date": end.isoformat(), "summarize_column_by": "Month",
              "accounting_method": "Accrual", "minorversion": "75"}
    pnl = api.get("/reports/ProfitAndLoss", params=params)
    bs = api.get("/reports/BalanceSheet", params=params)
    return {"gl": parse_qbo_pnl(pnl), "cash": parse_qbo_cash(bs)}


# ------------------------------------------------------------------ Xero
def _xero_month(v: str) -> date | None:
    for fmt in ("%d %b %Y", "%b %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(v.strip(), fmt).date().replace(day=1)
        except ValueError:
            continue
    return None


def _xero_section(title: str) -> str:
    t = (title or "").strip()
    return t[5:] if t.lower().startswith("less ") else t


def parse_xero_pnl(report: dict, accounts: dict, recs: list) -> None:
    rows = report["Reports"][0]["Rows"]
    header = next(r for r in rows if r.get("RowType") == "Header")
    months = [(j, _xero_month(c.get("Value", ""))) for j, c in enumerate(header["Cells"])]
    months = [(j, m) for j, m in months if j > 0 and m]
    for sec in rows:
        if sec.get("RowType") != "Section":
            continue
        section = _xero_section(sec.get("Title", ""))
        for r in sec.get("Rows", []):
            if r.get("RowType") != "Row":
                continue
            cells = r["Cells"]
            label = cells[0].get("Value", "").strip()
            if not label:
                continue
            key = _account(accounts, label, section)
            for j, m in months:
                v = parse_amount(cells[j].get("Value", "")) if j < len(cells) else None
                if v:
                    recs.append({"month": m, "account": key, "amount": v})


def parse_xero_cash(report: dict) -> list[dict]:
    rows = report["Reports"][0]["Rows"]
    header = next(r for r in rows if r.get("RowType") == "Header")
    months = [(j, _xero_month(c.get("Value", ""))) for j, c in enumerate(header["Cells"])]
    for sec in rows:
        if sec.get("RowType") == "Section" and sec.get("Title", "").strip().lower() == "bank":
            total = next((r for r in sec.get("Rows", []) if r.get("RowType") == "SummaryRow"), None)
            if total:
                return [{"month": m, "cash": parse_amount(total["Cells"][j].get("Value", "")) or 0.0}
                        for j, m in months if j > 0 and m and j < len(total["Cells"])]
    return []


def xero_tenant(token: str) -> dict:
    conns = Api("Xero", "https://api.xero.com", headers={"Authorization": f"Bearer {token}"}).get("/connections")
    orgs = [c for c in conns if c.get("tenantType") == "ORGANISATION"]
    if not orgs:
        raise SourceError("This Xero login has no organisation connected to Fred.")
    return {"tenant_id": orgs[0]["tenantId"], "organisation": orgs[0].get("tenantName")}


def xero_fetch(token: str, xero_tenant_id: str, last_closed: date) -> dict:
    api = Api("Xero", "https://api.xero.com/api.xro/2.0",
              headers={"Authorization": f"Bearer {token}", "xero-tenant-id": xero_tenant_id, "Accept": "application/json"})
    accounts: dict[str, dict] = {}
    recs: list[dict] = []
    cash: dict[date, float] = {}
    # Xero reports at most 12 monthly columns per call: two calls cover 24 months
    for end_month in (last_closed, _month_add(last_closed, -12)):
        end = date.fromordinal(_month_add(end_month, 1).toordinal() - 1)
        start = end_month
        pnl = api.get("/Reports/ProfitAndLoss", params={"fromDate": start.isoformat(), "toDate": end.isoformat(),
                                                         "periods": 11, "timeframe": "MONTH"})
        parse_xero_pnl(pnl, accounts, recs)
        bs = api.get("/Reports/BalanceSheet", params={"date": end.isoformat(), "periods": 11, "timeframe": "MONTH"})
        for r in parse_xero_cash(bs):
            cash.setdefault(r["month"], r["cash"])
    if not recs:
        raise SourceError("Xero returned a Profit and Loss report with no amounts for these months.")
    return {"gl": {"accounts": list(accounts.values()), "amounts": recs, "format": "xero"},
            "cash": [{"month": m, "cash": v} for m, v in sorted(cash.items())]}
