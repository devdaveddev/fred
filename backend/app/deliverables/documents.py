"""Document writers. Deterministic: same data, same document. Zero LLM calls.

Body format (rendered by the UI, printable to PDF):
  {"title", "subtitle", "sections": [{"id", "heading", "blocks": [
      {"type": "p", "text"} | {"type": "bullets", "items": [...]} |
      {"type": "kpis", "items": [{"label", "value", "note"}]} |
      {"type": "table", "columns": [...], "rows": [[...]]} | {"type": "callout", "tone", "text"}]}]}
"""
from __future__ import annotations

import math
import re
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from .. import revcore as rc
from ..provenance import Receipt
from .facts import FactBook, walk_text


def _title(s: str) -> str:
    acr = {"api", "hr", "iot", "d2c", "b2b", "crm", "erp", "ai"}
    return " · ".join(" ".join(w.upper() if w.lower() in acr else w[:1].upper() + w[1:] for w in p.split(" "))
                      for p in s.split(" / "))


class Ctx:
    """Everything a writer may read, plus the FactBook every number must go through."""

    def __init__(self, ws, analysis, fin: dict | None, extra: dict | None, tasks: list[dict], company: str):
        self.ws, self.a, self.fin, self.extra, self.tasks, self.company = ws, analysis, fin, extra or {}, tasks, company
        self.fb = FactBook()
        self.receipts: list[Receipt] = []
        self.R = ws.metrics.receipts
        self.as_of = pd.Timestamp(ws.metrics.as_of)

    def rid(self, metric: str, value: float, model_version: str | None = None, **inputs) -> str:
        """Receipt for a derived number that has none yet."""
        r = Receipt(metric=metric, value=value, snapshot_id=self.ws.snapshot_id,
                    inputs={"as_of": str(self.ws.metrics.as_of), **inputs}, model_version=model_version)
        self.receipts.append(r)
        return r.id

    def m(self, key: str):  # metric receipt shortcut
        r = self.R.get(key)
        return (r.value, r.id) if r else (None, None)

    @property
    def month_label(self) -> str:
        return self.as_of.strftime("%B %Y")

    def finish(self, title: str, subtitle: str, sections: list[dict]) -> tuple[dict, dict]:
        body = {"title": title, "subtitle": subtitle, "sections": sections}
        check = self.fb.verify(walk_text(body))
        return body, check


# ------------------------------------------------------------------ shared pieces
def _kpis(c: Ctx) -> list[dict]:
    fb = c.fb
    out = []
    arr, rid = c.m("arr")
    out.append({"label": "ARR", "value": fb.money("arr", arr, rid, "Annual recurring revenue")})
    g = c.a.facts.get("growth_yoy")
    if g is not None:
        out.append({"label": "Growth, year on year", "value": fb.pct("growth_yoy", g, c.rid("growth_yoy", g), "ARR growth YoY")})
    for key, label in (("nrr_12m", "Net revenue retention"), ("grr_12m", "Gross revenue retention")):
        v, rid = c.m(key)
        if v is not None:
            out.append({"label": label, "value": fb.pct(key, v, rid, label)})
    burn, rid = c.m("net_burn_monthly")
    if burn is not None:
        out.append({"label": "Net burn / month", "value": fb.money("net_burn", burn, rid, "Net burn per month")})
    rw, rid = c.m("runway_months")
    out.append({"label": "Runway", "value": fb.months("runway", rw if rw is not None else math.inf, rid, "Runway")})
    cust, rid = c.m("active_customers")
    out.append({"label": "Customers", "value": fb.num("customers", cust, rid, "Paying customers")})
    hc = (c.extra.get("headcount") or {}).get("active")
    if hc:
        out.append({"label": "Team", "value": fb.num("headcount", hc, c.rid("headcount", hc), "Headcount") + " people"})
    return out


