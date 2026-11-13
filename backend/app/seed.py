"""Deterministic demo workspace with real, learnable structure.

The generator plants the relationships a real B2B SaaS has, so the analyst team has something true
to discover (and tests can check that it does):
  * leads convert with odds driven by intent (demo, pricing views), ICP fit and channel quality
  * each paid channel produces leads with diminishing returns: leads = k * (spend / 1000) ^ b
  * account health follows a random walk; low health shows up in product usage weeks before churn
  * LinkedIn spend spikes in the last 10 days with no lead lift (an anomaly to catch)

    python -m app.seed            # seed if the demo tenant is missing
    python -m app.seed --reset    # drop and reseed
"""
from __future__ import annotations

import argparse
import calendar
import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np

from .seed_finance import (
    ACCOUNTS,
    CHANNEL_ACCOUNT,
    DEPT_ACCOUNT,
    People,
    build_people,
    build_plan,
    cogs_actual,
    nonpayroll_actual,
)
from .seed_text import QUALITY_HEALTH, QUALITY_MRR, TextGen

MONTHS = 24
GM = 0.78
SEGMENT_MRR = {"SMB": 230, "Mid-market": 680, "Enterprise": 2500}
SM_SHARE = {"paid_search": 0.30, "linkedin": 0.22, "events": 0.13, "content": 0.10, "sales_team": 0.25}
LEAD_RESPONSE = {"paid_search": (12.0, 0.72), "linkedin": (8.5, 0.68), "events": (9.0, 0.52), "content": (14.0, 0.78)}
CHANNEL_QUALITY = {"paid_search": 0.0, "linkedin": -0.25, "events": 0.3, "content": -0.35, "organic": 0.15, "referral": 1.0}
INDUSTRIES = {"fintech": 0.55, "saas": 0.45, "ecommerce": 0.1, "healthcare": -0.1, "media": -0.2,
              "manufacturing": -0.45, "education": -0.55}
SENIORITY = {"ic": -0.35, "manager": 0.0, "director": 0.35, "exec": 0.55}
COUNTRIES = ["US", "IN", "UK", "DE", "SG", "AU"]
FIRST = ["Aarav", "Maya", "Liam", "Priya", "Noah", "Zara", "Ethan", "Ishaan", "Sofia", "Kabir", "Emma", "Rohan",
         "Ava", "Arjun", "Mia", "Leo", "Anaya", "Lucas", "Diya", "Oliver"]
LAST = ["Shah", "Kim", "Patel", "Müller", "Garcia", "Iyer", "Smith", "Rao", "Chen", "Nair", "Brown", "Khan"]
WORDS = ["Nimbus", "Quanta", "Ledger", "Orbit", "Vertex", "Harbor", "Pine", "Atlas", "Signal", "Cobalt", "Ember",
         "Lumen", "Delta", "Kite", "Summit", "Forge", "Tidal", "Northstar", "Ridge", "Prism"]
SUFFIX = ["Labs", "Pay", "Health", "Works", "Cloud", "Systems", "Learning", "Media", "Commerce", "AI"]


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z))


@dataclass
class Generated:
    months: list[date]
    end: datetime
    accounts: list[dict] = field(default_factory=list)
    mrr: list[dict] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)
    leads: list[dict] = field(default_factory=list)
    spend: list[dict] = field(default_factory=list)
    cash: list[dict] = field(default_factory=list)
    people: People | None = None
    payroll: list[dict] = field(default_factory=list)
    gl: list[dict] = field(default_factory=list)
    plan: dict | None = None
    feedback: list[dict] = field(default_factory=list)


