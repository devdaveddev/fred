"""Demo finance data: chart of accounts, payroll roster, GL actuals and an approved annual plan.

Planted stories the budget-vs-actual engine must find (tests check each one):
  * hiring: planned hires start late, one planned role is cancelled, one person leaves and is backfilled
  * raises: plan assumed 3% merit raises in April; actual raises were 4%
  * software subscriptions step up 35% from June (tool sprawl): a run-rate change
  * professional fees spike in March (fundraising legal): a one-off
  * hosting runs 12% hot from August
  * revenue: the plan assumed 25% more new customers and lower churn than happened
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

import numpy as np

# code, name, group (revenue|cogs|sm|rnd|ga), kind (revenue|cogs|payroll|nonpayroll), department, spend channel
ACCOUNTS = [
    ("4000", "Subscription revenue", "revenue", "revenue", None, None),
    ("5000", "Hosting (cloud)", "cogs", "cogs", None, None),
    ("5100", "Third-party APIs", "cogs", "cogs", None, None),
    ("6000", "Salaries: Sales", "sm", "payroll", "Sales", None),
    ("6010", "Salaries: Marketing", "sm", "payroll", "Marketing", None),
    ("6020", "Salaries: Customer Success", "sm", "payroll", "Customer Success", None),
    ("6100", "Advertising: Paid search", "sm", "nonpayroll", None, "paid_search"),
    ("6110", "Advertising: LinkedIn", "sm", "nonpayroll", None, "linkedin"),
    ("6120", "Events & sponsorships", "sm", "nonpayroll", None, "events"),
    ("6130", "Content marketing", "sm", "nonpayroll", None, "content"),
    ("6200", "Travel & entertainment", "sm", "nonpayroll", None, None),
    ("7000", "Salaries: Engineering", "rnd", "payroll", "Engineering", None),
    ("7010", "Salaries: Product", "rnd", "payroll", "Product", None),
    ("8000", "Salaries: G&A", "ga", "payroll", "G&A", None),
    ("8100", "Software subscriptions", "ga", "nonpayroll", None, None),
    ("8200", "Rent & office", "ga", "nonpayroll", None, None),
    ("8300", "Professional fees", "ga", "nonpayroll", None, None),
]
DEPT_ACCOUNT = {a[4]: a[0] for a in ACCOUNTS if a[4]}
CHANNEL_ACCOUNT = {a[5]: a[0] for a in ACCOUNTS if a[5]}

ROLES = {
    "Software Engineer": ("Engineering", {"US": 150_000, "IN": 42_000}),
    "Senior Engineer": ("Engineering", {"US": 185_000, "IN": 60_000}),
    "Product Manager": ("Product", {"US": 155_000}),
    "Designer": ("Product", {"US": 135_000}),
    "Account Executive": ("Sales", {"US": 115_000}),
    "SDR": ("Sales", {"US": 65_000}),
    "Marketing Manager": ("Marketing", {"US": 120_000}),
    "Customer Success Manager": ("Customer Success", {"US": 90_000, "IN": 30_000}),
    "Finance & Ops Lead": ("G&A", {"US": 135_000}),
    "People Ops": ("G&A", {"US": 105_000, "IN": 32_000}),
}
LOADED = {"US": 1.22, "IN": 1.10}  # employer taxes + benefits
INITIAL = ([("Software Engineer", "US")] * 4 + [("Senior Engineer", "US")] * 2 + [("Software Engineer", "IN")] * 6
           + [("Product Manager", "US"), ("Designer", "US")] + [("Account Executive", "US")] * 3
           + [("SDR", "US")] * 2 + [("Marketing Manager", "US")] * 2 + [("Customer Success Manager", "US")] * 2
           + [("Customer Success Manager", "IN"), ("Finance & Ops Lead", "US"), ("People Ops", "US"),
              ("People Ops", "IN")])
PRE_PLAN_HIRES = [(2, "Software Engineer", "IN"), (3, "Account Executive", "US"), (4, "Senior Engineer", "US"),
                  (5, "Customer Success Manager", "IN"), (6, "SDR", "US"), (7, "Software Engineer", "US"),
                  (8, "Marketing Manager", "US"), (9, "Software Engineer", "IN"), (11, "Account Executive", "US"),
                  (12, "Senior Engineer", "IN")]
PRE_PLAN_DEPARTURE = (7, "SDR")
# (fiscal month 1-12, role, location)
FY_PLAN_HIRES = [(1, "Senior Engineer", "US"), (2, "Software Engineer", "IN"), (2, "Account Executive", "US"),
                 (3, "Customer Success Manager", "US"), (4, "Software Engineer", "US"), (4, "Product Manager", "US"),
                 (5, "SDR", "US"), (6, "Senior Engineer", "US"), (6, "Marketing Manager", "US"),
                 (7, "Software Engineer", "IN"), (8, "Account Executive", "US"), (9, "People Ops", "US"),
                 (10, "Finance & Ops Lead", "US"), (11, "Software Engineer", "US"), (12, "Account Executive", "US")]
FY_CANCELLED = {"People Ops"}  # planned, then cancelled
FY_DEPARTURE = (4, "Senior Engineer")  # someone leaves in fiscal April
FY_BACKFILL = (6, "Senior Engineer", "US")  # unplanned backfill hire
RAISE_MONTH = 4
RAISE_PLAN, RAISE_ACTUAL = 0.03, 0.04
FIRST = ["Aarav", "Maya", "Liam", "Priya", "Noah", "Zara", "Ethan", "Ishaan", "Sofia", "Kabir", "Emma", "Rohan",
         "Ava", "Arjun", "Mia", "Leo", "Anaya", "Lucas", "Diya", "Oliver", "Nina", "Vikram", "Grace", "Sameer"]
LAST = ["Shah", "Kim", "Patel", "Müller", "Garcia", "Iyer", "Smith", "Rao", "Chen", "Nair", "Brown", "Khan",
        "Lopez", "Mehta", "Wong", "Fischer"]


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


@dataclass
class People:
    employees: list[dict] = field(default_factory=list)
    planned_hires: list[dict] = field(default_factory=list)
    fy_start: date | None = None
    plan_id: str = ""

    def monthly_cost(self, e: dict, m: date) -> float:
        if m < e["start_date"] or (e["end_date"] and m >= e["end_date"]):
            return 0.0
        sal = e["salary_annual"]
        if e["start_date"] < self.fy_start and m >= add_months(self.fy_start, RAISE_MONTH - 1):
            sal *= 1 + RAISE_ACTUAL
        return round(sal * LOADED[e["location"]] / 12, 2)

    def payroll_by_dept(self, m: date) -> dict[str, float]:
        out: dict[str, float] = {}
        for e in self.employees:
            c = self.monthly_cost(e, m)
            if c:
                out[e["department"]] = out.get(e["department"], 0.0) + c
        return out


def build_people(months: list[date], rng: np.random.Generator) -> People:
    uid = lambda: str(uuid.UUID(int=int(rng.integers(0, 2**63))))
    last = months[-1]
    fy_start = date(last.year, 1, 1)
    p = People(fy_start=fy_start, plan_id=f"plan_fy{fy_start.year}")

    def hire(role: str, loc: str, start: date, salary: float | None = None) -> dict:
        dept, sal = ROLES[role]
        e = {"id": uid(), "name": f"{FIRST[rng.integers(len(FIRST))]} {LAST[rng.integers(len(LAST))]}",
             "title": role, "department": dept, "location": loc, "start_date": start, "end_date": None,
             "salary_annual": float(salary if salary is not None else sal[loc])}
        p.employees.append(e)
        return e

    for role, loc in INITIAL:
        hire(role, loc, months[0])
    for i, role, loc in PRE_PLAN_HIRES:
        hire(role, loc, months[i])
    leaver = next(e for e in p.employees if e["title"] == PRE_PLAN_DEPARTURE[1])
    leaver["end_date"] = months[PRE_PLAN_DEPARTURE[0]]

    # ---- the approved plan's hiring list, then what actually happened
    for fm, role, loc in FY_PLAN_HIRES:
        dept, sal = ROLES[role]
        planned = add_months(fy_start, fm - 1)
        h = {"id": uid(), "role": role, "department": dept, "location": loc, "planned_start": planned,
             "salary_annual": float(sal[loc]), "employee_id": None, "status": "planned"}
        p.planned_hires.append(h)
        if role in FY_CANCELLED:
            h["status"] = "cancelled"
            continue
        delay = int(rng.choice([0, 0, 0, 1, 1, 2, 3]))
        actual = add_months(planned, delay)
        if actual > last:
            continue  # not started yet (open req)
        e = hire(role, loc, actual, salary=round(sal[loc] * rng.uniform(0.96, 1.10), -3))
        h["employee_id"] = e["id"]
        h["status"] = "started"

    leaver = next(e for e in p.employees if e["title"] == FY_DEPARTURE[1] and e["start_date"] < fy_start
                  and e["location"] == "US")
    leaver["end_date"] = add_months(fy_start, FY_DEPARTURE[0] - 1)
    fm, role, loc = FY_BACKFILL
    if add_months(fy_start, fm - 1) <= last:
        hire(role, loc, add_months(fy_start, fm - 1), salary=round(ROLES[role][1][loc] * 1.05, -3))
    return p


def plan_payroll(p: People) -> list[dict]:
    """Payroll budget per person per fiscal month: staff on board at plan time + planned hires."""
    rows = []
    fy_months = [add_months(p.fy_start, k) for k in range(12)]
    plan_time = add_months(p.fy_start, -1)
    for e in p.employees:
        if e["start_date"] > plan_time or (e["end_date"] and e["end_date"] <= plan_time):
            continue
        for m in fy_months:
            sal = e["salary_annual"] * (1 + RAISE_PLAN if m >= add_months(p.fy_start, RAISE_MONTH - 1) else 1)
            rows.append({"month": m, "ref_type": "employee", "ref_id": e["id"], "department": e["department"],
                         "amount": round(sal * LOADED[e["location"]] / 12, 2)})
    for h in p.planned_hires:
        for m in fy_months:
            if m >= h["planned_start"]:
                rows.append({"month": m, "ref_type": "hire", "ref_id": h["id"], "department": h["department"],
                             "amount": round(h["salary_annual"] * LOADED[h["location"]] / 12, 2)})
    return rows


def nonpayroll_actual(i: int, m: date, fy_start: date, rng: np.random.Generator) -> dict[str, float]:
    fm = (m.year - fy_start.year) * 12 + m.month - fy_start.month + 1  # fiscal month, <=0 before FY
    software = 18_000 * 1.01 ** i * (1.35 if fm >= 6 else 1.0)
    fees = 9_000 * rng.uniform(0.85, 1.15) + (42_000 if fm == 3 else 0.0)
    return {"6200": round(6_000 * (1 + 0.02 * i) * rng.uniform(0.85, 1.15), 2),
            "8100": round(software * rng.uniform(0.98, 1.02), 2),
            "8200": 22_000.0,
            "8300": round(fees, 2)}


def cogs_actual(revenue: float, m: date, fy_start: date) -> dict[str, float]:
    fm = (m.year - fy_start.year) * 12 + m.month - fy_start.month + 1
    return {"5000": round(revenue * 0.15 * (1.12 if fm >= 8 else 1.0), 2), "5100": round(revenue * 0.07, 2)}


def build_plan(p: People, months: list[date], mrr_by_month: dict[date, float], new_by_month: dict[date, tuple],
               paid_by_month: dict[date, dict[str, float]], nonpayroll_hist: dict[date, dict[str, float]]) -> dict:
    """Annual plan built the way a finance lead would, from the second half of last year."""
    plan_time = add_months(p.fy_start, -1)
    h2 = [m for m in months if add_months(plan_time, -5) <= m <= plan_time]
    n_hist = np.mean([new_by_month[m][0] for m in h2])
    arpa_hist = np.mean([new_by_month[m][1] for m in h2])
    drivers = {"new_customers": round(float(n_hist) * 1.25, 1), "arpa_new": round(float(arpa_hist), 2),
               "revenue_churn": 0.016, "net_expansion": 0.008}
    fy_months = [add_months(p.fy_start, k) for k in range(12)]
    lines: list[dict] = []
    mrr = mrr_by_month[plan_time]
    driver_rows = []
    for k, m in enumerate(fy_months):
        mrr = mrr * (1 - drivers["revenue_churn"] + drivers["net_expansion"]) + \
            drivers["new_customers"] * drivers["arpa_new"]
        driver_rows.append({"month": m, **drivers})
        lines.append({"month": m, "account": "4000", "amount": round(mrr, 2)})
        lines.append({"month": m, "account": "5000", "amount": round(mrr * 0.15, 2)})
        lines.append({"month": m, "account": "5100", "amount": round(mrr * 0.07, 2)})
        for ch, acct in CHANNEL_ACCOUNT.items():
            base = np.mean([paid_by_month[x][ch] for x in h2])
            lines.append({"month": m, "account": acct, "amount": round(base * 1.012 ** (k + 1), 2)})
        for acct in ("6200", "8100", "8200", "8300"):
            base = np.mean([nonpayroll_hist[x][acct] for x in h2])
            growth = 1.01 ** (k + 1) if acct != "8200" else 1.0
            lines.append({"month": m, "account": acct, "amount": round(base * growth, 2)})
    pay = plan_payroll(p)
    for m in fy_months:
        for dept, acct in DEPT_ACCOUNT.items():
            amt = sum(r["amount"] for r in pay if r["month"] == m and r["department"] == dept)
            lines.append({"month": m, "account": acct, "amount": round(amt, 2)})
    return {"id": p.plan_id, "name": f"FY{p.fy_start.year} operating plan", "fiscal_year": p.fy_start.year,
            "approved_on": plan_time, "assumptions": {**drivers, "merit_raise": RAISE_PLAN,
                                                      "raise_month": RAISE_MONTH},
            "lines": lines, "drivers": driver_rows, "payroll": pay}