def _revenue_vs_plan(c: Ctx) -> str | None:
    if not c.fin:
        return None
    rev = next(r for r in c.fin["pnl"] if r["key"] == "revenue")
    rb = c.fin["revenue_bridge"]
    fb = c.fb
    var = fb.money("rev_var_ytd", abs(rev["var_ytd"]), rev["receipt_id"], "Revenue vs plan, YTD")
    pct = fb.pct("rev_var_pct", abs(rev["var_pct_ytd"] or 0), rev["receipt_id"], "Revenue vs plan, %", digits=1)
    if rev["var_ytd"] < 0 and rb is None:
        return f"Revenue is {var} ({pct}) behind plan for the year."
    if rev["var_ytd"] < 0:
        nc = rb["new_customers"]
        comps = rb["ytd"]
        main = min(comps, key=comps.get)
        why = {"volume": "we added fewer new customers than planned", "churn": "churn ran above plan",
               "price": "new customers came in at lower prices than planned",
               "expansion": "existing customers expanded less than planned"}[main]
        na = fb.num("new_actual", nc["actual"], rev["receipt_id"], "New customers YTD")
        npl = fb.num("new_plan", nc["plan"], rev["receipt_id"], "New customers planned YTD")
        return f"Revenue is {var} ({pct}) behind plan for the year, mainly because {why} ({na} new customers against {npl} planned)."
    return f"Revenue is {var} ({pct}) ahead of plan for the year."


def _churn_reason(c: Ctx) -> str | None:
    v = c.a.voc
    if not v or not v["churn_reasons"]:
        return None
    t = v["churn_reasons"][0]
    share = c.fb.pct("churn_reason_share", t["mrr_share"],
                     c.rid("churn_reason_share", t["mrr_share"], "BAAI/bge-small-en-v1.5", theme=t["label"]),
                     f"Share of churned revenue: {t['label']}")
    return f"{t['label']} is the main reason customers leave ({share} of churned revenue)."


def _cost_watch(c: Ctx) -> list[str]:
    out = []
    if not c.fin:
        return out
    for f in c.fin["findings"]:
        comps = f["components"] if isinstance(f["components"], dict) else {}
        if f["favorable"] or comps.get("pattern") != "run_rate":
            continue
        pct = c.fb.pct(f"run_rate_{f['key']}", abs(comps["pct"]), f["receipt_id"], f"{f['area']} vs plan", signed=False)
        since = pd.Timestamp(comps["since"]).strftime("%B")
        out.append(f"{f['area']} has been running {pct} above plan since {since}.")
    return out[:2]


def _doing(c: Ctx, limit: int = 4) -> list[str]:
    open_tasks = [t for t in c.tasks if t["status"] in ("open", "in_progress")]
    if open_tasks:
        out = []
        for t in open_tasks[:limit]:
            due = pd.Timestamp(t["due_date"])
            day = c.fb.const(f"due_{t['id']}", due.day, due.strftime("%b %-d"))
            title = c.fb.prose(f"task_{t['id']}", t["title"], "user-written task")
            owner = c.fb.prose(f"owner_{t['id']}", t["owner"], "user-written task")
            out.append(f"{title}: {owner} owns it, due {day}.")
        return out
    acts = []
    for f in c.a.findings:
        if f.severity in ("critical", "high") and f.actions:
            acts.append(c.fb.prose(f"action_{f.id}", f.actions[0], "playbook wording"))
    return list(dict.fromkeys(acts))[:limit]