def generate(seed: int = 7, today: date | None = None) -> Generated:
    rng = np.random.default_rng(seed)
    today = (today or date.today()).replace(day=1)
    start = add_months(today, -MONTHS)
    months = [add_months(start, i) for i in range(MONTHS)]
    end = datetime(today.year, today.month, 1, tzinfo=UTC) - timedelta(seconds=1)
    g = Generated(months=months, end=end)
    uid = lambda: str(uuid.UUID(int=int(rng.integers(0, 2**63))))

    tg = TextGen(seed + 2)  # text uses its own RNG stream
    active: dict[str, dict] = {}  # account id -> state
    pending: dict[int, list[dict]] = {}  # month index -> converted leads that become customers

    def company() -> str:
        return f"{WORDS[rng.integers(len(WORDS))]} {SUFFIX[rng.integers(len(SUFFIX))]} {rng.integers(10, 99)}"

    def segment_for(emp: int) -> str:
        return "SMB" if emp < 60 else ("Mid-market" if emp < 600 else "Enterprise")

    def open_account(month: date, channel: str, emp: int, industry: str, country: str, name: str,
                     niche: tuple[str, str] | None = None, description: str | None = None) -> str:
        aid = uid()
        seg = segment_for(emp)
        niche = niche or tg.niche(industry)
        mrr = round(float(SEGMENT_MRR[seg] * rng.lognormal(0, 0.3) * QUALITY_MRR[niche[1]]), 2)
        g.accounts.append({"id": aid, "name": name, "domain": name.lower().replace(" ", "") + ".example",
                           "segment": seg, "acquisition_channel": channel, "employees": emp, "industry": industry,
                           "country": country, "created_at": datetime(month.year, month.month, 1, tzinfo=UTC),
                           "niche": niche[0],
                           "description": description or tg.description(name, industry, niche[0], emp)})
        a, b = QUALITY_HEALTH[niche[1]]
        active[aid] = {"mrr": mrr, "h": float(np.clip(rng.beta(a, b), 0.05, 0.99)), "seg": seg, "q": niche[1]}
        return aid

    for _ in range(260):
        emp = int(np.clip(rng.lognormal(math.log(140), 1.0), 5, 20000))
        open_account(months[0], str(rng.choice(list(CHANNEL_QUALITY))), emp,
                     str(rng.choice(list(INDUSTRIES))), str(rng.choice(COUNTRIES)), company())

    # Finance uses its own RNG stream so the revenue, lead and usage data stay identical.
    frng = np.random.default_rng(seed + 1)
    people = build_people(months, frng)
    g.people = people
    cash = 8_000_000.0
    for i, m in enumerate(months):
        days = calendar.monthrange(m.year, m.month)[1]
        month_start = datetime(m.year, m.month, 1, tzinfo=UTC)

        # ---- spend: paid channels vary month to month so response curves are identifiable
        sm_month = 82_000 * (1.012 ** i)
        channel_spend = {}
        for ch, share in SM_SHARE.items():
            mult = 1.0 if ch == "sales_team" else rng.uniform(0.7, 1.3)
            channel_spend[ch] = sm_month * share * mult
        # People costs come from the payroll roster; "sales_team" carries all S&M people cost + travel.
        pay = people.payroll_by_dept(m)
        npay = nonpayroll_actual(i, m, people.fy_start, frng)
        channel_spend["sales_team"] = sum(pay.get(d, 0.0) for d in ("Sales", "Marketing", "Customer Success")) \
            + npay["6200"]
        opex_month = sum(pay.get(d, 0.0) for d in ("Engineering", "Product", "G&A")) \
            + npay["8100"] + npay["8200"] + npay["8300"]
        paid_month = {ch: 0.0 for ch in CHANNEL_ACCOUNT}
        for d in range(days):
            ts = month_start + timedelta(days=d)
            for ch, amt in channel_spend.items():
                noise = rng.uniform(0.9, 1.1)  # always drawn: keeps the random stream stable
                daily = amt / days * (1.0 if ch == "sales_team" else noise)
                if ch == "linkedin" and i == MONTHS - 1 and d >= days - 10:
                    daily *= 2.4  # planted anomaly: spend spike, no extra leads
                g.spend.append({"ts": ts, "c": "sm", "ch": ch, "amt": round(daily, 2)})
                if ch in paid_month:
                    paid_month[ch] += round(daily, 2)
            g.spend.append({"ts": ts, "c": "other_opex", "ch": "g_and_a_rnd", "amt": round(opex_month / days, 2)})

        # ---- leads
        n_by_channel = {ch: rng.poisson(k * (channel_spend[ch] / 1000) ** b) for ch, (k, b) in LEAD_RESPONSE.items()}
        n_by_channel["organic"] = rng.poisson(40 + 2 * i)
        n_by_channel["referral"] = rng.poisson(14 + 0.6 * i)
        for ch, n in n_by_channel.items():
            for _ in range(n):
                created = month_start + timedelta(seconds=float(rng.uniform(0, days * 86400)))
                emp = int(np.clip(rng.lognormal(math.log(120), 1.1), 3, 50000))
                industry = str(rng.choice(list(INDUSTRIES)))
                seniority = str(rng.choice(list(SENIORITY), p=[0.4, 0.32, 0.18, 0.10]))
                demo = bool(rng.random() < 0.17)
                pv = int(rng.poisson(1.1 + 1.6 * demo))
                docs = int(rng.poisson(1.8))
                email = int(min(5, rng.poisson(1.4)))
                webinar = bool(rng.random() < (0.3 if ch == "events" else 0.1))
                size_lift = 0.65 if 50 <= emp <= 1000 else (-0.6 if emp < 20 else 0.0)
                z = (-4.75 + 1.9 * demo + 0.3 * min(pv, 6) + 0.07 * min(docs, 8) + 0.2 * email + 0.35 * webinar
                     + size_lift + INDUSTRIES[industry] + SENIORITY[seniority] + CHANNEL_QUALITY[ch])
                will_convert = rng.random() < sigmoid(z)
                lag = timedelta(days=float(rng.uniform(12, 50)))
                lead_company = company()
                lead_niche = tg.niche(industry)
                lead = {"id": uid(), "created_at": created, "source_channel": ch, "company": lead_company,
                        "niche": lead_niche,
                        "description": tg.description(lead_company, industry, lead_niche[0], emp),
                        "contact_name": f"{FIRST[rng.integers(len(FIRST))]} {LAST[rng.integers(len(LAST))]}",
                        "industry": industry, "employees": emp, "country": str(rng.choice(COUNTRIES)),
                        "seniority": seniority, "pricing_views": pv, "docs_views": docs, "email_engagement": email,
                        "demo_requested": demo, "webinar": webinar, "status": "lost", "converted_at": None,
                        "account_id": None}
                if will_convert and created + lag <= end:
                    lead["status"], lead["converted_at"] = "converted", created + lag
                    ci = (lead["converted_at"].year - months[0].year) * 12 + lead["converted_at"].month - months[0].month
                    pending.setdefault(ci, []).append(lead)
                elif will_convert or created + timedelta(days=45) > end:
                    lead["status"] = "open"  # outcome not known yet: these are what the team scores
                g.leads.append(lead)

        # ---- existing accounts: health walk -> churn / expansion / contraction
        if i > 0:
            for aid, st in list(active.items()):
                st["h"] = float(np.clip(st["h"] + rng.normal(-0.006, 0.075), 0.0, 1.0))
                p_churn = 0.004 + 0.55 * max(0.0, 0.58 - st["h"])
                u = rng.random()
                if u < p_churn:
                    if tg.rng.random() < 0.75:  # exit survey response rate
                        theme, body = tg.exit_reason(st["seg"], st["q"], st["h"])
                        g.feedback.append({"id": str(uuid.UUID(int=int(tg.rng.integers(0, 2**63)))), "a": aid,
                                           "ts": datetime(m.year, m.month, 3, tzinfo=UTC), "kind": "exit_survey",
                                           "score": None, "body": body, "theme": theme})
                    del active[aid]
                    continue
                v = rng.random()
                if st["h"] > 0.72 and v < 0.10:
                    st["mrr"] = round(st["mrr"] * rng.uniform(1.12, 1.35), 2)
                elif st["h"] < 0.5 and v < 0.07:
                    st["mrr"] = round(st["mrr"] * rng.uniform(0.6, 0.85), 2)

        for lead in pending.pop(i, []):
            lead["account_id"] = open_account(m, lead["source_channel"], lead["employees"], lead["industry"],
                                              lead["country"], lead["company"], lead["niche"], lead["description"])

        # ---- billing + usage rows
        mrr_total = 0.0
        for aid, st in active.items():
            if tg.rng.random() < 0.05:  # NPS survey responses
                score, body = tg.nps(st["h"], st["seg"])
                g.feedback.append({"id": str(uuid.UUID(int=int(tg.rng.integers(0, 2**63)))), "a": aid,
                                   "ts": datetime(m.year, m.month, 15, tzinfo=UTC), "kind": "nps", "score": score,
                                   "body": body, "theme": None})
            g.mrr.append({"a": aid, "m": m, "v": st["mrr"]})
            mrr_total += st["mrr"]
            seats = max(3, round(st["mrr"] / 45))
            wk = m + timedelta(days=(7 - m.weekday()) % 7)  # first Monday of the month
            while wk.month == m.month:
                util = float(np.clip(st["h"] + rng.normal(0, 0.06), 0.02, 0.98))
                au = int(rng.binomial(seats, util))
                g.usage.append({"a": aid, "w": wk, "s": seats, "au": au, "e": int(rng.poisson(au * 30 + 1))})
                wk += timedelta(days=7)

        cogs = cogs_actual(mrr_total, m, people.fy_start)

        # ---- general ledger (monthly, by account) + payroll register; cash moves with the books
        gl = {"4000": round(mrr_total, 2), **cogs, **npay}
        gl |= {CHANNEL_ACCOUNT[ch]: round(v, 2) for ch, v in paid_month.items()}
        gl |= {DEPT_ACCOUNT[d]: round(v, 2) for d, v in pay.items()}
        cash -= sum(v for a, v in gl.items() if a != "4000") - gl["4000"]
        g.cash.append({"m": m, "c": round(cash, 2)})
        g.gl += [{"m": m, "acct": a, "amt": v} for a, v in gl.items()]
        g.payroll += [{"m": m, "e": e["id"], "amt": c} for e in people.employees
                      if (c := people.monthly_cost(e, m))]

    # ---- the annual plan, built from history the way a finance lead would
    by_m: dict[date, dict[str, float]] = {}
    for r in g.gl:
        by_m.setdefault(r["m"], {})[r["acct"]] = r["amt"]
    first: dict[str, tuple[date, float]] = {}
    for r in g.mrr:
        if r["a"] not in first or r["m"] < first[r["a"]][0]:
            first[r["a"]] = (r["m"], r["v"])
    new_by_month = {}
    for m in months:
        vals = [v for (fm, v) in first.values() if fm == m]
        new_by_month[m] = (len(vals), sum(vals) / len(vals) if vals else 0.0)
    g.plan = build_plan(people, months, {m: by_m[m]["4000"] for m in months}, new_by_month,
                        {m: {ch: by_m[m][a] for ch, a in CHANNEL_ACCOUNT.items()} for m in months},
                        {m: by_m[m] for m in months})
    return g


