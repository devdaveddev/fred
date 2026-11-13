"""CSV imports: the universal path for accounting, payroll, budgets, ad spend, surveys and usage.

Every parser returns clean records or raises ImportProblem with a message a finance person can act on
("row 14: amount '12,4O0' is not a number"). Formats accepted are documented in TEMPLATES and the
UI offers a sample file for each.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime

from .mapping import parse_code


class ImportProblem(ValueError):
    pass


TEMPLATES = {
    "billing": {"label": "Billing / MRR", "columns": ["customer_id", "customer_name", "email", "month", "mrr"],
                "help": "One row per customer per month with their recurring revenue (any billing system)."},
    "gl": {"label": "Accounting: P&L by month", "columns": ["month", "account", "amount", "section"],
           "help": "Long format, or QuickBooks 'Profit and Loss by Month' / Xero 'Profit and Loss' exported to CSV as-is."},
    "cash": {"label": "Cash balance", "columns": ["month", "cash"], "help": "Month-end bank balance."},
    "payroll": {"label": "Team roster (payroll)", "columns": ["name", "title", "department", "location", "start_date",
                                                               "end_date", "salary_annual", "loaded_factor"],
                "help": "From Gusto, Rippling or Deel. Departments: Sales, Marketing, Customer Success, Engineering, Product, G&A."},
    "budget": {"label": "Annual budget", "columns": ["account", "month", "amount"],
               "help": "Your approved plan, by account and month. Wide format (one column per month) also works."},
    "hiring_plan": {"label": "Hiring plan", "columns": ["role", "department", "location", "planned_start", "salary_annual"],
                    "help": "Planned hires in the budget year. Makes payroll variances explainable person by person."},
    "spend": {"label": "Ad spend by channel", "columns": ["date", "channel", "amount"],
              "help": "Daily spend per channel (paid_search, linkedin, events, content or your own names)."},
    "feedback": {"label": "Customer feedback", "columns": ["date", "customer_id", "kind", "score", "text"],
                 "help": "Exit surveys, NPS comments or support tickets. kind = exit_survey, nps or support_ticket."},
    "usage": {"label": "Product usage", "columns": ["customer_id", "week", "seats", "active_users", "events"],
              "help": "Weekly active users per customer, from PostHog, Segment, Amplitude or your database."},
}

MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def parse_month(v: str) -> date | None:
    t = (v or "").strip()
    if not t:
        return None
    m = re.match(r"^(\d{4})-(\d{1,2})(?:-\d{1,2})?$", t)
    if m:
        return date(int(m.group(1)), int(m.group(2)), 1)
    m = re.match(r"^(\d{1,2})/(\d{4})$", t)
    if m:
        return date(int(m.group(2)), int(m.group(1)), 1)
    m = re.match(r"^([A-Za-z]{3,9})\.?[\s\-]+(\d{2,4})$", t)
    if m and m.group(1)[:3].lower() in MONTHS:
        y = int(m.group(2))
        return date(y + 2000 if y < 100 else y, MONTHS[m.group(1)[:3].lower()], 1)
    return None


def parse_date(v: str) -> date | None:
    t = (v or "").strip()
    if not t:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}", t):  # ISO date, possibly with a time part
        return date.fromisoformat(t[:10])
    for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    m = parse_month(t)
    return m


def parse_amount(v: str) -> float | None:
    t = (v or "").strip().replace("−", "-")
    if t in ("", "-", "—"):
        return None
    neg = t.startswith("(") and t.endswith(")")
    t = re.sub(r"[,$€£₹\s()]", "", t)
    if t.endswith("-"):
        neg, t = True, t[:-1]
    try:
        x = float(t)
    except ValueError:
        return None
    return -x if neg else x


def _rows(text: str) -> list[list[str]]:
    text = text.lstrip("﻿")
    return [r for r in csv.reader(io.StringIO(text))]


def _dict_rows(text: str, required: list[str]) -> list[dict]:
    rows = _rows(text)
    if not rows:
        raise ImportProblem("The file is empty.")
    header = [h.strip().lower().replace(" ", "_") for h in rows[0]]
    missing = [c for c in required if c not in header]
    if missing:
        raise ImportProblem(f"Missing column(s): {', '.join(missing)}. Expected: {', '.join(required)}.")
    return [dict(zip(header, r)) | {"_row": i + 2} for i, r in enumerate(rows[1:]) if any(c.strip() for c in r)]


def _need(row: dict, col: str, parser, label: str):
    v = parser(row.get(col, ""))
    if v is None:
        raise ImportProblem(f"Row {row['_row']}: {label} '{row.get(col, '')}' could not be read.")
    return v


# ------------------------------------------------------------------ parsers
def billing(text: str) -> list[dict]:
    out = []
    for r in _dict_rows(text, ["customer_id", "month", "mrr"]):
        out.append({"customer_id": r["customer_id"].strip(), "name": (r.get("customer_name") or r["customer_id"]).strip(),
                    "email": (r.get("email") or "").strip() or None, "month": _need(r, "month", parse_month, "month"),
                    "mrr": _need(r, "mrr", parse_amount, "mrr")})
    return out


def gl(text: str) -> dict:
    """Long format or a QuickBooks/Xero 'P&L by month' export. Returns accounts + monthly amounts."""
    rows = _rows(text)
    head = [c.strip().lower() for c in rows[0]] if rows else []
    if {"month", "account", "amount"} <= set(head):
        recs, accounts = [], {}
        for r in _dict_rows(text, ["month", "account", "amount"]):
            code, name = parse_code(r["account"])
            key = code or name
            accounts.setdefault(key, {"code": key, "name": name, "section": r.get("section") or None})
            recs.append({"month": _need(r, "month", parse_month, "month"), "account": key,
                         "amount": _need(r, "amount", parse_amount, "amount")})
        return {"accounts": list(accounts.values()), "amounts": recs, "format": "long"}
    # wide: find the header row whose cells are months
    hdr_i = next((i for i, r in enumerate(rows) if sum(parse_month(c) is not None for c in r[1:]) >= 2), None)
    if hdr_i is None:
        raise ImportProblem("Couldn't find month columns. Export 'Profit and Loss' with columns by month, or use "
                            "the long format: month, account, amount.")
    months = [(j, parse_month(c)) for j, c in enumerate(rows[hdr_i]) if j > 0 and parse_month(c)]
    accounts, recs, section = {}, [], None
    skip = re.compile(r"^(total|gross profit|net (operating )?income|net other income|net income|net profit)", re.IGNORECASE)
    for r in rows[hdr_i + 1:]:
        if not r or not r[0].strip():
            continue
        label = r[0].strip()
        vals = [(m, parse_amount(r[j]) if j < len(r) else None) for j, m in months]
        if all(v is None for _, v in vals):
            section = label  # a section header like "Income" or "Expenses"
            continue
        if skip.match(label):
            continue
        code, name = parse_code(label)
        key = code or name
        accounts.setdefault(key, {"code": key, "name": name, "section": section})
        recs += [{"month": m, "account": key, "amount": v} for m, v in vals if v is not None]
    if not recs:
        raise ImportProblem("No account rows with amounts were found under the month columns.")
    return {"accounts": list(accounts.values()), "amounts": recs, "format": "wide"}


def cash(text: str) -> list[dict]:
    return [{"month": _need(r, "month", parse_month, "month"), "cash": _need(r, "cash", parse_amount, "cash")}
            for r in _dict_rows(text, ["month", "cash"])]


DEPTS = {"sales": "Sales", "marketing": "Marketing", "customer success": "Customer Success", "cs": "Customer Success",
         "support": "Customer Success", "engineering": "Engineering", "r&d": "Engineering", "product": "Product",
         "design": "Product", "g&a": "G&A", "finance": "G&A", "operations": "G&A", "people": "G&A", "hr": "G&A",
         "admin": "G&A", "executive": "G&A"}


def _dept(v: str, row: int) -> str:
    d = DEPTS.get((v or "").strip().lower())
    if not d:
        raise ImportProblem(f"Row {row}: department '{v}' is not one of Sales, Marketing, Customer Success, "
                            "Engineering, Product, G&A.")
    return d


def payroll(text: str) -> list[dict]:
    out = []
    for r in _dict_rows(text, ["name", "title", "department", "start_date", "salary_annual"]):
        lf = parse_amount(r.get("loaded_factor", "")) or 1.2
        out.append({"name": r["name"].strip(), "title": r["title"].strip(), "department": _dept(r["department"], r["_row"]),
                    "location": (r.get("location") or "US").strip() or "US",
                    "start_date": _need(r, "start_date", parse_date, "start date"),
                    "end_date": parse_date(r.get("end_date", "")),
                    "salary_annual": _need(r, "salary_annual", parse_amount, "salary"), "loaded_factor": lf})
    return out


def budget(text: str) -> list[dict]:
    rows = _rows(text)
    head = [c.strip().lower() for c in rows[0]] if rows else []
    if {"account", "month", "amount"} <= set(head):
        return [{"account": parse_code(r["account"])[0] or parse_code(r["account"])[1],
                 "month": _need(r, "month", parse_month, "month"), "amount": _need(r, "amount", parse_amount, "amount")}
                for r in _dict_rows(text, ["account", "month", "amount"])]
    g = gl(text)  # same wide layout as a P&L export
    return [{"account": a["account"], "month": a["month"], "amount": a["amount"]} for a in g["amounts"]]


def hiring_plan(text: str) -> list[dict]:
    return [{"role": r["role"].strip(), "department": _dept(r["department"], r["_row"]),
             "location": (r.get("location") or "US").strip() or "US",
             "planned_start": _need(r, "planned_start", parse_month, "planned start"),
             "salary_annual": _need(r, "salary_annual", parse_amount, "salary")}
            for r in _dict_rows(text, ["role", "department", "planned_start", "salary_annual"])]


def spend(text: str) -> list[dict]:
    return [{"date": _need(r, "date", parse_date, "date"), "channel": r["channel"].strip().lower().replace(" ", "_"),
             "amount": _need(r, "amount", parse_amount, "amount")} for r in _dict_rows(text, ["date", "channel", "amount"])]


def feedback(text: str) -> list[dict]:
    out = []
    for r in _dict_rows(text, ["date", "kind", "text"]):
        kind = r["kind"].strip().lower().replace(" ", "_")
        if kind not in ("exit_survey", "nps", "support_ticket"):
            raise ImportProblem(f"Row {r['_row']}: kind '{r['kind']}' must be exit_survey, nps or support_ticket.")
        score = parse_amount(r.get("score", ""))
        out.append({"date": _need(r, "date", parse_date, "date"), "customer_id": (r.get("customer_id") or "").strip() or None,
                    "kind": kind, "score": int(score) if score is not None else None, "text": r["text"].strip()})
    return out


def usage(text: str) -> list[dict]:
    return [{"customer_id": r["customer_id"].strip(), "week": _need(r, "week", parse_date, "week"),
             "seats": int(_need(r, "seats", parse_amount, "seats")),
             "active_users": int(_need(r, "active_users", parse_amount, "active users")),
             "events": int(parse_amount(r.get("events", "")) or 0)}
            for r in _dict_rows(text, ["customer_id", "week", "seats", "active_users"])]


PARSERS = {"billing": billing, "gl": gl, "cash": cash, "payroll": payroll, "budget": budget,
           "hiring_plan": hiring_plan, "spend": spend, "feedback": feedback, "usage": usage}
