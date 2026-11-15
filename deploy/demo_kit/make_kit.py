"""Build the Lumen Desk demo kit: ten small CSVs that tie out with each other, with stories planted for Fred to find.

    cd backend && uv run python ../deploy/demo_kit/make_kit.py      # writes ../demo-kit/*.csv

Lumen Desk is fictional (a helpdesk SaaS, ~180 customers, ~$2M ARR). Everything is consistent:
  revenue in the books = billing MRR; payroll in the books = the roster; ad spend in the books = daily ad spend;
  customers = won leads; the budget follows the hiring plan.
Planted for the demo (what each role should find):
  - Revenue behind plan (planned 10 new customers a month; churn from June): customers who need a Jira/Salesforce integration churn from June 2026
    (exit surveys say so; their usage fades for weeks before they leave)
  - Hosting cost per revenue dollar steps up from July 2026 (run-rate, not one-off)
  - Software subscriptions step up in August 2026; legal bill in May 2026 is a one-off
  - Account Executive started 3 months late; Marketing Manager never hired; an engineer left in July
  - LinkedIn spend doubles in the last 12 days of October with no lift in leads
  - Leads that request a demo, come by referral, or are execs at fintechs convert far more often
"""
from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta
from pathlib import Path

R = random.Random(42)
OUT = Path(__file__).resolve().parents[2] / "demo-kit"
FIRST_MONTH, LAST_MONTH = date(2025, 5, 1), date(2026, 10, 1)  # 18 closed months
FY = 2026


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


MONTHS = []
m = FIRST_MONTH
while m <= LAST_MONTH:
    MONTHS.append(m)
    m = add_months(m, 1)

INDUSTRY = {
    "fintech": ("Payments and lending platform for small businesses", "Card issuing API for neobanks",
                "Expense management software for finance teams", "Invoice factoring marketplace"),
    "saas": ("Project management tool for remote teams", "Developer API for sending transactional email",
             "HR software for hourly workers", "Analytics dashboard for product teams"),
    "ecommerce": ("Online store selling sustainable homeware", "Direct-to-consumer skincare brand",
                  "Marketplace for refurbished electronics", "Subscription box for specialty coffee"),
    "healthcare": ("Clinic scheduling and patient reminders", "Telehealth platform for physiotherapy",
                   "Pharmacy inventory software"),
    "education": ("Online coding bootcamp", "Tutoring marketplace for high-school students",
                  "Learning management system for corporate training"),
}
IND_WEIGHT = {"fintech": 0.25, "saas": 0.3, "ecommerce": 0.2, "healthcare": 0.12, "education": 0.13}
IND_LIFT = {"fintech": 0.9, "saas": 0.4, "ecommerce": -0.2, "healthcare": -0.1, "education": -0.4}
WORDS = ["Nimbus", "Quanta", "Ledger", "Orbit", "Vertex", "Harbor", "Pine", "Atlas", "Signal", "Cobalt", "Ember",
         "Juniper", "Kestrel", "Lumen", "Meridian", "Northwind", "Onyx", "Pioneer", "Quill", "Ridge", "Sable", "Tidal",
         "Umbra", "Vela", "Willow", "Zephyr", "Birch", "Cedar", "Delta", "Fable"]
SUFFIX = ["Labs", "Pay", "Health", "Works", "Cloud", "Systems", "Learning", "Commerce", "AI", "Studio"]
FIRST = ["Aarav", "Maya", "Liam", "Priya", "Noah", "Zara", "Ethan", "Ishaan", "Sofia", "Kabir", "Emma", "Rohan",
         "Ana", "Bo", "Chloe", "Dev", "Elena", "Farah", "Gabe", "Hana"]
LAST = ["Shah", "Kim", "Patel", "Garcia", "Iyer", "Smith", "Rao", "Chen", "Nair", "Brown", "Khan", "Lopez", "Ng"]
TITLES = {"exec": ["VP Operations", "Head of Support", "COO", "Founder"], "director": ["Director of Support",
          "Director of Operations"], "manager": ["Support Manager", "CX Manager"], "ic": ["Support Agent", "Analyst"]}
SEN_LIFT = {"exec": 0.7, "director": 0.4, "manager": 0.0, "ic": -0.6}
SRC_LIFT = {"referral": 1.2, "organic": 0.3, "paid_search": 0.0, "content": -0.2, "linkedin": -0.5}
used_names: set[str] = set()


