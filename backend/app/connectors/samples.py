"""Sample files: the demo company exported in exactly the formats the importers accept.

Used for 'download a sample' in the UI and for the import round-trip test.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date

from ..seed import Generated, generate
from ..seed_finance import ACCOUNTS, LOADED

ACCT_NAME = {c: n for c, n, *_ in ACCOUNTS}
SECTION = {"revenue": "Income", "cogs": "Cost of Goods Sold", "sm": "Expenses", "rnd": "Expenses", "ga": "Expenses"}
GROUP = {c: g for c, _, g, *_ in ACCOUNTS}


def _csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def _money(v: float) -> str:
    s = f"{abs(v):,.2f}"
    return f"({s})" if v < 0 else s


def export(g: Generated | None = None) -> dict[str, str]:
    g = g or generate()
    acc_by_id = {a["id"]: a for a in g.accounts}
    months = g.months
    out: dict[str, str] = {}

    out["billing"] = _csv(["customer_id", "customer_name", "email", "month", "mrr"],
                          [[r["a"], acc_by_id[r["a"]]["name"], f"billing@{acc_by_id[r['a']]['domain']}",
                            r["m"].strftime("%Y-%m"), f"{r['v']:.2f}"] for r in g.mrr])

    # GL as a QuickBooks 'Profit and Loss by Month' export (title rows, sections, totals, $ formatting)
    by = defaultdict(dict)
    for r in g.gl:
        by[r["acct"]][r["m"]] = r["amt"]
    rows = [["Acme Analytics (demo)"], ["Profit and Loss"], [f"{months[0]:%B %Y} - {months[-1]:%B %Y}"], [],
            [""] + [f"{m:%b %Y}" for m in months] + ["Total"]]
    for grp, section in (("revenue", "Income"), ("cogs", "Cost of Goods Sold"), ("x", "Expenses")):
        rows.append([section] + [""] * (len(months) + 1))
        codes = [c for c in by if (GROUP[c] == grp if grp != "x" else GROUP[c] in ("sm", "rnd", "ga"))]
        for c in sorted(codes):
            vals = [by[c].get(m, 0.0) for m in months]
            rows.append([f"   {c} {ACCT_NAME[c]}"] + [f"${_money(v)}" for v in vals] + [f"${_money(sum(vals))}"])
        tot = [sum(by[c].get(m, 0.0) for c in codes) for m in months]
        rows.append([f"Total {section}"] + [f"${_money(v)}" for v in tot] + [f"${_money(sum(tot))}"])
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    out["gl"] = buf.getvalue()

    out["cash"] = _csv(["month", "cash"], [[r["m"].strftime("%Y-%m"), f"{r['c']:.2f}"] for r in g.cash])

    p = g.people
    out["payroll"] = _csv(["name", "title", "department", "location", "start_date", "end_date", "salary_annual",
                           "loaded_factor"],
                          [[e["name"], e["title"], e["department"], e["location"], e["start_date"].isoformat(),
                            e["end_date"].isoformat() if e["end_date"] else "", f"{e['salary_annual']:.0f}",
                            LOADED[e["location"]]] for e in p.employees])

    plan_months = sorted({r["month"] for r in g.plan["lines"]})
    pl = defaultdict(dict)
    for r in g.plan["lines"]:
        pl[r["account"]][r["month"]] = r["amount"]
    out["budget"] = _csv(["account"] + [f"{m:%b %Y}" for m in plan_months],
                         [[f"{c} {ACCT_NAME[c]}"] + [f"{pl[c][m]:.2f}" for m in plan_months] for c in sorted(pl)])
    out["hiring_plan"] = _csv(["role", "department", "location", "planned_start", "salary_annual"],
                              [[h["role"], h["department"], h["location"], h["planned_start"].strftime("%Y-%m"),
                                f"{h['salary_annual']:.0f}"] for h in p.planned_hires if h["status"] != "cancelled"])

    daily = defaultdict(float)
    for r in g.spend:
        if r["c"] == "sm" and r["ch"] != "sales_team":
            daily[(r["ts"].date(), r["ch"])] += r["amt"]
    out["spend"] = _csv(["date", "channel", "amount"], [[d.isoformat(), ch, f"{v:.2f}"] for (d, ch), v in sorted(daily.items())])
    out["feedback"] = _csv(["date", "customer_id", "kind", "score", "text"],
                           [[r["ts"].date().isoformat(), r["a"], r["kind"], "" if r["score"] is None else r["score"], r["body"]]
                            for r in g.feedback])
    out["usage"] = _csv(["customer_id", "week", "seats", "active_users", "events"],
                        [[r["a"], r["w"].isoformat(), r["s"], r["au"], r["e"]] for r in g.usage])
    return out


def demo_today() -> date:
    return date.today().replace(day=1)