TENANT_TABLES = ("finding_note", "task", "document", "feedback", "plan_payroll", "plan_hire", "plan_driver", "plan_line", "plan", "payroll", "employee",
                 "gl_actual", "gl_account", "model_run", "receipt", "snapshot", "usage_weekly", "lead", "cash_balance",
                 "spend", "subscription_mrr", "account")


def wipe_tenant_data(conn, tid: str) -> None:
    from sqlalchemy import text

    for tbl in TENANT_TABLES:
        conn.execute(text(f"DELETE FROM {tbl} WHERE tenant_id = :t"), {"t": tid})


def scope_ids(g: Generated, tid: str) -> Generated:
    """Give every generated row an id unique to this workspace (deterministic, collision-free across tenants)."""
    ns = uuid.UUID(tid)
    m = lambda old: str(uuid.uuid5(ns, str(old))) if old else old
    for a in g.accounts:
        a["id"] = m(a["id"])
    for r in g.mrr + g.usage:
        r["a"] = m(r["a"])
    for r in g.leads:
        r["id"], r["account_id"] = m(r["id"]), m(r["account_id"])
    for r in g.feedback:
        r["id"], r["a"] = m(r["id"]), m(r["a"])
    for e in g.people.employees:
        e["id"] = m(e["id"])
    for r in g.payroll:
        r["e"] = m(r["e"])
    for h in g.people.planned_hires:
        h["id"], h["employee_id"] = m(h["id"]), m(h["employee_id"])
    for r in g.plan["payroll"]:
        r["ref_id"] = m(r["ref_id"])
    g.plan["id"] = f"{g.plan['id']}_{tid.replace('-', '')[:12]}"
    return g