def company() -> tuple[str, str]:
    """Unique names: 'Nimbus Labs' while they last, then 'Nimbus Ridge Labs' (thousands of combinations)."""
    for attempt in range(200):
        name = (f"{R.choice(WORDS)} {R.choice(SUFFIX)}" if attempt < 5
                else f"{R.choice(WORDS)} {R.choice(WORDS)} {R.choice(SUFFIX)}")
        if name not in used_names:
            used_names.add(name)
            return name, name.lower().replace(" ", "") + ".com"
    raise RuntimeError("ran out of company names")


def pick(weights: dict[str, float]) -> str:
    x, acc = R.random() * sum(weights.values()), 0.0
    for k, w in weights.items():
        acc += w
        if x <= acc:
            return k
    return k


def segment_mrr() -> tuple[str, float, int]:
    seg = pick({"SMB": 0.6, "Mid": 0.3, "Ent": 0.1})
    base = {"SMB": (400, 700, 8), "Mid": (1200, 2000, 25), "Ent": (3800, 6500, 80)}[seg]
    return seg, round(R.uniform(base[0], base[1]) / 10) * 10, base[2]


# ------------------------------------------------------------------ ad spend (daily) and leads (driven by it)
spend_rows, day = [], FIRST_MONTH
while day < add_months(LAST_MONTH, 1):
    for ch, base in (("paid_search", 300.0), ("linkedin", 200.0)):
        amt = base * R.uniform(0.8, 1.2) * (1 + 0.02 * MONTHS.index(day.replace(day=1)) / 3)
        if ch == "linkedin" and day >= date(2026, 10, 20):
            amt *= 2.3  # planted: LinkedIn spend doubles, leads don't follow
        spend_rows.append((day, ch, round(amt, 2)))
    day += timedelta(days=1)
spend_by_day = {(d, c): a for d, c, a in spend_rows}

customers: list[dict] = []
leads: list[dict] = []


def new_customer(start: date, lead: dict | None) -> dict:
    name, domain = (lead["company"], lead["domain"]) if lead else company()
    ind = lead["industry"] if lead else pick(IND_WEIGHT)
    seg, mrr, seats = segment_mrr()
    c = {"id": f"LD-{len(customers) + 1:04d}", "name": name, "domain": domain, "industry": ind, "segment": seg,
         "mrr": mrr, "seats": seats, "start": start, "end": None,
         "needs_integration": R.random() < (0.38 if ind in ("saas", "fintech") else 0.22)}
    customers.append(c)
    return c


for _ in range(95):  # customers before the window
    new_customer(add_months(FIRST_MONTH, -R.randint(1, 30)), None)

day = add_months(FIRST_MONTH, -1)
while day < add_months(LAST_MONTH, 1):
    for ch in ("paid_search", "linkedin", "organic", "referral", "content"):
        if ch in ("paid_search", "linkedin"):
            s = spend_by_day.get((day, ch), 0.0) if day >= FIRST_MONTH else (300 if ch == "paid_search" else 200)
            base = {"paid_search": 0.3, "linkedin": 0.18}[ch] * (s / (300 if ch == "paid_search" else 200)) ** 0.7
            if ch == "linkedin" and day >= date(2026, 10, 20):
                base = 0.18 * 1.05  # the spike buys almost nothing
        else:
            base = {"organic": 0.22, "referral": 0.08, "content": 0.12}[ch]
        n = sum(1 for _ in range(4) if R.random() < base / 4)
        for _ in range(n):
            ind = pick(IND_WEIGHT)
            sen = pick({"exec": 0.15, "director": 0.2, "manager": 0.35, "ic": 0.3})
            demo = R.random() < 0.3
            views = max(0, int(R.gauss(2.2 if demo else 1.0, 1.5)))
            score = -2.4 + IND_LIFT[ind] + SEN_LIFT[sen] + SRC_LIFT[ch] + (1.6 if demo else 0) + 0.18 * views
            p = 1 / (1 + math.exp(-score))
            name, domain = company()
            lead = {"created_at": day + timedelta(days=R.randint(0, 0)), "company": name, "domain": domain,
                    "contact_name": f"{R.choice(FIRST)} {R.choice(LAST)}", "title": R.choice(TITLES[sen]),
                    "source": ch, "industry": ind, "employees": int(R.lognormvariate(4, 1)) + 5,
                    "country": pick({"US": 0.45, "IN": 0.25, "UK": 0.15, "DE": 0.08, "SG": 0.07}),
                    "pricing_views": views, "demo_requested": demo,
                    "description": R.choice(INDUSTRY[ind])}
            age = (date(2026, 11, 15) - day).days
            if R.random() < p:
                won = day + timedelta(days=R.randint(10, 40))
                if won < add_months(LAST_MONTH, 1):
                    lead.update(status="won", converted_at=won)
                    new_customer(won.replace(day=1) if won.day < 15 else add_months(won.replace(day=1), 1), lead)
                else:
                    lead.update(status="open", converted_at=None)
            else:
                lead.update(status="lost" if age > 60 else "open", converted_at=None)
            leads.append(lead)
    day += timedelta(days=1)