# ------------------------------------------------------------------ investor update
def investor_update(c: Ctx) -> tuple[dict, dict]:
    fb = c.fb
    arr, arr_rid = c.m("arr")
    g = c.a.facts.get("growth_yoy")
    g_rid = c.rid("growth_yoy", g) if g is not None else None
    burn, burn_rid = c.m("net_burn_monthly")
    rw, rw_rid = c.m("runway_months")
    tldr = (f"ARR is {fb.money('arr', arr, arr_rid)}"
            + (f", up {fb.pct('growth_yoy', g, g_rid)} on a year ago" if g is not None else "")
            + f". Net burn is {fb.money('net_burn', burn, burn_rid)} a month, giving us "
            + f"{fb.months('runway', rw if rw is not None else math.inf, rw_rid)} of runway.")

    highlights = []
    if g is not None and g >= 0.25:
        highlights.append(f"ARR grew {fb.pct('growth_yoy', g, g_rid)} year on year.")
    seg = c.a.segments and c.a.segments.get("best")
    if seg:
        nrr = fb.pct("best_segment_nrr", seg["nrr_12m"], c.rid("segment_nrr", seg["nrr_12m"], segment=seg["name"]),
                     "NRR of best segment")
        highlights.append(f"We now know our best-fit customers: {_title(seg['name'])} companies keep {nrr} of their "
                          "revenue after a year, and we are focusing acquisition there.")
    mn, mn_rid = c.m("magic_number")
    if mn is not None and mn >= 0.75:
        highlights.append(f"Sales and marketing is efficient, with a Magic Number of "
                          f"{fb.num('magic_number', mn, mn_rid, 'Magic Number', digits=2)}.")
    nrr, nrr_rid = c.m("nrr_12m")
    if nrr is not None and nrr >= 1.0:
        highlights.append(f"Existing customers grow: net revenue retention is {fb.pct('nrr_12m', nrr, nrr_rid)}.")

    lowlights = [x for x in (_revenue_vs_plan(c), _churn_reason(c)) if x]
    grr, grr_rid = c.m("grr_12m")
    if grr is not None and grr < 0.85:
        lowlights.append(f"Gross revenue retention is {fb.pct('grr_12m', grr, grr_rid)}, below where it needs to be.")
    lowlights += _cost_watch(c)

    asks = []
    if seg:
        nice = {"saas": "SaaS", "fintech": "fintech", "ecommerce": "e-commerce"}
        inds = " and ".join(nice.get(i, i) for i in seg["industries"][:2])
        asks.append(f"Introductions to {inds} companies that look like our best customers ({_title(seg['name'])}).")
    if c.extra.get("headcount"):
        overdue = [h for h in c.extra["headcount"]["plan_hires"] if h["status"] in ("overdue",)]
        if overdue:
            asks.append(f"Referrals for our open {overdue[0]['role']} role.")
    if rw is not None and math.isfinite(rw) and rw < 18:
        asks.append("Advice on timing for our next raise.")
    if not asks:
        asks.append("Introductions to companies that match our ideal customer profile.")

    sections = [
        {"id": "tldr", "heading": "In short", "blocks": [{"type": "p", "text": tldr}, {"type": "kpis", "items": _kpis(c)}]},
        {"id": "highlights", "heading": "What went well", "blocks": [{"type": "bullets", "items": highlights or
                                                                       ["A steady month with no major changes."]}]},
        {"id": "lowlights", "heading": "What didn't", "blocks": [{"type": "bullets", "items": lowlights or
                                                                    ["Nothing material off plan this month."]}]},
        {"id": "doing", "heading": "What we're doing about it", "blocks": [{"type": "bullets", "items": _doing(c)}]},
        {"id": "asks", "heading": "How you can help", "blocks": [{"type": "bullets", "items": asks}]},
    ]
    return c.finish(f"{c.company} investor update: {c.month_label}",
                    "Monthly update for investors and advisors", sections)


