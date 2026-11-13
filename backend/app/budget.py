"""Shared budgeting: a budget round collects each department's plan, Fred checks it, an admin approves and
publishes it as the operating plan (which budget-vs-actual then measures against).

Departments own two things: non-payroll spend lines (prefilled at today's run-rate) and new hires. Current
staff carry over automatically at their salaries (leavers drop off on their end date), so nobody retypes the
roster. Revenue is planned centrally from drivers (new customers, their price, churn, expansion), prefilled
from the trailing six months of billing.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from datetime import date

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection

from .connectors.mapping import DEPARTMENTS
from .connectors.pipeline import _add, _id, last_closed_month, rebuild_plan_payroll

DEFAULT_DEPT = {"sm": "Marketing", "rnd": "Engineering", "ga": "G&A", "cogs": "Engineering"}


def months_of(fy: int) -> list[date]:
    return [date(fy, m, 1) for m in range(1, 13)]


def even(annual: float) -> list[float]:
    return [round(annual / 12, 2)] * 12


# ------------------------------------------------------------------ context from the books
def context(conn: Connection, tid: str) -> dict:
    last = last_closed_month()
    have = conn.execute(text("SELECT max(month) FROM gl_actual WHERE tenant_id = :t"), {"t": tid}).scalar()
    last = min(last, have) if have else last
    first12, first3 = _add(last, -11), _add(last, -2)
    accounts = [dict(r) for r in conn.execute(text("""
        SELECT g.code, g.name, g.pl_group, g.kind, g.department,
               coalesce(sum(a.amount) FILTER (WHERE a.month >= :f12), 0)::float AS last12,
               coalesce(sum(a.amount) FILTER (WHERE a.month >= :f3), 0)::float AS last3
        FROM gl_account g LEFT JOIN gl_actual a ON a.tenant_id = g.tenant_id AND a.account = g.code AND a.month <= :l
        WHERE g.tenant_id = :t GROUP BY 1, 2, 3, 4, 5 ORDER BY g.code"""),
        {"t": tid, "f12": first12, "f3": first3, "l": last}).mappings()]
    staff = [dict(r) for r in conn.execute(text("""
        SELECT id::text, name, title, department, start_date, end_date, salary_annual::float FROM employee
        WHERE tenant_id = :t AND (end_date IS NULL OR end_date > :l) ORDER BY department, name"""),
        {"t": tid, "l": last}).mappings()]
    lf = conn.execute(text("""SELECT avg(p.amount * 12 / NULLIF(e.salary_annual, 0)) FROM payroll p
                              JOIN employee e ON e.id = p.employee_id WHERE p.tenant_id = :t AND p.month > :f"""),
                      {"t": tid, "f": first12}).scalar()
    cash = conn.execute(text("SELECT cash::float FROM cash_balance WHERE tenant_id = :t ORDER BY month DESC LIMIT 1"),
                        {"t": tid}).scalar()
    return {"last_closed": last, "accounts": accounts, "staff": staff, "loaded_factor": float(lf or 1.2), "cash": cash}


def baseline(ctx: dict) -> dict:
    by = defaultdict(float)
    for a in ctx["accounts"]:
        by[a["pl_group"]] += a["last12"]
    payroll_by_dept = defaultdict(float)
    for a in ctx["accounts"]:
        if a["kind"] == "payroll" and a["department"]:
            payroll_by_dept[a["department"]] += a["last12"]
    return {"groups": dict(by), "payroll_by_dept": dict(payroll_by_dept), "last_closed": str(ctx["last_closed"])}


def revenue_defaults(ws) -> dict:
    """Trailing-six-month drivers from billing."""
    from .analysis.forecast import decompose

    mrr = ws.mrr_frame()
    if mrr.empty:
        return {"start_mrr": 0.0, "new_customers": 0.0, "arpa_new": 0.0, "revenue_churn": 0.0, "net_expansion": 0.0}
    d = decompose(mrr)
    tail = d.iloc[1:].tail(6)
    first_month = mrr.sort_values("month").groupby("account_id").first()
    recent_new = first_month[first_month["month"] >= first_month["month"].max() - pd.DateOffset(months=6)]
    arpa = float(recent_new["mrr"].mean()) if len(recent_new) else float(mrr.groupby("month")["mrr"].sum().iloc[-1] / max(1, mrr["account_id"].nunique()))
    new_mrr = float(tail["new"].mean())
    return {"start_mrr": float(d["mrr"].iloc[-1]), "start_month": str(d["month"].iloc[-1])[:10],
            "new_customers": round(new_mrr / arpa, 1) if arpa else 0.0, "arpa_new": round(arpa, 2),
            "revenue_churn": round(float((tail["churned"] / tail["prior"]).mean()), 4),
            "net_expansion": round(float((tail["net_expansion"] / tail["prior"]).mean()), 4)}


def revenue_path(rev: dict, fy: int) -> list[float]:
    """Monthly MRR (= revenue) for the plan year, rolling forward from the latest known month."""
    m = date.fromisoformat(rev.get("start_month") or f"{fy - 1}-12-01")
    mrr = float(rev["start_mrr"])
    out = {}
    while m < date(fy + 1, 1, 1):
        m = _add(m, 1)
        mrr = mrr * (1 - rev["revenue_churn"] + rev["net_expansion"]) + rev["new_customers"] * rev["arpa_new"]
        out[m] = mrr
    return [round(out.get(x, 0.0), 2) for x in months_of(fy)]


def staff_cost(staff: list[dict], dept: str, fy: int, lf: float) -> list[float]:
    out = [0.0] * 12
    for e in staff:
        if e["department"] != dept:
            continue
        for i, m in enumerate(months_of(fy)):
            if e["start_date"] <= _add(m, 1) and (e["end_date"] is None or e["end_date"] > m):
                out[i] += e["salary_annual"] * lf / 12
    return [round(v, 2) for v in out]


def hire_cost(hires: list[dict], fy: int, lf: float) -> list[float]:
    out = [0.0] * 12
    for h in hires:
        start = date.fromisoformat(str(h["planned_start"])[:10]).replace(day=1)
        for i, m in enumerate(months_of(fy)):
            if m >= start:
                out[i] += float(h["salary_annual"]) * lf / 12
    return [round(v, 2) for v in out]


def prefill(ctx: dict, assignments: dict[str, str]) -> dict[str, list[dict]]:
    """Each department's non-payroll lines at today's run-rate (last 3 closed months x 4)."""
    lines: dict[str, list[dict]] = {d: [] for d in DEPARTMENTS}
    for a in ctx["accounts"]:
        if a["kind"] in ("payroll", "revenue"):
            continue
        dept = assignments.get(a["code"])
        if dept in lines:
            lines[dept].append({"account": a["code"], "monthly": even(round(a["last3"] * 4, -2))})
    return lines


# ------------------------------------------------------------------ the assembled plan
def assemble(rnd: dict, subs: list[dict], ctx: dict) -> dict:
    fy, lf = rnd["fiscal_year"], ctx["loaded_factor"]
    acc = {a["code"]: a for a in ctx["accounts"]}
    lines: dict[str, list[float]] = defaultdict(lambda: [0.0] * 12)
    by_group = defaultdict(float)
    depts = {}
    for s in subs:
        spend = 0.0
        for ln in s["lines"]:
            a = acc.get(ln["account"])
            if not a:
                continue
            lines[ln["account"]] = [x + y for x, y in zip(lines[ln["account"]], ln["monthly"])]
            spend += sum(ln["monthly"])
            by_group[a["pl_group"]] += sum(ln["monthly"])
        staff = staff_cost(ctx["staff"], s["department"], fy, lf)
        new = hire_cost(s["hires"], fy, lf)
        payroll = [x + y for x, y in zip(staff, new)]
        group = {"Sales": "sm", "Marketing": "sm", "Customer Success": "sm", "Engineering": "rnd", "Product": "rnd"}.get(
            s["department"], "ga")
        by_group[group] += sum(payroll)
        pay_acct = next((a["code"] for a in ctx["accounts"] if a["kind"] == "payroll" and a["department"] == s["department"]), None)
        if pay_acct:
            lines[pay_acct] = [x + y for x, y in zip(lines[pay_acct], payroll)]
        heads_now = sum(1 for e in ctx["staff"] if e["department"] == s["department"]
                        and (e["end_date"] is None or e["end_date"] > date(fy, 12, 31)))
        depts[s["department"]] = {"spend": round(spend, 2), "payroll": round(sum(payroll), 2), "staff_cost": round(sum(staff), 2),
                                  "hires": len(s["hires"]), "headcount_end": heads_now + len(s["hires"]),
                                  "total": round(spend + sum(payroll), 2), "payroll_account": pay_acct}
    revenue = revenue_path(rnd["revenue"], fy)
    rev_acct = max((a for a in ctx["accounts"] if a["kind"] == "revenue"), key=lambda a: a["last12"], default=None)
    if rev_acct:
        lines[rev_acct["code"]] = revenue
    total_rev = sum(revenue)
    opex = by_group["sm"] + by_group["rnd"] + by_group["ga"]
    gross = total_rev - by_group["cogs"]
    oi = gross - opex
    monthly_oi = []
    for i in range(12):
        cost = sum(v[i] for k, v in lines.items() if acc.get(k, {}).get("kind") != "revenue")
        monthly_oi.append(revenue[i] - cost)
    cash_end = (ctx["cash"] or 0.0) + oi if ctx["cash"] is not None else None
    return {"lines": {k: v for k, v in lines.items()}, "departments": depts, "revenue": revenue,
            "summary": {"revenue": round(total_rev, 2), "cogs": round(by_group["cogs"], 2), "sm": round(by_group["sm"], 2),
                        "rnd": round(by_group["rnd"], 2), "ga": round(by_group["ga"], 2), "gross_margin": gross / total_rev if total_rev else None,
                        "operating_income": round(oi, 2), "cash_start": ctx["cash"], "cash_end": round(cash_end, 2) if cash_end is not None else None,
                        "min_monthly_oi": round(min(monthly_oi), 2), "headcount_end": sum(d["headcount_end"] for d in depts.values()),
                        "revenue_account": rev_acct["code"] if rev_acct else None}}


def checks(rnd: dict, plan: dict, ctx: dict, subs: list[dict]) -> list[dict]:
    """Fred's review, the questions a finance lead would ask before approving."""
    out = []
    base = rnd["baseline"]
    rev_last = base["groups"].get("revenue", 0.0)
    rev_growth = plan["summary"]["revenue"] / rev_last - 1 if rev_last else None
    for s in subs:
        d = plan["departments"].get(s["department"], {})
        last = base["payroll_by_dept"].get(s["department"], 0.0) + sum(
            a["last12"] for a in ctx["accounts"] if a["kind"] not in ("payroll", "revenue")
            and rnd["assignments"].get(a["code"]) == s["department"])
        if last and rev_growth is not None:
            g = d.get("total", 0.0) / last - 1
            if g > rev_growth + 0.25 and d.get("total", 0) - last > 50_000:
                out.append({"department": s["department"], "level": "watch",
                            "text": f"Spend grows {g:.0%} while revenue is planned to grow {rev_growth:.0%}."})
        salaries = [e["salary_annual"] for e in ctx["staff"] if e["department"] == s["department"]]
        med = statistics.median(salaries) if salaries else None
        for h in s["hires"]:
            sal = float(h["salary_annual"])
            if med and not (0.6 * med <= sal <= 1.8 * med):
                out.append({"department": s["department"], "level": "watch",
                            "text": f"{h['role']} at {sal:,.0f} is far from the team's median salary ({med:,.0f})."})
            start = date.fromisoformat(str(h["planned_start"])[:10])
            if start < date.today().replace(day=1) and start.year == rnd["fiscal_year"]:
                out.append({"department": s["department"], "level": "info",
                            "text": f"{h['role']} is planned to start in {start:%B}, which has already passed."})
    smy = plan["summary"]
    if smy["cash_end"] is not None and smy["min_monthly_oi"] < 0:
        burn = -smy["min_monthly_oi"]
        runway = smy["cash_end"] / burn if burn else None
        if smy["cash_end"] < 0:
            out.append({"department": None, "level": "bad",
                        "text": f"The plan runs out of cash during the year (ends at {smy['cash_end']:,.0f})."})
        elif runway is not None and runway < 12:
            out.append({"department": None, "level": "watch",
                        "text": f"At the plan's peak burn, cash at year end covers about {runway:.0f} months."})
    return out