leads = [x for x in leads if x["created_at"] >= FIRST_MONTH]

# ------------------------------------------------------------------ churn and expansion -> billing
churn_feedback = []
for c in customers:
    m = max(c["start"], FIRST_MONTH)
    while m <= LAST_MONTH and c["end"] is None:
        story = c["needs_integration"] and m >= date(2026, 6, 1)
        p = 0.08 if story else 0.011
        if m > c["start"] and R.random() < p:
            c["end"] = m
            c["churn_reason"] = "integration" if story else pick({"price": 0.5, "support": 0.3, "closed": 0.2})
        m = add_months(m, 1)

billing = []
for c in customers:
    mrr = c["mrr"]
    for m in MONTHS:
        if m < c["start"] or (c["end"] and m >= c["end"]):
            continue
        if c["segment"] != "SMB" and R.random() < 0.04:
            mrr = round(mrr * 1.12, -1)  # expansion
        billing.append((c, m, mrr))
revenue = {m: sum(v for _, mm, v in billing if mm == m) for m in MONTHS}

# ------------------------------------------------------------------ people
DEPTS = ["Sales", "Marketing", "Customer Success", "Engineering", "Product", "G&A"]
roster = []
base_team = [("Head of Sales", "Sales", "US", 165000), ("Account Executive", "Sales", "US", 120000),
             ("Account Executive", "Sales", "US", 118000), ("SDR", "Sales", "IN", 32000),
             ("Growth Marketer", "Marketing", "US", 115000), ("Content Writer", "Marketing", "IN", 30000),
             ("Customer Success Manager", "Customer Success", "US", 98000),
             ("Support Lead", "Customer Success", "IN", 36000), ("Support Engineer", "Customer Success", "IN", 28000),
             ("CTO", "Engineering", "US", 210000), ("Senior Engineer", "Engineering", "US", 175000),
             ("Software Engineer", "Engineering", "US", 145000), ("Software Engineer", "Engineering", "IN", 48000),
             ("Software Engineer", "Engineering", "IN", 46000), ("Software Engineer", "Engineering", "IN", 45000),
             ("Product Manager", "Product", "US", 150000), ("Product Designer", "Product", "IN", 40000),
             ("CEO", "G&A", "US", 180000), ("Finance & Ops Manager", "G&A", "IN", 42000)]
for title, dept, loc, sal in base_team:
    roster.append({"name": f"{R.choice(FIRST)} {R.choice(LAST)}", "title": title, "department": dept, "location": loc,
                   "start_date": date(2024, R.randint(1, 12), R.randint(1, 28)), "end_date": None,
                   "salary_annual": sal, "loaded_factor": 1.2})
leaver = next(p for p in roster if p["title"] == "Software Engineer" and p["location"] == "US")
leaver["end_date"] = date(2026, 7, 15)  # planted: departure
hiring_plan = [("Senior Engineer", "Engineering", "US", date(2026, 2, 1), 170000),
               ("Account Executive", "Sales", "US", date(2026, 3, 1), 120000),
               ("Customer Success Manager", "Customer Success", "IN", date(2026, 4, 1), 34000),
               ("Marketing Manager", "Marketing", "US", date(2026, 5, 1), 130000)]
actual_starts = {"Senior Engineer": date(2026, 2, 2), "Account Executive": date(2026, 6, 15),  # 3 months late
                 "Customer Success Manager": date(2026, 4, 6)}  # Marketing Manager never hired
for role, dept, loc, _, sal in hiring_plan:
    if role in actual_starts:
        roster.append({"name": f"{R.choice(FIRST)} {R.choice(LAST)}", "title": role, "department": dept, "location": loc,
                       "start_date": actual_starts[role], "end_date": None, "salary_annual": sal, "loaded_factor": 1.2})