# ------------------------------------------------------------------ board pack
def board_pack(c: Ctx) -> tuple[dict, dict]:
    fb, a = c.fb, c.a
    sections: list[dict[str, Any]] = []

    # 1 executive summary
    summary = [x for x in (_revenue_vs_plan(c),) if x]
    if c.fin:
        oi = next(r for r in c.fin["pnl"] if r["key"] == "operating_income")
        side = "better" if oi["var_ytd"] >= 0 else "worse"
        summary.append(f"Operating result is {fb.money('oi_var', abs(oi['var_ytd']), oi['receipt_id'], 'Operating income vs plan YTD')} "
                       f"{side} than plan year to date.")
        cash = c.fin["cash"]
        summary.append(f"Cash is {fb.money('cash', cash['actual'], c.rid('cash', cash['actual']), 'Cash')}, "
                       f"{fb.money('cash_var', abs(cash['actual'] - cash['plan']), c.rid('cash_vs_plan', cash['actual'] - cash['plan']), 'Cash vs plan')} "
                       f"{'above' if cash['actual'] >= cash['plan'] else 'below'} plan.")
    cr = _churn_reason(c)
    if cr:
        summary.append(cr)
    decisions = _decisions(c)
    if decisions:
        summary.append(f"The board is asked to discuss: {decisions[0]['ask'].rstrip('.').lower()}.")
    sections.append({"id": "summary", "heading": "Executive summary",
                     "blocks": [{"type": "bullets", "items": summary}, {"type": "kpis", "items": _kpis(c)}]})

    # 2 financials vs plan
    if c.fin:
        rows = []
        for r in c.fin["pnl"]:
            if r["level"] == "account":
                continue
            k = r["key"]
            rows.append([r["label"],
                         fb.money(f"plan_{k}", r["plan_ytd"], r["receipt_id"], f"{r['label']} plan YTD"),
                         fb.money(f"act_{k}", r["actual_ytd"], r["receipt_id"], f"{r['label']} actual YTD"),
                         fb.money(f"var_{k}", r["var_ytd"], r["receipt_id"], f"{r['label']} variance YTD", signed=True)])
        rbx = c.fin["revenue_bridge"]
        rev_rid = next(r["receipt_id"] for r in c.fin["pnl"] if r["key"] == "revenue")
        bridge = [f"{name}: {fb.money(f'bridge_{k}', rbx['ytd'][k], rev_rid, f'Revenue bridge: {name}', signed=True)}"
                  for k, name in (("volume", "New-customer volume"), ("price", "New-customer pricing"),
                                  ("churn", "Churn"), ("expansion", "Expansion"))] if rbx else \
            ["Add revenue drivers to the plan to split the revenue gap by cause."]
        sections.append({"id": "financials", "heading": f"Financials vs {c.fin['plan']['name']}",
                         "blocks": [{"type": "table", "columns": ["Year to date", "Plan", "Actual", "Variance"], "rows": rows},
                                    {"type": "p", "text": "Revenue gap, by cause:"},
                                    {"type": "bullets", "items": bridge}]})
        movers = []
        for f in c.fin["findings"][:4]:
            movers.append(f"{f['area']}: {fb.money(f'mover_{f['key']}', f['variance'], f['receipt_id'], f['area'] + ' vs plan', signed=True)}. "
                          + _mover_reason(f))
        sections.append({"id": "movers", "heading": "What moved and why", "blocks": [{"type": "bullets", "items": movers}]})

    # 3 customers
    cust = []
    nrr, nrr_rid = c.m("nrr_12m")
    grr, grr_rid = c.m("grr_12m")
    if nrr is not None:
        cust.append(f"Net revenue retention {fb.pct('nrr_12m', nrr, nrr_rid)}; gross revenue retention {fb.pct('grr_12m', grr, grr_rid)}.")
    risky = a.at_risk[a.at_risk["p"] >= 0.25]
    if len(risky):
        mrr_risk = float(risky["mrr_at_risk"].sum())
        cust.append(f"{fb.num('at_risk_accounts', len(risky), c.rid('at_risk_accounts', len(risky), model_version=a.churn_model.card.version))} "
                    f"accounts are likely to churn in the next {fb.const('horizon', 3, '3 months')}, "
                    f"carrying {fb.money('at_risk_arr', mrr_risk * 12, c.rid('churn_arr_at_risk', mrr_risk * 12, model_version=a.churn_model.card.version))} of expected ARR loss.")
    if a.voc and a.voc["churn_reasons"]:
        top3 = a.voc["churn_reasons"][:3]
        cust.append("Why customers leave: " + "; ".join(
            f"{t['label']} {fb.pct('cr_' + t['theme'], t['mrr_share'], c.rid('churn_reason_share', t['mrr_share'], theme=t['label']))}"
            for t in top3) + ".")
    if a.segments and a.segments.get("best"):
        b, w = a.segments["best"], a.segments["worst"]
        cust.append(f"Best segment: {_title(b['name'])} (keeps {fb.pct('best_nrr', b['nrr_12m'], c.rid('segment_nrr', b['nrr_12m'], segment=b['name']))} of revenue). "
                    f"Weakest: {_title(w['name'])} (keeps {fb.pct('worst_nrr', w['nrr_12m'], c.rid('segment_nrr', w['nrr_12m'], segment=w['name']))}).")
    sections.append({"id": "customers", "heading": "Customers and retention", "blocks": [{"type": "bullets", "items": cust}]})

    # 4 go-to-market
    gtm = []
    paid = [ch for ch in a.channels if ch["paid"] and ch["payback_months"]]
    if paid:
        best = min(paid, key=lambda x: x["payback_months"])
        worst = max(paid, key=lambda x: x["payback_months"])
        gtm.append(f"Fastest payback: {best['channel'].replace('_', ' ')} ({fb.num('pb_best', best['payback_months'], c.rid('channel_payback', best['payback_months'], channel=best['channel']), digits=1)} months). "
                   f"Slowest: {worst['channel'].replace('_', ' ')} ({fb.num('pb_worst', worst['payback_months'], c.rid('channel_payback', worst['payback_months'], channel=worst['channel']), digits=1)} months).")
    if a.realloc.get("delta_customers_monthly", 0) >= 0.5:
        d = a.realloc["delta_customers_monthly"]
        gtm.append(f"Shifting budget between channels at the same total spend would add about "
                   f"{fb.num('realloc', d, c.rid('realloc_delta_customers', d), digits=1)} customers a month.")
    fast = a.scored_leads[a.scored_leads["lane"] == "fast_lane"]
    if len(fast):
        gtm.append(f"{fb.num('fast_lane', len(fast), c.rid('fast_lane_leads', len(fast), model_version=a.lead_model.card.version))} "
                   "high-intent leads are routed to account executives.")
    sections.append({"id": "gtm", "heading": "Go-to-market", "blocks": [{"type": "bullets", "items": gtm or ["No changes."]}]})

    # 5 team
    hc = c.extra.get("headcount")
    if hc:
        late = [h for h in hc["plan_hires"] if h["status"].startswith("late_")]
        open_ = [h for h in hc["plan_hires"] if h["status"] == "overdue"]
        team = [(f"Headcount {fb.num('headcount', hc['active'], c.rid('headcount', hc['active']))}; "
                f"{fb.num('departures', hc['departures_ytd'], c.rid('departures_ytd', hc['departures_ytd']))} "
                 f"departure{'' if hc['departures_ytd'] == 1 else 's'} this year.")]
        if late:
            team.append(f"{fb.num('late_hires', len(late), c.rid('late_hires', len(late)))} planned hires started late, which kept payroll under plan.")
        if open_:
            team.append("Open roles past their planned start: " + ", ".join(h["role"] for h in open_) + ".")
        sections.append({"id": "team", "heading": "Team", "blocks": [{"type": "bullets", "items": team}]})

    # 6 risks + 7 decisions
    risks = []
    for f in [f for f in a.findings if f.severity in ("critical", "high")][:4]:
        owner = next((t["owner"] for t in c.tasks if t["finding_id"] == f.id and t["status"] != "cancelled"), None)
        mitigation = fb.prose(f"mitigation_{f.id}", f.actions[0], "playbook wording") if f.actions else ""
        owner = fb.prose(f"riskowner_{f.id}", owner, "user-written task") if owner else "Unassigned"
        risks.append([plain_title(f), mitigation, owner])
    sections.append({"id": "risks", "heading": "Risks and what we're doing",
                     "blocks": [{"type": "table", "columns": ["Risk", "Mitigation", "Owner"], "rows": risks}]})
    if decisions:
        sections.append({"id": "decisions", "heading": "Decisions for the board",
                         "blocks": [{"type": "bullets", "items": [d["ask"] + " " + d["why"] for d in decisions]}]})
    return c.finish(f"{c.company} board pack", f"Data through {c.month_label}", sections)