def seed_workspace(conn, tid: str, seed: int = 7) -> dict:
    """Load the demo company into an existing tenant (used by signup and the CLI)."""
    from sqlalchemy import text

    g = scope_ids(generate(seed=seed), tid)
    conn.execute(text("UPDATE tenant SET gross_margin = :gm, annual_wacc = 0.14, data_source = 'demo' WHERE id = :t"),
                 {"gm": GM, "t": tid})
    conn.execute(text("""INSERT INTO account (id, tenant_id, name, domain, segment, acquisition_channel, employees,
                                              industry, country, created_at, description)
                         VALUES (:id, :t, :name, :domain, :segment, :acquisition_channel, :employees, :industry,
                                 :country, :created_at, :description)"""), [{**a, "t": tid} for a in g.accounts])
    conn.execute(text("INSERT INTO subscription_mrr (tenant_id, account_id, month, mrr) VALUES (:t, :a, :m, :v)"),
                 [{**r, "t": tid} for r in g.mrr])
    conn.execute(text("""INSERT INTO usage_weekly (tenant_id, account_id, week, seats, active_users, events)
                         VALUES (:t, :a, :w, :s, :au, :e)"""), [{**r, "t": tid} for r in g.usage])
    conn.execute(text("""INSERT INTO lead (id, tenant_id, created_at, source_channel, company, contact_name, industry,
                           employees, country, seniority, pricing_views, docs_views, email_engagement,
                           demo_requested, webinar, status, converted_at, account_id, description)
                         VALUES (:id, :t, :created_at, :source_channel, :company, :contact_name, :industry,
                           :employees, :country, :seniority, :pricing_views, :docs_views, :email_engagement,
                           :demo_requested, :webinar, :status, :converted_at, :account_id, :description)"""),
                 [{**r, "t": tid} for r in g.leads])
    conn.execute(text("INSERT INTO spend (ts, tenant_id, category, channel, amount) VALUES (:ts, :t, :c, :ch, :amt)"),
                 [{**r, "t": tid} for r in g.spend])
    conn.execute(text("INSERT INTO cash_balance (tenant_id, month, cash) VALUES (:t, :m, :c)"),
                 [{**r, "t": tid} for r in g.cash])
    conn.execute(text("""INSERT INTO feedback (id, tenant_id, account_id, created_at, kind, score, body)
                         VALUES (:id, :t, :a, :ts, :kind, :score, :body)"""), [{**r, "t": tid} for r in g.feedback])
    insert_finance(conn, tid, g)
    return {"accounts": len(g.accounts), "leads": len(g.leads), "feedback": len(g.feedback)}


