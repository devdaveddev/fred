"""Budget vs actual: the monthly job of a strategic finance / BizOps lead, done in code.

Every variance is decomposed into causes that sum exactly to the total:
  revenue  = new-logo volume + new-logo price + churn + expansion
  payroll  = late starts + unfilled roles + unplanned hires + departures + pay rates
  other    = classified as one-off, run-rate change, or scattered, by rule
No LLM is involved; explanations are templated from the computed components.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..analysis.forecast import decompose
from .data import FinanceData

GROUPS = [("revenue", "Revenue"), ("cogs", "Cost of revenue"), ("sm", "Sales & marketing"),
          ("rnd", "Research & development"), ("ga", "General & administrative")]
COST_GROUPS = {"cogs", "sm", "rnd", "ga"}


# Materiality by line type: revenue and payroll are large, so a smaller % gap still matters.
MATERIALITY = {"revenue": (0.02, 25_000), "payroll": (0.03, 15_000), "other": (0.05, 5_000)}
EARLY_WARNING_ANNUAL = 25_000  # a run-rate change worth this much a year is flagged even if YTD is small


def material(var: float, plan: float, ytd: bool = True, kind: str = "other") -> bool:
    pct, floor = MATERIALITY.get(kind, MATERIALITY["other"])
    if not ytd:
        pct, floor = pct * 2, floor * 0.6
    return abs(var) >= max(pct * abs(plan), floor)


def _money(v: float) -> str:
    s = "-" if v < 0 else "+"
    v = abs(v)
    return f"{s}${v / 1e6:.2f}M" if v >= 1e6 else (f"{s}${v / 1e3:.0f}K" if v >= 1e3 else f"{s}${v:,.0f}")


@dataclass
class Period:
    month: pd.Timestamp
    ytd: list[pd.Timestamp]


def period(fd: FinanceData, month: pd.Timestamp | None = None) -> Period:
    closed = fd.closed_months
    m = month if month is not None and month in closed else closed[-1]
    return Period(month=m, ytd=[x for x in closed if x <= m])


# ---------------------------------------------------------------- P&L table
def pnl(fd: FinanceData, per: Period) -> list[dict]:
    acc = fd.accounts.set_index("code")

    def tot(df: pd.DataFrame, months: list) -> pd.Series:
        return df[df["month"].isin(months)].groupby("account")["amount"].sum()

    am, pm = tot(fd.actual, [per.month]), tot(fd.plan_lines, [per.month])
    ay, py = tot(fd.actual, per.ytd), tot(fd.plan_lines, per.ytd)

    def row(key, label, level, group, a_m, p_m, a_y, p_y, kind="other") -> dict:
        cost = group in COST_GROUPS
        var_m, var_y = a_m - p_m, a_y - p_y
        return {"key": key, "label": label, "level": level, "group": group, "cost": cost,
                "plan_m": p_m, "actual_m": a_m, "var_m": var_m, "plan_ytd": p_y, "actual_ytd": a_y, "var_ytd": var_y,
                "var_pct_ytd": var_y / p_y if p_y else None,
                "favorable_ytd": (var_y <= 0) if cost else (var_y >= 0),
                "material_ytd": material(var_y, p_y, kind=kind), "material_m": material(var_m, p_m, False, kind)}

    rows, sums = [], {}
    for g, label in GROUPS:
        codes = acc[acc["pl_group"] == g].index
        s = [sum(x.get(c, 0.0) for c in codes) for x in (am, pm, ay, py)]
        sums[g] = s
        rows.append(row(g, label, "group", g, *s, kind="revenue" if g == "revenue" else "other"))
        for c in codes:
            k = acc.loc[c, "kind"]
            rows.append(row(c, f"{c} {acc.loc[c, 'name']}", "account", g, am.get(c, 0.0), pm.get(c, 0.0),
                            ay.get(c, 0.0), py.get(c, 0.0),
                            kind="revenue" if k == "revenue" else ("payroll" if k == "payroll" else "other")))
        if g == "cogs":
            gp = [sums["revenue"][k] - sums["cogs"][k] for k in range(4)]
            rows.append(row("gross_profit", "Gross profit", "total", "revenue", *gp))
    opex = [sum(sums[g][k] for g in ("sm", "rnd", "ga")) for k in range(4)]
    rows.append(row("opex", "Total operating expenses", "total", "sm", *opex))
    oi = [sums["revenue"][k] - sums["cogs"][k] - opex[k] for k in range(4)]
    rows.append(row("operating_income", "Operating income (burn)", "total", "revenue", *oi, kind="revenue"))
    return rows


# ---------------------------------------------------------------- revenue bridge
def revenue_bridge(fd: FinanceData, mrr: pd.DataFrame, per: Period) -> dict:
    """Exact: actual - plan revenue = volume + price + churn + expansion, for the month and YTD.

    Plan and actual start from the same MRR (the month before the plan year), so the gap in any
    month is the running sum of flow differences since then.
    """
    d = decompose(mrr).set_index("month")
    d.index = pd.to_datetime(d.index)
    drv = fd.drivers.set_index("month")
    months = [m for m in drv.index if m <= per.month]
    first = mrr[mrr["mrr"] > 0].groupby("account_id")["month"].min()
    n_new = first.value_counts()
    prev_mrr_plan = float(d.loc[fd.fy_start - pd.DateOffset(months=1), "mrr"])
    cum = {"volume": 0.0, "price": 0.0, "churn": 0.0, "expansion": 0.0}
    monthly, ytd = [], {k: 0.0 for k in cum}
    n_a_tot = n_p_tot = 0.0
    for m in months:
        r = drv.loc[m]
        n_a = float(n_new.get(m, 0))
        arpa_a = float(d.loc[m, "new"] / n_a) if n_a else 0.0
        churn_p = r["revenue_churn"] * prev_mrr_plan
        exp_p = r["net_expansion"] * prev_mrr_plan
        cum["volume"] += (n_a - r["new_customers"]) * r["arpa_new"]
        cum["price"] += n_a * (arpa_a - r["arpa_new"])
        cum["churn"] += -(float(d.loc[m, "churned"]) - churn_p)
        cum["expansion"] += float(d.loc[m, "net_expansion"]) - exp_p
        prev_mrr_plan = prev_mrr_plan * (1 - r["revenue_churn"] + r["net_expansion"]) + r["new_customers"] * r["arpa_new"]
        n_a_tot += n_a
        n_p_tot += r["new_customers"]
        if m in per.ytd:
            for k in ytd:
                ytd[k] += cum[k]
        monthly.append({"month": str(m.date()), **cum, "new_actual": n_a, "new_plan": float(r["new_customers"]),
                        "arpa_actual": arpa_a, "arpa_plan": float(r["arpa_new"])})
    tail = d.loc[[m for m in months if m in d.index]]
    return {
        "month": monthly[-1] if monthly else {},
        "ytd": ytd,
        "ytd_total": sum(ytd.values()),
        "new_customers": {"actual": n_a_tot, "plan": n_p_tot},
        "revenue_churn": {"actual": float((tail["churned"] / tail["prior"]).mean()),
                          "plan": float(drv.loc[months, "revenue_churn"].mean())},
        "net_expansion": {"actual": float((tail["net_expansion"] / tail["prior"]).mean()),
                          "plan": float(drv.loc[months, "net_expansion"].mean())},
        "series": monthly,
    }


# ---------------------------------------------------------------- payroll bridge
def payroll_bridge(fd: FinanceData, months: list[pd.Timestamp]) -> dict:
    pp_all = fd.plan_payroll
    pp = pp_all[pp_all["month"].isin(months)]
    ap = fd.payroll[fd.payroll["month"].isin(months)]
    emp = fd.employees.set_index("employee_id")
    a_by = ap.groupby(["employee_id", "month"])["amount"].sum()
    items: list[dict] = []

    def series(df: pd.DataFrame, key: str, val: str) -> pd.Series:
        s = df[df[key] == val].set_index("month")["amount"]
        return s.reindex(months, fill_value=0.0)

    plan_emps = set(pp[pp["ref_type"] == "employee"]["ref_id"])
    for eid in plan_emps:
        p = series(pp[pp["ref_type"] == "employee"], "ref_id", eid)
        a = (a_by.loc[eid] if eid in a_by.index.get_level_values(0) else pd.Series(dtype=float)) \
            .reindex(months, fill_value=0.0)
        e = emp.loc[eid]
        dept = e["department"]
        end = e["end_date"]
        if pd.notna(end) and end <= months[-1]:
            after = [m for m in months if m >= end]
            before = [m for m in months if m < end]
            items.append({"department": dept, "kind": "departure", "amount": float(a[after].sum() - p[after].sum()),
                          "label": f"{e['title']} ({e['name']}) left in {end:%b %Y}"})
            items.append({"department": dept, "kind": "pay_rate", "amount": float(a[before].sum() - p[before].sum()),
                          "label": "Pay vs plan for existing staff"})
        else:
            items.append({"department": dept, "kind": "pay_rate", "amount": float(a.sum() - p.sum()),
                          "label": "Pay vs plan for existing staff"})

    linked = set()
    for h in fd.hires.itertuples():
        p = series(pp[pp["ref_type"] == "hire"], "ref_id", h.hire_id)
        full = pp_all[(pp_all["ref_type"] == "hire") & (pp_all["ref_id"] == h.hire_id)]["amount"]
        rate_p = float(full[full > 0].iloc[0]) if (full > 0).any() else 0.0
        if isinstance(h.employee_id, str) and h.employee_id:
            linked.add(h.employee_id)
            a = (a_by.loc[h.employee_id] if h.employee_id in a_by.index.get_level_values(0)
                 else pd.Series(dtype=float)).reindex(months, fill_value=0.0)
            n_a, n_p = int((a > 0).sum()), int((p > 0).sum())
            start = emp.loc[h.employee_id, "start_date"]
            delay = (start.year - h.planned_start.year) * 12 + start.month - h.planned_start.month
            if n_a != n_p:
                items.append({"department": h.department, "kind": "late_start", "amount": (n_a - n_p) * rate_p,
                              "label": f"{h.role} started {start:%b} vs plan {h.planned_start:%b} "
                                       f"({delay} month{'s' if delay != 1 else ''} late)" if delay > 0
                              else f"{h.role} started early"})
            if n_a:
                items.append({"department": h.department, "kind": "pay_rate", "amount": float(a.sum() - n_a * rate_p),
                              "label": f"{h.role} hired at a different salary than planned"})
        elif p.sum():
            kind = "cancelled" if h.status == "cancelled" else "unfilled"
            items.append({"department": h.department, "kind": kind, "amount": -float(p.sum()),
                          "label": f"{h.role} ({'cancelled' if kind == 'cancelled' else 'not yet hired'}, "
                                   f"planned {h.planned_start:%b})"})

    for eid in set(ap["employee_id"]) - plan_emps - linked:
        e = emp.loc[eid]
        items.append({"department": e["department"], "kind": "unplanned",
                      "amount": float(a_by.loc[eid].reindex(months, fill_value=0.0).sum()),
                      "label": f"{e['title']} ({e['name']}), not in plan, started {e['start_date']:%b}"})

    items = [i for i in items if abs(i["amount"]) >= 1]
    depts = {}
    for dept in sorted(set(pp["department"]) | set(emp["department"])):
        p_tot = float(pp[pp["department"] == dept]["amount"].sum())
        a_tot = float(ap[ap["employee_id"].map(emp["department"]) == dept]["amount"].sum())
        comps: dict[str, float] = {}
        for i in items:
            if i["department"] == dept:
                comps[i["kind"]] = comps.get(i["kind"], 0.0) + i["amount"]
        depts[dept] = {"plan": p_tot, "actual": a_tot, "variance": a_tot - p_tot, "components": comps,
                       "headcount_plan": int(((pp["department"] == dept) & (pp["month"] == months[-1])).sum()),
                       "headcount_actual": int(((ap["month"] == months[-1])
                                                & (ap["employee_id"].map(emp["department"]) == dept)).sum())}
    return {"departments": depts, "items": sorted(items, key=lambda i: i["amount"])}


KIND_LABEL = {"late_start": "late starts", "unfilled": "open roles", "cancelled": "cancelled roles",
              "unplanned": "unplanned hires", "departure": "departures", "pay_rate": "pay vs plan"}


def explain_payroll(dept: str, d: dict, items: list[dict]) -> str:
    parts = []
    for kind, amt in sorted(d["components"].items(), key=lambda kv: -abs(kv[1])):
        n = sum(1 for i in items if i["department"] == dept and i["kind"] == kind)
        label = KIND_LABEL[kind]
        if kind != "pay_rate" and n == 1:
            label = label.removesuffix("s")
        parts.append(f"{f'{n} ' if kind != 'pay_rate' else ''}{label} {_money(amt)}")
    side = "under" if d["variance"] < 0 else "over"
    return f"{dept} payroll is {_money(abs(d['variance']))[1:]} {side} plan YTD: " + ", ".join(parts) + "."


# ---------------------------------------------------------------- non-payroll classification
def classify(var: pd.Series, plan: pd.Series) -> dict:
    """Rule-based: one-off spike, run-rate step change, or scattered noise."""
    total = float(var.sum())
    if not len(var) or abs(total) < 1:
        return {"pattern": "on_plan"}
    peak = var.abs().idxmax()
    if np.sign(var[peak]) == np.sign(total) and abs(var[peak]) >= 0.7 * abs(total):
        return {"pattern": "one_off", "month": str(peak.date()), "amount": float(var[peak])}
    pct = (var / plan.replace(0, np.nan)).fillna(0)
    sign = np.sign(total)
    streak = []
    for m in reversed(var.index):
        if np.sign(var[m]) == sign and abs(pct[m]) >= 0.08:
            streak.append(m)
        else:
            break
    if len(streak) >= 3:
        since = min(streak)
        run = float(var[streak].mean())
        return {"pattern": "run_rate", "since": str(since.date()), "monthly": run, "annualized": run * 12,
                "pct": float(pct[streak].mean())}
    return {"pattern": "scattered"}


def explain_other(name: str, var_ytd: float, c: dict) -> str:
    side = "over" if var_ytd > 0 else "under"
    head = f"{name} is {_money(abs(var_ytd))[1:]} {side} plan YTD"
    if c["pattern"] == "one_off":
        return f"{head}, almost all from one month ({pd.Timestamp(c['month']):%b}: {_money(c['amount'])}). Likely one-off."
    if c["pattern"] == "run_rate":
        return (f"{head}. Running {c['pct']:+.0%} vs plan every month since {pd.Timestamp(c['since']):%b}: "
                f"a run-rate change worth about {_money(c['annualized'])} a year if it continues.")
    return f"{head}, spread across months with no single cause."


# ---------------------------------------------------------------- the whole review
def review(fd: FinanceData, mrr: pd.DataFrame, month: pd.Timestamp | None = None) -> dict:
    per = period(fd, month)
    rows = pnl(fd, per)
    # Bridges need their inputs; without them we report the variance, not an invented explanation.
    rb = revenue_bridge(fd, mrr, per) if len(fd.drivers) and \
        (fd.fy_start - pd.DateOffset(months=1)) in set(pd.to_datetime(mrr["month"])) else None
    pb_ytd = payroll_bridge(fd, per.ytd) if len(fd.plan_payroll) else {"departments": {}, "items": []}
    acc = fd.accounts.set_index("code")

    findings = []
    # revenue
    rev = next(r for r in rows if r["key"] == "revenue")
    if rev["material_ytd"] and rb is None:
        findings.append({
            "key": "revenue", "area": "Revenue", "variance": rev["var_ytd"], "favorable": rev["favorable_ytd"],
            "title": f"Revenue is {_money(abs(rev['var_ytd']))[1:]} {'below' if rev['var_ytd'] < 0 else 'above'} plan YTD",
            "explanation": "Add revenue drivers to the plan (new customers, price, churn) to split this gap by cause.",
            "components": {}, "method": "Plan vs books"})
    elif rev["material_ytd"]:
        c = rb["ytd"]
        nc = rb["new_customers"]
        findings.append({
            "key": "revenue", "area": "Revenue", "variance": rev["var_ytd"], "favorable": rev["favorable_ytd"],
            "title": f"Revenue is {_money(abs(rev['var_ytd']))[1:]} {'below' if rev['var_ytd'] < 0 else 'above'} plan YTD",
            "explanation": (f"New-logo volume {_money(c['volume'])} ({nc['actual']:.0f} new customers vs "
                            f"{nc['plan']:.0f} planned), new-logo pricing {_money(c['price'])}, churn {_money(c['churn'])} "
                            f"({rb['revenue_churn']['actual']:.1%}/mo vs {rb['revenue_churn']['plan']:.1%} planned), "
                            f"expansion {_money(c['expansion'])}."),
            "components": c, "method": "Exact driver bridge (volume, price, churn, expansion)"})
    # payroll by department
    for dept, d in pb_ytd["departments"].items():
        gross = sum(abs(v) for v in d["components"].values())
        if material(d["variance"], d["plan"], kind="payroll") or gross >= max(0.08 * d["plan"], 50_000):
            findings.append({"key": f"payroll_{dept}", "area": f"Payroll: {dept}", "variance": d["variance"],
                             "favorable": d["variance"] <= 0,
                             "title": f"{dept} payroll {_money(d['variance'])} vs plan YTD",
                             "explanation": explain_payroll(dept, d, pb_ytd["items"]), "components": d["components"],
                             "method": "Exact person-level bridge (timing, open roles, unplanned, departures, rates)"})
    # non-payroll lines
    for r in rows:
        if r["level"] != "account" or acc.loc[r["key"], "kind"] in ("payroll", "revenue"):
            continue
        a = fd.actual[(fd.actual["account"] == r["key"]) & fd.actual["month"].isin(per.ytd)].set_index("month")["amount"]
        p = fd.plan_lines[(fd.plan_lines["account"] == r["key"]) & fd.plan_lines["month"].isin(per.ytd)] \
            .set_index("month")["amount"]
        a = a.reindex(per.ytd, fill_value=0.0)
        p = p.reindex(per.ytd, fill_value=0.0)
        name = acc.loc[r["key"], "name"]
        flex = None
        if acc.loc[r["key"], "kind"] == "cogs":
            # Flexible budget: variable costs are judged at the plan's cost per revenue dollar.
            rev_a = fd.actual[fd.actual["account"] == "4000"].set_index("month")["amount"].reindex(per.ytd)
            rev_p = fd.plan_lines[fd.plan_lines["account"] == "4000"].set_index("month")["amount"].reindex(per.ytd)
            flexed = p * rev_a / rev_p
            flex = {"volume": float((flexed - p).sum()), "rate": float((a - flexed).sum())}
            c = classify(a - flexed, flexed)
        else:
            c = classify(a - p, p)
        early = c["pattern"] == "run_rate" and abs(c["annualized"]) >= EARLY_WARNING_ANNUAL
        if not (r["material_ytd"] or early):
            continue
        if flex is not None:
            expl = (f"{name} is {_money(r['var_ytd'])} vs plan YTD: {_money(flex['volume'])} from revenue volume "
                    f"(expected for a variable cost) and {_money(flex['rate'])} from cost per revenue dollar. ")
            if c["pattern"] == "run_rate":
                expl += (f"Cost per revenue dollar has run {c['pct']:+.0%} above plan since "
                         f"{pd.Timestamp(c['since']):%b}, about {_money(c['annualized'])} a year if it continues.")
            title = (f"{name}: cost per revenue dollar {c['pct']:+.0%} vs plan since {pd.Timestamp(c['since']):%b}"
                     if c["pattern"] == "run_rate" else f"{name} {_money(r['var_ytd'])} vs plan YTD")
            comps, method = {**c, **flex}, "Flexible budget (volume vs rate) + run-rate rule"
        else:
            expl = explain_other(name, r["var_ytd"], c)
            title = (f"{name} is running {c['pct']:+.0%} vs plan since {pd.Timestamp(c['since']):%b}"
                     if early and not r["material_ytd"] else f"{name} {_money(r['var_ytd'])} vs plan YTD")
            comps, method = c, "Rule: one-off vs run-rate classification"
        findings.append({"key": r["key"], "area": name, "variance": r["var_ytd"], "favorable": r["favorable_ytd"],
                         "early_warning": early and not r["material_ytd"], "title": title, "explanation": expl,
                         "components": comps, "method": method})
    # rank by the larger of the YTD gap and a year of the run-rate change
    findings.sort(key=lambda f: -max(abs(f["variance"]), abs(f["components"].get("annualized", 0.0))
                                     if isinstance(f["components"], dict) else 0.0))

    # cash vs plan: plan cash = cash at plan time + cumulative planned operating income
    cash = fd.cash.set_index("month")["cash"]
    plan_time = fd.fy_start - pd.DateOffset(months=1)
    oi_plan = sum(r["plan_ytd"] for r in rows if r["key"] == "operating_income")
    oi_act = sum(r["actual_ytd"] for r in rows if r["key"] == "operating_income")
    cash_out = {"start": float(cash.get(plan_time, np.nan)), "plan": float(cash.get(plan_time, np.nan) + oi_plan),
                "actual": float(cash.get(per.month, np.nan)), "operating_variance": oi_act - oi_plan}

    return {"plan": {"id": fd.plan["id"], "name": fd.plan["name"], "approved_on": str(fd.plan["approved_on"]),
                     "assumptions": fd.plan["assumptions"]},
            "month": str(per.month.date()), "ytd_months": [str(m.date()) for m in per.ytd],
            "closed_months": [str(m.date()) for m in fd.closed_months],
            "pnl": rows, "revenue_bridge": rb, "payroll": pb_ytd, "cash": cash_out, "findings": findings}


def headcount(fd: FinanceData, month: pd.Timestamp) -> dict:
    emp = fd.employees
    active = emp[(emp["start_date"] <= month) & (emp["end_date"].isna() | (emp["end_date"] > month))]
    hires = []
    for h in fd.hires.itertuples():
        status, start = h.status, None
        if isinstance(h.employee_id, str) and h.employee_id:
            start = emp.set_index("employee_id").loc[h.employee_id, "start_date"]
            delay = (start.year - h.planned_start.year) * 12 + start.month - h.planned_start.month
            status = "on_time" if delay <= 0 else f"late_{delay}"
        elif h.status == "cancelled":
            status = "cancelled"
        elif h.planned_start <= month:
            status = "overdue"
        else:
            status = "upcoming"
        hires.append({"role": h.role, "department": h.department, "location": h.location,
                      "planned_start": str(h.planned_start.date()), "actual_start": str(start.date()) if start is not None else None,
                      "salary_plan": h.salary_annual, "status": status})
    by_dept = active.groupby("department").size().to_dict()
    return {"month": str(month.date()), "active": len(active), "by_department": by_dept, "plan_hires": hires,
            "departures_ytd": int(((emp["end_date"] >= fd.fy_start) & (emp["end_date"] <= month)).sum())}