def _mover_reason(f: dict) -> str:
    comps = f["components"] if isinstance(f["components"], dict) else {}
    p = comps.get("pattern")
    if p == "one_off":
        return "A one-off; not expected to repeat."
    if p == "run_rate" and "rate" in comps:
        return ("Lower revenue reduced the bill, but cost per revenue dollar is running above plan."
                if f["variance"] < 0 else "Cost per revenue dollar is running above plan and will continue unless addressed.")
    if p == "run_rate":
        return "A run-rate change; it will continue unless addressed."
    if f["key"] == "revenue":
        return "Driven mainly by new-customer volume and churn."
    if f["key"].startswith("payroll_"):
        return "Mostly hiring timing: late starts, open roles and departures."
    return "Spread across months."


def plain_title(f) -> str:
    """Risk table rows use plain-language titles; numbers live in the sections above."""
    plain = {"churn_risk": "Accounts likely to churn in the next quarter", "churn_reason": "Main churn driver",
             "fast_lane": "High-intent leads waiting for sales follow-up", "goal_plan": "Growth goal vs current trajectory",
             "pb_revenue_leak": "Gross revenue retention below target"}
    if f.id in plain:
        return plain[f.id]
    if f.id.startswith("anomaly_"):
        return f"Unusual {f.id.split('_', 1)[1].replace('_', ' ')} spend"
    if f.id == "bva_revenue":
        return "Revenue behind plan" if "below" in f.title else "Revenue vs plan"
    if f.id.startswith("bva_payroll_"):
        return f"{f.id.removeprefix('bva_payroll_')} payroll vs plan"
    if f.id.startswith("bva_"):
        return re.split(r" [+\-−]\$| is running|:", f.title)[0].strip()
    if f.id == "segment_best":
        return "Win more of the strongest segment"
    if f.id == "segment_worst":
        return "Stop acquiring the weakest segment"
    if f.id == "budget_reallocation":
        return "Rebalance marketing budget across channels"
    if f.id == "icp":
        return "Retarget toward the real ideal customer profile"
    if f.id == "forecast":
        return "Base-case forecast"
    clean = re.sub(r"[\d$%.,+\-−]+", " ", f.title)
    return re.sub(r"\s+", " ", clean).strip(" :.")