def payroll_month(dept: str, m: date) -> float:
    nxt = add_months(m, 1)
    tot = 0.0
    for p in roster:
        if p["department"] == dept and p["start_date"] < nxt and (p["end_date"] is None or p["end_date"] > m):
            tot += p["salary_annual"] * p["loaded_factor"] / 12
    return round(tot, 2)


# ------------------------------------------------------------------ books (long format), cash
ACC = {"rev": "4000 Subscription revenue", "host": "5000 Hosting (AWS)", "fees": "5100 Payment processing fees",
       "ads_g": "6400 Advertising: Google Ads", "ads_l": "6410 Advertising: LinkedIn",
       "soft": "6500 Software subscriptions", "rent": "6600 Rent & office", "legal": "6700 Legal & accounting"}
PAY_ACC = {d: f"{6000 + 10 * i} Salaries: {d}" for i, d in enumerate(DEPTS)}
gl, cash_rows, cash = [], [], 4_200_000.0
for m in MONTHS:
    rev = revenue[m]
    lines = {ACC["rev"]: (rev, "Income"),
             ACC["host"]: (rev * (0.18 if m >= date(2026, 7, 1) else 0.14), "Cost of Goods Sold"),
             ACC["fees"]: (rev * 0.029, "Cost of Goods Sold"),
             ACC["ads_g"]: (sum(a for d, c, a in spend_rows if c == "paid_search" and d.replace(day=1) == m), "Expenses"),
             ACC["ads_l"]: (sum(a for d, c, a in spend_rows if c == "linkedin" and d.replace(day=1) == m), "Expenses"),
             ACC["soft"]: (12000.0 if m >= date(2026, 8, 1) else 7500.0, "Expenses"),
             ACC["rent"]: (9000.0, "Expenses"),
             ACC["legal"]: (24000.0 if m == date(2026, 5, 1) else 1500.0, "Expenses")}
    for d in DEPTS:
        lines[PAY_ACC[d]] = (payroll_month(d, m), "Expenses")
    for acct, (amt, section) in lines.items():
        gl.append((m, acct, round(amt, 2), section))
    cash += rev - sum(a for k, (a, s) in lines.items() if k != ACC["rev"])
    cash_rows.append((m, round(cash, 2)))

# ------------------------------------------------------------------ budget FY2026: plan MRR path, planned team
plan_rows = []
mrr_plan = revenue[date(2025, 12, 1)]
plan_time = date(2025, 12, 1)
on_board = [p for p in roster if p["start_date"] <= plan_time and (p["end_date"] is None or p["end_date"] > plan_time)]
for i in range(12):
    m = date(FY, i + 1, 1)
    mrr_plan = mrr_plan * (1 - 0.011 + 0.004) + 10 * 1050  # the plan: 10 new customers a month
    plan = {ACC["rev"]: mrr_plan, ACC["host"]: mrr_plan * 0.14, ACC["fees"]: mrr_plan * 0.029,
            ACC["ads_g"]: 9300.0, ACC["ads_l"]: 6200.0, ACC["soft"]: 7500.0, ACC["rent"]: 9000.0, ACC["legal"]: 1500.0}
    for d in DEPTS:
        tot = sum(p["salary_annual"] * 1.2 / 12 for p in on_board if p["department"] == d)
        tot += sum(sal * 1.2 / 12 for role, dd, _, start, sal in hiring_plan if dd == d and m >= start)
        plan[PAY_ACC[d]] = tot
    for acct, amt in plan.items():
        plan_rows.append((acct, m, round(amt, 2)))

# ------------------------------------------------------------------ usage (weekly) and feedback
usage = []
week = date(2026, 1, 5)
while week < date(2026, 11, 1):
    for c in customers:
        if week < c["start"] or (c["end"] and week >= c["end"]):
            continue
        util = 0.68 + R.uniform(-0.08, 0.08)
        if c["end"]:  # fades in the 10 weeks before leaving
            weeks_left = (c["end"] - week).days / 7
            if weeks_left < 10:
                util *= max(0.15, weeks_left / 10)
        if c["needs_integration"] and week >= date(2026, 4, 1):
            util *= 0.88
        active = max(0, round(c["seats"] * util))
        usage.append((c["id"], week, c["seats"], active, active * R.randint(30, 50)))
    week += timedelta(days=7)

