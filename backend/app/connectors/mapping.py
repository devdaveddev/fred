"""Chart-of-accounts mapping: which P&L line is each account, is it payroll, which department.

Rules handle the common names (most QuickBooks/Xero charts); for anything else the pre-trained
embedding model picks the closest category description (zero-shot). Every suggestion carries its
method and confidence, and the user can override it in the mapping screen.
"""
from __future__ import annotations

import re

DEPARTMENTS = ["Sales", "Marketing", "Customer Success", "Engineering", "Product", "G&A"]

# (pattern, pl_group, kind, department) in priority order
RULES: list[tuple[str, str, str, str | None]] = [
    # payroll first: "Salaries: Sales" is a salary, not revenue
    (r"\b(salar|wages|payroll|bonus|commission|employer tax|benefits|health insurance|401k|pension)", "payroll", "payroll", None),
    (r"\b(subscription|saas|recurring|software)\b.*\b(revenue|income|sales)\b|\b(revenue|income)\b.*subscription", "revenue", "revenue", None),
    (r"\b(revenue|sales|income)\b(?!.*(cost|tax|commission|tool))", "revenue", "revenue", None),
    (r"\b(hosting|aws|amazon web services|gcp|google cloud|azure|cloud|infrastructure|servers?|data ?center)\b", "cogs", "cogs", None),
    (r"\b(cost of (goods|sales|revenue)|cogs|merchant fees|payment processing|stripe fees|third[- ]party api)\b", "cogs", "cogs", None),
    (r"\b(advertis|google ads|adwords|linkedin ads|facebook ads|meta ads|paid (search|social)|ppc|sponsorship|events?|conference|trade show|content|seo|marketing)", "sm", "nonpayroll", None),
    (r"\b(travel|meals|entertainment|lodging|airfare)\b", "sm", "nonpayroll", None),
    (r"\b(software|subscriptions?|saas tools|licen[cs]es?|tools?)\b", "ga", "nonpayroll", None),
    (r"\b(rent|lease|office|utilities|coworking)\b", "ga", "nonpayroll", None),
    (r"\b(legal|accounting|audit|professional|consult|bookkeeping)\b", "ga", "nonpayroll", None),
    (r"\b(insurance|bank (fees|charges)|dues|depreciation|amortization|taxes and licenses)\b", "ga", "nonpayroll", None),
]
DEPT_RULES = [
    (r"\b(sales|account exec(utive)?s?|ae|sdrs?|bdrs?|business development)\b", "Sales"),
    (r"\b(marketing|growth|demand gen|content)\b", "Marketing"),
    (r"\b(customer success|support|cs|account management|onboarding)\b", "Customer Success"),
    (r"\b(engineering|developers?|r&d|research|technology|tech)\b", "Engineering"),
    (r"\b(product|design)\b", "Product"),
    (r"\b(g&a|general|admin|finance|people|hr|operations|executive)\b", "G&A"),
]
SECTION_HINT = {"income": "revenue", "revenue": "revenue", "cost of goods sold": "cogs", "cost of sales": "cogs",
                "cost of revenue": "cogs", "expenses": None, "operating expenses": None}

PROTOTYPES = {
    ("revenue", "revenue"): "income from customers: subscription revenue, sales, services income",
    ("cogs", "cogs"): "cost of delivering the product: cloud hosting, servers, payment processing fees, support tools",
    ("sm", "payroll"): "salaries and commissions of the sales and marketing team",
    ("sm", "nonpayroll"): "marketing spend: advertising, campaigns, events, sponsorships, content, travel to customers",
    ("rnd", "payroll"): "salaries of engineers, developers and product designers",
    ("rnd", "nonpayroll"): "research and development tools, developer infrastructure, contractors for engineering",
    ("ga", "payroll"): "salaries of finance, HR, operations and executive staff",
    ("ga", "nonpayroll"): "general and administrative costs: rent, office, software subscriptions, legal and accounting fees, insurance",
}


def parse_code(name: str) -> tuple[str | None, str]:
    """'6000 Salaries - Sales' -> ('6000', 'Salaries - Sales')."""
    m = re.match(r"^\s*(\d{3,6})[\s\-·:.]+(.*)$", name)
    return (m.group(1), m.group(2).strip()) if m else (None, name.strip())


def _dept(text: str) -> str | None:
    for pat, d in DEPT_RULES:
        if re.search(pat, text, re.IGNORECASE):
            return d
    return None


def classify(names: list[str], sections: list[str | None] | None = None, embed=None) -> list[dict]:
    sections = sections or [None] * len(names)
    out: list[dict | None] = []
    pending = []
    for i, (raw, sec) in enumerate(zip(names, sections)):
        _, name = parse_code(raw)
        low = name.lower()
        hint = SECTION_HINT.get((sec or "").strip().lower())
        expense_section = "expense" in (sec or "").lower()
        hit = None
        for pat, group, kind, dept in RULES:
            if expense_section and group == "revenue":
                continue  # nothing filed under Expenses is revenue
            if re.search(pat, low, re.IGNORECASE):
                hit = (group, kind, dept)
                break
        if hit and hint == "revenue" and hit[0] != "revenue":
            hit = ("revenue", "revenue", None)
        if hit and hit[0] == "payroll":
            d = _dept(low) or "G&A"
            group = {"Sales": "sm", "Marketing": "sm", "Customer Success": "sm", "Engineering": "rnd",
                     "Product": "rnd"}.get(d, "ga")
            hit = (group, "payroll", d)
        if hit is None and hint in ("revenue", "cogs"):
            hit = (hint, hint, None)
        if hit:
            out.append({"pl_group": hit[0], "kind": hit[1], "department": hit[2], "method": "rule", "confidence": 0.95})
        else:
            out.append(None)
            pending.append(i)
    if pending:
        if embed is None:
            for i in pending:
                out[i] = {"pl_group": "ga", "kind": "nonpayroll", "department": None, "method": "default",
                          "confidence": 0.3}
        else:
            import numpy as np

            keys = list(PROTOTYPES)
            P = embed(list(PROTOTYPES.values()))
            E = embed([parse_code(names[i])[1] for i in pending])
            S = E @ P.T
            for row, i in enumerate(pending):
                sec = (sections[i] or "").lower()
                if "expense" in sec:  # filed under expenses: never revenue or cost of revenue
                    S[row, [k for k, (g, _) in enumerate(keys) if g in ("revenue", "cogs")]] = -np.inf
                elif SECTION_HINT.get(sec.strip()) == "revenue":
                    S[row, [k for k, (g, _) in enumerate(keys) if g != "revenue"]] = -np.inf
                j = int(np.argmax(S[row]))
                group, kind = keys[j]
                dept = _dept(names[i]) if kind == "payroll" else None
                if kind == "payroll" and dept is None:
                    dept = {"sm": "Sales", "rnd": "Engineering"}.get(group, "G&A")
                out[i] = {"pl_group": group, "kind": "cogs" if group == "cogs" else ("revenue" if group == "revenue" else kind),
                          "department": dept, "method": "model", "confidence": float(S[row, j])}
    return out  # type: ignore[return-value]