def _decisions(c: Ctx) -> list[dict]:
    out = []
    plan = c.a.forecast.get("plan", {})
    if plan.get("out_of_sample"):
        out.append({"ask": "Agree a realistic growth target or a funding plan.",
                    "why": "The current goal needs more customer acquisition than the business has ever shown."})
    mn, _ = c.m("magic_number")
    rw, _ = c.m("runway_months")
    if mn is not None and mn > 1 and rw is not None and (not math.isfinite(rw) or rw > 18):
        out.append({"ask": "Approve a step-up in sales and marketing spend.",
                    "why": "Spend is converting efficiently and runway allows it; scale in stages with a stop rule."})
    grr, _ = c.m("grr_12m")
    if grr is not None and grr < 0.85:
        out.append({"ask": "Prioritise retention work on the roadmap.",
                    "why": "Gross retention is below target; the main churn reason is fixable product work."})
    return out


# ------------------------------------------------------------------ decision memos
def decision_memo(c: Ctx, kind: str, p: dict) -> tuple[dict, dict]:
    if kind == "hire":
        return _memo_hire(c, p)
    if kind == "price":
        return _memo_scenario(c, "price", p)
    if kind == "spend":
        return _memo_scenario(c, "spend", p)
    raise ValueError("unknown memo type")