# ------------------------------------------------------------------ publishing
def publish(conn: Connection, tid: str, rnd: dict, plan: dict, subs: list[dict]) -> str:
    fy = rnd["fiscal_year"]
    pid = f"plan_fy{fy}_{tid.replace('-', '')[:12]}"
    conn.execute(text("""INSERT INTO plan (id, tenant_id, name, fiscal_year, approved_on, assumptions, status)
                         VALUES (:id, :t, :n, :fy, :ap, CAST(:as AS JSONB), 'approved')
                         ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, approved_on = EXCLUDED.approved_on,
                           assumptions = EXCLUDED.assumptions, status = 'approved'"""),
                 {"id": pid, "t": tid, "n": rnd["name"], "fy": fy, "ap": date.today(),
                  "as": json.dumps({"revenue": rnd["revenue"], "round_id": str(rnd["id"])})})
    conn.execute(text("DELETE FROM plan_line WHERE plan_id = :p"), {"p": pid})
    rows = [{"t": tid, "p": pid, "m": m, "a": code, "v": round(v, 2)}
            for code, monthly in plan["lines"].items() for m, v in zip(months_of(fy), monthly) if v]
    if rows:
        conn.execute(text("INSERT INTO plan_line (tenant_id, plan_id, month, account, amount) VALUES (:t, :p, :m, :a, :v)"), rows)
    r = rnd["revenue"]
    conn.execute(text("DELETE FROM plan_driver WHERE plan_id = :p"), {"p": pid})
    conn.execute(text("""INSERT INTO plan_driver (tenant_id, plan_id, month, new_customers, arpa_new, revenue_churn, net_expansion)
                         VALUES (:t, :p, :m, :n, :a, :c, :e)"""),
                 [{"t": tid, "p": pid, "m": m, "n": r["new_customers"], "a": r["arpa_new"], "c": r["revenue_churn"],
                   "e": r["net_expansion"]} for m in months_of(fy)])
    conn.execute(text("DELETE FROM plan_payroll WHERE plan_id = :p"), {"p": pid})
    conn.execute(text("DELETE FROM plan_hire WHERE plan_id = :p"), {"p": pid})
    for s in subs:
        for i, h in enumerate(s["hires"]):
            conn.execute(text("""INSERT INTO plan_hire (id, tenant_id, plan_id, role, department, location, planned_start,
                                                        salary_annual, status)
                                 VALUES (:id, :t, :p, :role, :dept, :loc, :start, :sal, 'planned')"""),
                         {"id": _id(tid, f"hire:{pid}:{s['department']}:{i}:{h['role']}"), "t": tid, "p": pid,
                          "role": h["role"], "dept": s["department"], "loc": h.get("location") or "US",
                          "start": date.fromisoformat(str(h["planned_start"])[:10]).replace(day=1),
                          "sal": float(h["salary_annual"])})
    rebuild_plan_payroll(conn, tid)
    # payroll lines in the P&L view = the person-level payroll plan, so both views of the plan agree exactly
    for dept, d in plan["departments"].items():
        if not d.get("payroll_account"):
            continue
        conn.execute(text("DELETE FROM plan_line WHERE plan_id = :p AND account = :a"), {"p": pid, "a": d["payroll_account"]})
        conn.execute(text("""INSERT INTO plan_line (tenant_id, plan_id, month, account, amount)
                             SELECT :t, :p, month, :a, round(sum(amount), 2) FROM plan_payroll
                             WHERE plan_id = :p AND department = :d GROUP BY month"""),
                     {"t": tid, "p": pid, "a": d["payroll_account"], "d": dept})
    return pid