EXIT = {"integration": ["We need it to sync with Jira and it doesn't. Moving to a tool that integrates.",
                        "No Salesforce integration, so our agents double-enter every ticket.",
                        "Missing integrations with our CRM was the dealbreaker.",
                        "Couldn't connect it to Jira; engineering ignored escalations."],
        "price": ["Too expensive for a team our size.", "Price went up and the value didn't.",
                  "Found a cheaper tool that does enough."],
        "support": ["Support took days to answer our questions.", "Onboarding was rough and nobody followed up."],
        "closed": ["We shut down the product line that used it."]}
NPS = {"promoter": ["Automations save our team hours every week.", "Best ticket routing we've used.",
                    "Setup took an afternoon. Love the reporting."],
       "passive": ["Good tool, reporting could be deeper.", "Works fine, a bit pricey for what we use."],
       "detractor": ["Without a Jira integration it's a dead end for us.", "Reports are too basic for management."]}
feedback = []
for c in customers:
    if c["end"] and c["end"] >= FIRST_MONTH:
        feedback.append((c["end"] + timedelta(days=R.randint(1, 9)), c["id"], "exit_survey", "",
                         R.choice(EXIT[c["churn_reason"]])))
active_now = [c for c in customers if not c["end"]]
for c in R.sample(active_now, min(45, len(active_now))):
    kind = pick({"promoter": 0.5, "passive": 0.3, "detractor": 0.2 if not c["needs_integration"] else 0.6})
    score = {"promoter": R.randint(9, 10), "passive": R.randint(7, 8), "detractor": R.randint(2, 6)}[kind]
    feedback.append((date(2026, R.randint(6, 10), R.randint(1, 28)), c["id"], "nps", score, R.choice(NPS[kind])))


# ------------------------------------------------------------------ write
def write(name: str, header: list[str], rows) -> None:
    with open(OUT / name, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


OUT.mkdir(exist_ok=True)
write("01_billing.csv", ["customer_id", "customer_name", "email", "month", "mrr"],
      [(c["id"], c["name"], f"billing@{c['domain']}", m.strftime("%Y-%m"), f"{v:.2f}") for c, m, v in billing])
write("02_accounting_pnl.csv", ["month", "account", "amount", "section"],
      [(m.strftime("%Y-%m"), a, f"{v:.2f}", s) for m, a, v, s in gl])
write("03_cash.csv", ["month", "cash"], [(m.strftime("%Y-%m"), f"{v:.2f}") for m, v in cash_rows])
write("04_team_roster.csv", ["name", "title", "department", "location", "start_date", "end_date", "salary_annual",
                             "loaded_factor"],
      [(p["name"], p["title"], p["department"], p["location"], p["start_date"], p["end_date"] or "",
        p["salary_annual"], p["loaded_factor"]) for p in roster])
write("05_budget_fy2026.csv", ["account", "month", "amount"], [(a, m.strftime("%Y-%m"), f"{v:.2f}") for a, m, v in plan_rows])
write("06_hiring_plan.csv", ["role", "department", "location", "planned_start", "salary_annual"],
      [(r, d, loc, s.strftime("%Y-%m"), sal) for r, d, loc, s, sal in hiring_plan])
write("07_ad_spend.csv", ["date", "channel", "amount"], [(d, c, f"{a:.2f}") for d, c, a in spend_rows])
write("08_crm_leads.csv", ["created_at", "company", "domain", "contact_name", "title", "source", "industry", "employees",
                           "country", "status", "converted_at", "pricing_views", "demo_requested", "description"],
      [(x["created_at"], x["company"], x["domain"], x["contact_name"], x["title"], x["source"], x["industry"],
        x["employees"], x["country"], x["status"], x["converted_at"] or "", x["pricing_views"],
        "yes" if x["demo_requested"] else "no", x["description"]) for x in sorted(leads, key=lambda x: x["created_at"])])
write("09_customer_feedback.csv", ["date", "customer_id", "kind", "score", "text"], sorted(feedback))
write("10_product_usage.csv", ["customer_id", "week", "seats", "active_users", "events"], usage)

won = sum(1 for x in leads if x["status"] == "won")
print(f"customers {len(customers)} (active {len(active_now)}), leads {len(leads)} (won {won}, "
      f"lost {sum(1 for x in leads if x['status'] == 'lost')}), billing rows {len(billing)}, usage rows {len(usage)}, "
      f"feedback {len(feedback)}")
for f in sorted(OUT.glob("*.csv")):
    print(f"  {f.name:28s} {f.stat().st_size / 1024:6.1f} KB")