def _memo_hire(c: Ctx, p: dict) -> tuple[dict, dict]:
    fb = c.fb
    role, count = p["role"].strip(), int(p.get("count", 1))
    salary = float(p["salary_annual"])
    loaded = salary * 1.22 / 12 * count  # US employer taxes + benefits (same factor as payroll data)
    burn, burn_rid = c.m("net_burn_monthly")
    cash = c.ws.metrics.baseline.cash
    rw_now = cash / burn if burn and burn > 0 else math.inf
    rw_after = cash / (burn + loaded) if burn + loaded > 0 else math.inf
    rid = c.rid("memo_hire_runway", rw_after, role=role, count=count, salary_annual=salary, loaded_monthly=loaded,
                cash=cash, burn=burn)
    in_plan = [h for h in (c.extra.get("headcount") or {}).get("plan_hires", [])
               if h["role"].lower() == role.lower() and h["status"] in ("overdue", "upcoming")]
    if rw_after >= 18:
        rec, tone = f"Hire. Runway stays at {fb.months('rw_after', rw_after, rid)}, above the comfortable range.", "good"
    elif rw_after >= 12:
        rec, tone = (f"Hire only if it is tied to revenue within two quarters: runway drops to "
                     f"{fb.months('rw_after', rw_after, rid)}."), "watch"
    else:
        rec, tone = f"Hold. Runway would fall to {fb.months('rw_after', rw_after, rid)}, inside the fundraising danger zone.", "bad"
    why = [
        (f"Fully loaded cost: {fb.money('hire_cost_m', loaded, rid, 'Monthly loaded cost')} a month "
        f"({fb.money('hire_cost_y', loaded * 12, rid, 'Annual loaded cost')} a year)."),
        f"Net burn goes from {fb.money('net_burn', burn, burn_rid)} to {fb.money('burn_after', burn + loaded, rid, 'Burn after hire')} a month.",
        f"Runway goes from {fb.months('rw_now', rw_now, rid)} to {fb.months('rw_after', rw_after, rid)}.",
        ("This role is already in the approved plan, so it does not change the budget." if in_plan
         else "This role is not in the approved plan; it adds to budgeted spend."),
    ]
    sections = [
        {"id": "question", "heading": "Question", "blocks": [{"type": "p", "text":
            f"Should we hire {'a' if count == 1 else fb.num('count', count, rid)} "
            f"{role if count == 1 or role.endswith('s') else role + 's'}?"}]},
        {"id": "recommendation", "heading": "Recommendation", "blocks": [{"type": "callout", "tone": tone, "text": rec}]},
        {"id": "why", "heading": "The numbers", "blocks": [{"type": "bullets", "items": why}]},
        {"id": "risks", "heading": "What would change this", "blocks": [{"type": "bullets", "items": [
            "Revenue falling further behind plan would shorten runway faster than shown.",
            "A new funding round or a large contract would make the hire straightforward.",
        ]}]},
        {"id": "assumptions", "heading": "Assumptions", "blocks": [{"type": "bullets", "items": [
            f"Employer taxes and benefits add {fb.const('loading', 0.22, '22%')} to salary.",
            "Burn is the average of the last three months and stays flat apart from this hire.",
        ]}]},
    ]
    return c.finish(f"Decision memo: hire {role}", f"Prepared {c.month_label}", sections)