def seed(reset: bool = False) -> None:
    """CLI: (re)build the shared demo workspace identified by DEFAULT_TENANT_SLUG."""
    from sqlalchemy import text

    from .config import get_settings
    from .db import engine

    slug = get_settings().default_tenant_slug
    with engine.begin() as conn:
        existing = conn.execute(text("SELECT id FROM tenant WHERE slug = :s"), {"s": slug}).scalar()
        if existing and not reset:
            print(f"tenant '{slug}' already seeded; use --reset to rebuild")
            return
        if existing:
            wipe_tenant_data(conn, existing)
            tid = str(existing)
        else:
            tid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"fred-demo-{slug}"))
            conn.execute(text("""INSERT INTO tenant (id, slug, name, currency, gross_margin, annual_wacc)
                                 VALUES (:id, :slug, 'Acme Analytics (demo)', 'USD', :gm, 0.14)"""),
                         {"id": tid, "slug": slug, "gm": GM})
        counts = seed_workspace(conn, tid)
        print(f"seeded '{slug}': {counts}")


def insert_finance(conn, tid: str, g: Generated) -> None:
    from sqlalchemy import text

    p, plan = g.people, g.plan
    conn.execute(text("""INSERT INTO gl_account (tenant_id, code, name, pl_group, kind, department)
                         VALUES (:t, :c, :n, :g, :k, :d)"""),
                 [{"t": tid, "c": c, "n": n, "g": grp, "k": k, "d": d} for c, n, grp, k, d, _ in ACCOUNTS])
    conn.execute(text("INSERT INTO gl_actual (tenant_id, month, account, amount) VALUES (:t, :m, :acct, :amt)"),
                 [{**r, "t": tid} for r in g.gl])
    conn.execute(text("""INSERT INTO employee (id, tenant_id, name, title, department, location, start_date, end_date,
                                               salary_annual)
                         VALUES (:id, :t, :name, :title, :department, :location, :start_date, :end_date,
                                 :salary_annual)"""), [{**e, "t": tid} for e in p.employees])
    conn.execute(text("INSERT INTO payroll (tenant_id, month, employee_id, amount) VALUES (:t, :m, :e, :amt)"),
                 [{**r, "t": tid} for r in g.payroll])
    conn.execute(text("""INSERT INTO plan (id, tenant_id, name, fiscal_year, approved_on, assumptions)
                         VALUES (:id, :t, :name, :fy, :ap, CAST(:a AS JSONB))"""),
                 {"id": plan["id"], "t": tid, "name": plan["name"], "fy": plan["fiscal_year"],
                  "ap": plan["approved_on"], "a": json.dumps(plan["assumptions"])})
    conn.execute(text("""INSERT INTO plan_line (tenant_id, plan_id, month, account, amount)
                         VALUES (:t, :p, :month, :account, :amount)"""),
                 [{**r, "t": tid, "p": plan["id"]} for r in plan["lines"]])
    conn.execute(text("""INSERT INTO plan_driver (tenant_id, plan_id, month, new_customers, arpa_new, revenue_churn,
                                                  net_expansion)
                         VALUES (:t, :p, :month, :new_customers, :arpa_new, :revenue_churn, :net_expansion)"""),
                 [{**r, "t": tid, "p": plan["id"]} for r in plan["drivers"]])
    conn.execute(text("""INSERT INTO plan_hire (id, tenant_id, plan_id, role, department, location, planned_start,
                                                salary_annual, status, employee_id)
                         VALUES (:id, :t, :p, :role, :department, :location, :planned_start, :salary_annual,
                                 :status, :employee_id)"""),
                 [{**h, "t": tid, "p": plan["id"]} for h in p.planned_hires])
    conn.execute(text("""INSERT INTO plan_payroll (tenant_id, plan_id, month, ref_type, ref_id, department, amount)
                         VALUES (:t, :p, :month, :ref_type, :ref_id, :department, :amount)"""),
                 [{**r, "t": tid, "p": plan["id"]} for r in plan["payroll"]])


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--reset", action="store_true")
    seed(reset=p.parse_args().reset)