def _memo_scenario(c: Ctx, kind: str, p: dict) -> tuple[dict, dict]:
    fb = c.fb
    base = c.ws.metrics.baseline
    if kind == "price":
        change = float(p["price_change"])
        shock = rc.Shock(price_change=change, churn_multiplier=float(p.get("churn_multiplier", 1.0)))
        question = f"Should we change prices by {fb.pct('price_change', change, None, 'Price change', signed=True)}?"
    else:
        change = float(p["spend_change"])
        el = [x["b"] for x in c.a.curves.values() if x["r2"] >= 0.3]
        b = float(np.clip(np.mean(el), 0.3, 1.0)) if el else 0.7
        # diminishing returns: customers scale with spend^b, so effective CAC rises by (1+change)^(1-b)
        shock = rc.Shock(sm_spend_change=change, cac_change=(1 + change) ** (1 - b) - 1)
        question = f"Should we change sales and marketing spend by {fb.pct('spend_change', change, None, 'S&M change', signed=True)}?"
    res = rc.compare(replace(base), shock, months=24, runs=5000, seed=42)
    bl, sc = res["baseline"], res["scenario"]
    inputs = {"shock": shock.__dict__, "months": 24, "runs": 5000}
    r_arr = c.rid("memo_arr_p50", sc["arr_end"]["p50"], seed=42, **inputs)
    r_nrr = c.rid("memo_nrr_p50", sc["nrr_12m"]["p50"], seed=42, **inputs)
    r_cash = c.rid("memo_p_cash_out", sc["p_cash_out"], seed=42, **inputs)
    r_ltv = c.rid("memo_ltv_cac", sc["ltv_cac_wacc"], seed=42, **inputs)
    d_arr = sc["arr_end"]["p50"] / bl["arr_end"]["p50"] - 1
    d_nrr = sc["nrr_12m"]["p50"] - bl["nrr_12m"]["p50"]
    good = d_arr > 0.02 and d_nrr > -0.02 and sc["ltv_cac_wacc"] >= 3 and sc["p_cash_out"] <= max(0.05, bl["p_cash_out"])
    risky = sc["p_cash_out"] > bl["p_cash_out"] + 0.05 or d_arr < 0
    if good:
        rec, tone = "Do it, and review the result after one quarter.", "good"
    elif risky:
        rec, tone = "Don't, on current numbers: it lowers growth or raises the risk of running out of cash.", "bad"
    else:
        rec, tone = "Test it on a small group first (new customers only, or one channel) before rolling it out.", "watch"
    why = [
        (f"ARR in {fb.const('horizon24', 24, '24 months')}: {fb.money('arr_base', bl['arr_end']['p50'], r_arr)} without the change, "
        f"{fb.money('arr_scen', sc['arr_end']['p50'], r_arr)} with it ({fb.pct('d_arr', d_arr, r_arr, 'Change in ARR', signed=True, digits=1)})."),
        f"Net revenue retention: {fb.pct('nrr_base', bl['nrr_12m']['p50'], r_nrr)} → {fb.pct('nrr_scen', sc['nrr_12m']['p50'], r_nrr)}.",
        f"LTV:CAC: {fb.multiple('ltv_base', bl['ltv_cac_wacc'], r_ltv)} → {fb.multiple('ltv_scen', sc['ltv_cac_wacc'], r_ltv)}.",
        f"Chance of running out of cash within the period: {fb.pct('cash_base', bl['p_cash_out'], r_cash)} → {fb.pct('cash_scen', sc['p_cash_out'], r_cash)}.",
    ]
    assumptions = [f"Simulated {fb.const('runs', 5000, '5,000')} times with a fixed seed: same inputs, same answer."]
    if kind == "price":
        assumptions.append(f"Price elasticity {fb.num('elasticity', base.price_elasticity, None, 'Price elasticity', digits=1)} "
                           "(default until fitted from your own price changes).")
        assumptions.append(f"Churn reaction: ×{fb.num('churn_mult', shock.churn_multiplier, None, 'Churn multiplier', digits=2)}.")
    else:
        assumptions.append("Returns to spend follow your fitted channel curves: each extra dollar buys fewer customers.")
    sections = [
        {"id": "question", "heading": "Question", "blocks": [{"type": "p", "text": question}]},
        {"id": "recommendation", "heading": "Recommendation", "blocks": [{"type": "callout", "tone": tone, "text": rec}]},
        {"id": "why", "heading": "The numbers (median outcome)", "blocks": [{"type": "bullets", "items": why}]},
        {"id": "risks", "heading": "What would change this", "blocks": [{"type": "bullets", "items": [
            "How customers really react: run it on new customers first and measure churn for a quarter.",
            "If cash gets tighter, prefer changes that pay back fastest.",
        ]}]},
        {"id": "assumptions", "heading": "Assumptions", "blocks": [{"type": "bullets", "items": assumptions}]},
    ]
    title = "Decision memo: price change" if kind == "price" else "Decision memo: sales and marketing budget"
    return c.finish(title, f"Prepared {c.month_label}", sections)
