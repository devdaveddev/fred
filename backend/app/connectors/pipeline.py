"""Write pipeline: each source refreshes its own slice of the canonical tables, idempotently.

Rules that keep real data honest:
  * the first real import replaces the demo company (the UI says so before you confirm)
  * ids are deterministic per workspace (uuid5 of the source id), so re-syncs update instead of duplicating
  * accounting (GL) is the source of truth for spend totals; ad-platform files only add channel detail
  * user overrides of the account mapping survive every re-import
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import mapping


def last_closed_month(today: date | None = None) -> date:
    t = (today or date.today()).replace(day=1)
    return date(t.year - 1, 12, 1) if t.month == 1 else date(t.year, t.month - 1, 1)


def _add(m: date, n: int) -> date:
    y, mo = divmod(m.month - 1 + n, 12)
    return date(m.year + y, mo + 1, 1)


def _id(tid: str, key: str) -> str:
    return str(uuid.uuid5(uuid.UUID(tid), key))


def ensure_real(conn: Connection, tid: str) -> bool:
    """Drop the demo company the first time real data arrives. Returns True if demo data was removed."""
    src = conn.execute(text("SELECT data_source FROM tenant WHERE id = :t"), {"t": tid}).scalar()
    if src == "demo":
        from ..seed import wipe_tenant_data

        wipe_tenant_data(conn, tid)
        conn.execute(text("UPDATE tenant SET data_source = 'connected' WHERE id = :t"), {"t": tid})
        return True
    if src != "connected":
        conn.execute(text("UPDATE tenant SET data_source = 'connected' WHERE id = :t"), {"t": tid})
    return False


def _segment(employees: int | None, mrr: float | None) -> str:
    if employees:
        return "SMB" if employees < 60 else ("Mid-market" if employees < 600 else "Enterprise")
    m = mrr or 0
    return "SMB" if m < 300 else ("Mid-market" if m < 1500 else "Enterprise")


def _account_ids_by_external(conn: Connection, tid: str) -> dict[str, str]:
    return {e: a for a, e in conn.execute(text("""SELECT id::text, external_id FROM account
                                                  WHERE tenant_id = :t AND external_id IS NOT NULL"""), {"t": tid})}


# ------------------------------------------------------------------ billing
def write_billing(conn: Connection, tid: str, accounts: list[dict], mrr_rows: list[dict]) -> dict:
    latest: dict[str, float] = {}
    for r in sorted(mrr_rows, key=lambda r: r["m"]):
        latest[r["a"]] = r["v"]
    for a in accounts:
        conn.execute(text("""
            INSERT INTO account (id, tenant_id, name, domain, segment, country, created_at, external_id)
            VALUES (:id, :t, :name, :domain, :seg, :country, :created, :ext)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, domain = COALESCE(EXCLUDED.domain, account.domain),
              country = COALESCE(account.country, EXCLUDED.country), external_id = EXCLUDED.external_id,
              segment = CASE WHEN account.employees IS NULL THEN EXCLUDED.segment ELSE account.segment END"""),
            {"id": a["id"], "t": tid, "name": a["name"][:200], "domain": a.get("domain"),
             "seg": _segment(None, latest.get(a["id"])), "country": a.get("country"),
             "created": a.get("created_at") or datetime.now(UTC), "ext": a["external_id"]})
    conn.execute(text("DELETE FROM subscription_mrr WHERE tenant_id = :t"), {"t": tid})
    if mrr_rows:
        conn.execute(text("INSERT INTO subscription_mrr (tenant_id, account_id, month, mrr) VALUES (:t, :a, :m, :v)"),
                     [{**r, "t": tid} for r in mrr_rows])
    return {"accounts": len(accounts), "mrr_rows": len(mrr_rows), "months": len({r["m"] for r in mrr_rows})}


def billing_from_csv(tid: str, rows: list[dict]) -> tuple[list[dict], list[dict]]:
    from .stripe import domain_of

    accounts, mrr = {}, defaultdict(float)
    cap = last_closed_month()
    for r in rows:
        aid = _id(tid, f"billing:{r['customer_id']}")
        accounts.setdefault(aid, {"id": aid, "external_id": r["customer_id"], "name": r["name"],
                                  "domain": domain_of(r.get("email")), "country": None,
                                  "created_at": datetime(r["month"].year, r["month"].month, 1, tzinfo=UTC)})
        if r["month"] <= cap and r["mrr"] > 0:
            mrr[(aid, r["month"])] += r["mrr"]
    return list(accounts.values()), [{"a": a, "m": m, "v": round(v, 2)} for (a, m), v in mrr.items()]


# ------------------------------------------------------------------ CRM
def write_crm(conn: Connection, tid: str, profiles: dict[str, dict], leads: list[dict]) -> dict:
    enriched = 0
    for dom, p in profiles.items():
        n = conn.execute(text("""
            UPDATE account SET industry = COALESCE(:ind, industry), employees = COALESCE(:emp, employees),
              country = COALESCE(:country, country), description = COALESCE(:descr, description),
              acquisition_channel = COALESCE(:ch, acquisition_channel),
              crm = COALESCE(:crm, crm), crm_id = COALESCE(:crm_id, crm_id),
              segment = CASE WHEN :emp IS NULL THEN segment WHEN :emp < 60 THEN 'SMB'
                             WHEN :emp < 600 THEN 'Mid-market' ELSE 'Enterprise' END
            WHERE tenant_id = :t AND lower(domain) = :dom"""),
            {"t": tid, "dom": dom, "ind": p.get("industry"), "emp": p.get("employees"), "country": p.get("country"),
             "descr": p.get("description"), "ch": p.get("acquisition_channel"), "crm": p.get("crm"),
             "crm_id": p.get("crm_id")}).rowcount
        enriched += n
    by_domain = {d: a for a, d in conn.execute(text("SELECT id::text, lower(domain) FROM account WHERE tenant_id = :t"),
                                               {"t": tid}) if d}
    conn.execute(text("DELETE FROM lead WHERE tenant_id = :t"), {"t": tid})
    rows = []
    for x in leads:
        acc = by_domain.get((x.get("domain") or "").lower()) if x["status"] == "converted" else None
        rows.append({**{k: x[k] for k in ("id", "created_at", "source_channel", "company", "contact_name", "industry",
                                          "employees", "country", "seniority", "pricing_views", "docs_views",
                                          "email_engagement", "demo_requested", "webinar", "status", "converted_at",
                                          "description")}, "t": tid, "account_id": acc,
                     "crm": x.get("crm"), "external_id": x.get("external_id")})
    if rows:
        conn.execute(text("""
            INSERT INTO lead (id, tenant_id, created_at, source_channel, company, contact_name, industry, employees,
              country, seniority, pricing_views, docs_views, email_engagement, demo_requested, webinar, status,
              converted_at, account_id, description, crm, external_id)
            VALUES (:id, :t, :created_at, :source_channel, :company, :contact_name, :industry, :employees, :country,
              :seniority, :pricing_views, :docs_views, :email_engagement, :demo_requested, :webinar, :status,
              :converted_at, :account_id, :description, :crm, :external_id)"""), rows)
    return {"accounts_enriched": enriched, "leads": len(rows)}


# ------------------------------------------------------------------ accounting
def write_gl(conn: Connection, tid: str, parsed: dict, embed=None) -> dict:
    existing = {r["code"]: dict(r) for r in conn.execute(text(
        "SELECT code, pl_group, kind, department, mapped_by FROM gl_account WHERE tenant_id = :t"), {"t": tid}).mappings()}
    accts = parsed["accounts"]
    guesses = mapping.classify([a["name"] for a in accts], [a.get("section") for a in accts], embed=embed)
    low_conf = 0
    for a, gss in zip(accts, guesses):
        keep = existing.get(a["code"], {}).get("mapped_by") == "user"
        if gss["method"] != "rule":
            low_conf += 1
        conn.execute(text("""
            INSERT INTO gl_account (tenant_id, code, name, pl_group, kind, department, section, mapped_by, confidence)
            VALUES (:t, :code, :name, :g, :k, :d, :sec, :by, :conf)
            ON CONFLICT (tenant_id, code) DO UPDATE SET name = EXCLUDED.name, section = EXCLUDED.section""" + ("" if keep else """,
              pl_group = EXCLUDED.pl_group, kind = EXCLUDED.kind, department = EXCLUDED.department,
              mapped_by = EXCLUDED.mapped_by, confidence = EXCLUDED.confidence""")),
            {"t": tid, "code": a["code"][:60], "name": a["name"][:200], "g": gss["pl_group"], "k": gss["kind"],
             "d": gss["department"], "sec": a.get("section"), "by": gss["method"], "conf": gss["confidence"]})
    conn.execute(text("DELETE FROM gl_actual WHERE tenant_id = :t"), {"t": tid})
    agg: dict[tuple[date, str], float] = defaultdict(float)
    for r in parsed["amounts"]:
        agg[(r["month"], r["account"][:60])] += r["amount"]
    conn.execute(text("INSERT INTO gl_actual (tenant_id, month, account, amount) VALUES (:t, :m, :a, :v)"),
                 [{"t": tid, "m": m, "a": a, "v": round(v, 2)} for (m, a), v in agg.items()])
    derive_spend_from_gl(conn, tid)
    return {"accounts": len(accts), "months": len({m for m, _ in agg}), "rows": len(agg),
            "needs_review": low_conf}


def derive_spend_from_gl(conn: Connection, tid: str) -> None:
    """Monthly S&M and other opex from the books; these become the totals the metrics use."""
    conn.execute(text("DELETE FROM spend WHERE tenant_id = :t AND source = 'gl'"), {"t": tid})
    conn.execute(text("""
        INSERT INTO spend (ts, tenant_id, category, channel, amount, source)
        SELECT a.month::timestamptz, :t,
               CASE WHEN g.pl_group = 'sm' THEN 'sm' ELSE 'other_opex' END,
               CASE WHEN g.pl_group = 'sm' AND g.kind = 'payroll' THEN 'sales_team'
                    WHEN g.pl_group = 'sm' THEN 'gl_' || g.code ELSE 'g_and_a_rnd' END,
               sum(a.amount), 'gl'
        FROM gl_actual a JOIN gl_account g ON g.tenant_id = a.tenant_id AND g.code = a.account
        WHERE a.tenant_id = :t AND g.pl_group IN ('sm', 'rnd', 'ga')
        GROUP BY 1, 3, 4"""), {"t": tid})


def write_cash(conn: Connection, tid: str, rows: list[dict]) -> dict:
    conn.execute(text("DELETE FROM cash_balance WHERE tenant_id = :t"), {"t": tid})
    conn.execute(text("INSERT INTO cash_balance (tenant_id, month, cash) VALUES (:t, :m, :c)"),
                 [{"t": tid, "m": r["month"], "c": r["cash"]} for r in rows])
    return {"months": len(rows)}


# ------------------------------------------------------------------ people + plan
def write_payroll(conn: Connection, tid: str, roster: list[dict]) -> dict:
    cap = last_closed_month()
    conn.execute(text("DELETE FROM payroll WHERE tenant_id = :t"), {"t": tid})
    conn.execute(text("UPDATE plan_hire SET employee_id = NULL WHERE tenant_id = :t"), {"t": tid})
    conn.execute(text("DELETE FROM employee WHERE tenant_id = :t"), {"t": tid})
    pay, seen = [], defaultdict(int)
    for e in roster:
        k = f"employee:{e['name']}:{e['title']}:{e['start_date']}"
        seen[k] += 1  # two people can share a name, title and start date; keep them apart, stably
        eid = _id(tid, f"{k}:{seen[k]}")
        e["id"] = eid
        conn.execute(text("""INSERT INTO employee (id, tenant_id, name, title, department, location, start_date,
                                                   end_date, salary_annual)
                             VALUES (:id, :t, :name, :title, :dept, :loc, :s, :e, :sal)"""),
                     {"id": eid, "t": tid, "name": e["name"], "title": e["title"], "dept": e["department"],
                      "loc": e["location"], "s": e["start_date"], "e": e["end_date"], "sal": e["salary_annual"]})
        m = e["start_date"].replace(day=1)
        while m <= cap and (e["end_date"] is None or m < e["end_date"]):
            pay.append({"t": tid, "m": m, "e": eid, "v": round(e["salary_annual"] * e["loaded_factor"] / 12, 2)})
            m = _add(m, 1)
    if pay:
        conn.execute(text("INSERT INTO payroll (tenant_id, month, employee_id, amount) VALUES (:t, :m, :e, :v)"), pay)
    rebuild_plan_payroll(conn, tid)
    return {"employees": len(roster), "payroll_rows": len(pay)}


def _plan(conn: Connection, tid: str, fiscal_year: int | None = None) -> dict | None:
    q = "SELECT id, fiscal_year FROM plan WHERE tenant_id = :t" + (" AND fiscal_year = :fy" if fiscal_year else "") + \
        " ORDER BY fiscal_year DESC LIMIT 1"
    r = conn.execute(text(q), {"t": tid, "fy": fiscal_year}).mappings().first()
    return dict(r) if r else None


def write_budget(conn: Connection, tid: str, rows: list[dict]) -> dict:
    by_year = defaultdict(int)
    for r in rows:
        by_year[r["month"].year] += 1
    fy = max(by_year, key=by_year.get)
    pid = f"plan_fy{fy}_{tid.replace('-', '')[:12]}"
    codes = {c: c for (c,) in conn.execute(text("SELECT code FROM gl_account WHERE tenant_id = :t"), {"t": tid})}
    names = {n.lower(): c for c, n in conn.execute(text("SELECT code, name FROM gl_account WHERE tenant_id = :t"), {"t": tid})}
    unmatched, lines = set(), defaultdict(float)
    for r in rows:
        if r["month"].year != fy:
            continue
        code = codes.get(r["account"]) or names.get(r["account"].lower())
        if code is None:
            unmatched.add(r["account"])
            continue
        lines[(r["month"], code)] += r["amount"]
    if not lines:
        raise ValueError("None of the budget's accounts match the chart of accounts. Upload the P&L first, "
                         "and use the same account names or codes in the budget.")
    conn.execute(text("""INSERT INTO plan (id, tenant_id, name, fiscal_year, approved_on, assumptions)
                         VALUES (:id, :t, :n, :fy, :ap, '{}'::jsonb)
                         ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"""),
                 {"id": pid, "t": tid, "n": f"FY{fy} operating plan", "fy": fy, "ap": date(fy - 1, 12, 1)})
    conn.execute(text("DELETE FROM plan_line WHERE plan_id = :p"), {"p": pid})
    conn.execute(text("INSERT INTO plan_line (tenant_id, plan_id, month, account, amount) VALUES (:t, :p, :m, :a, :v)"),
                 [{"t": tid, "p": pid, "m": m, "a": a, "v": round(v, 2)} for (m, a), v in lines.items()])
    rebuild_plan_payroll(conn, tid)
    return {"plan": f"FY{fy}", "lines": len(lines), "unmatched_accounts": sorted(unmatched)}


def write_hiring_plan(conn: Connection, tid: str, hires: list[dict]) -> dict:
    p = _plan(conn, tid)
    if p is None:
        raise ValueError("Upload the annual budget first; the hiring plan belongs to it.")
    conn.execute(text("DELETE FROM plan_hire WHERE plan_id = :p"), {"p": p["id"]})
    for i, h in enumerate(hires):
        conn.execute(text("""INSERT INTO plan_hire (id, tenant_id, plan_id, role, department, location, planned_start,
                                                    salary_annual, status)
                             VALUES (:id, :t, :p, :role, :dept, :loc, :start, :sal, 'planned')"""),
                     {"id": _id(tid, f"hire:{p['id']}:{i}:{h['role']}"), "t": tid, "p": p["id"], "role": h["role"],
                      "dept": h["department"], "loc": h["location"], "start": h["planned_start"],
                      "sal": h["salary_annual"]})
    rebuild_plan_payroll(conn, tid)
    return {"hires": len(hires)}


def rebuild_plan_payroll(conn: Connection, tid: str) -> None:
    """Person-level payroll budget: staff on board at plan time + planned hires. Links hires to the people who
    filled them (same role and department, started within 6 months of the planned month)."""
    p = _plan(conn, tid)
    if p is None:
        return
    fy = p["fiscal_year"]
    fy_start, plan_time = date(fy, 1, 1), date(fy - 1, 12, 1)
    months = [_add(fy_start, k) for k in range(12)]
    emps = [dict(r) for r in conn.execute(text("""SELECT id::text, title, department, location, start_date, end_date,
                                                         salary_annual::float FROM employee WHERE tenant_id = :t"""),
                                          {"t": tid}).mappings()]
    hires = [dict(r) for r in conn.execute(text("""SELECT id::text, role, department, location, planned_start,
                                                          salary_annual::float FROM plan_hire WHERE plan_id = :p
                                                   ORDER BY planned_start"""), {"p": p["id"]}).mappings()]
    loaded = {r[0]: float(r[1]) for r in conn.execute(text("""
        SELECT employee_id::text, avg(amount) * 12 / NULLIF(max(e.salary_annual), 0) FROM payroll p
        JOIN employee e ON e.id = p.employee_id WHERE p.tenant_id = :t GROUP BY 1"""), {"t": tid}) if r[1]}
    default_lf = (sum(loaded.values()) / len(loaded)) if loaded else 1.2
    rows = []
    on_board = [e for e in emps if e["start_date"] <= plan_time and (e["end_date"] is None or e["end_date"] > plan_time)]
    for e in on_board:
        lf = loaded.get(e["id"], default_lf)
        rows += [{"m": m, "rt": "employee", "rid": e["id"], "d": e["department"], "v": round(e["salary_annual"] * lf / 12, 2)}
                 for m in months]
    taken: set[str] = set()
    for h in hires:
        rows += [{"m": m, "rt": "hire", "rid": h["id"], "d": h["department"],
                  "v": round(h["salary_annual"] * default_lf / 12, 2)} for m in months if m >= h["planned_start"]]
        match = next((e for e in sorted(emps, key=lambda e: e["start_date"])
                      if e["id"] not in taken and e["department"] == h["department"]
                      and e["title"].lower() == h["role"].lower() and e["start_date"] > plan_time
                      and 0 <= (e["start_date"].year - h["planned_start"].year) * 12
                      + e["start_date"].month - h["planned_start"].month <= 6), None)
        if match:
            taken.add(match["id"])
        conn.execute(text("UPDATE plan_hire SET employee_id = :e, status = :s WHERE id = :id"),
                     {"e": match["id"] if match else None, "s": "started" if match else "planned", "id": h["id"]})
    conn.execute(text("DELETE FROM plan_payroll WHERE plan_id = :p"), {"p": p["id"]})
    if rows:
        conn.execute(text("""INSERT INTO plan_payroll (tenant_id, plan_id, month, ref_type, ref_id, department, amount)
                             VALUES (:t, :p, :m, :rt, :rid, :d, :v)"""), [{**r, "t": tid, "p": p["id"]} for r in rows])


# ------------------------------------------------------------------ marketing, feedback, usage
def write_spend(conn: Connection, tid: str, rows: list[dict], channels: list[str] | None = None) -> dict:
    """CSV upload replaces all ad spend; an ad-platform sync replaces only its own channels (so Google Ads and
    Meta can sync independently)."""
    if channels is None:
        conn.execute(text("DELETE FROM spend WHERE tenant_id = :t AND source IN ('ads', 'demo')"), {"t": tid})
    else:
        conn.execute(text("""DELETE FROM spend WHERE tenant_id = :t AND (source = 'demo'
                             OR (source = 'ads' AND channel = ANY(:c)))"""), {"t": tid, "c": channels})
    agg: dict[tuple[date, str], float] = defaultdict(float)
    for r in rows:
        agg[(r["date"], r["channel"])] += r["amount"]
    conn.execute(text("""INSERT INTO spend (ts, tenant_id, category, channel, amount, source)
                         VALUES (:ts, :t, 'sm', :ch, :v, 'ads')"""),
                 [{"ts": datetime(d.year, d.month, d.day, tzinfo=UTC), "t": tid, "ch": c, "v": round(v, 2)}
                  for (d, c), v in agg.items()])
    return {"days": len({d for d, _ in agg}), "channels": sorted({c for _, c in agg})}


def _account_lookup(conn: Connection, tid: str) -> dict[str, str]:
    m = _account_ids_by_external(conn, tid)
    for a, d in conn.execute(text("SELECT id::text, lower(domain) FROM account WHERE tenant_id = :t AND domain IS NOT NULL"),
                             {"t": tid}):
        m.setdefault(d, a)
    return m


def write_feedback(conn: Connection, tid: str, rows: list[dict], source: str = "csv") -> dict:
    look = _account_lookup(conn, tid)
    conn.execute(text("DELETE FROM feedback WHERE tenant_id = :t AND source = :s"), {"t": tid, "s": source})
    recs = [{"id": _id(tid, f"fb:{source}:{i}:{r['date']}:{r['text'][:40]}"), "t": tid, "src": source,
             "a": look.get((r["customer_id"] or "").strip()) or look.get((r["customer_id"] or "").lower()),
             "ts": datetime(r["date"].year, r["date"].month, r["date"].day, tzinfo=UTC), "k": r["kind"],
             "s": r["score"], "b": r["text"][:2000]} for i, r in enumerate(rows)]
    if recs:
        conn.execute(text("""INSERT INTO feedback (id, tenant_id, account_id, created_at, kind, score, body, source)
                             VALUES (:id, :t, :a, :ts, :k, :s, :b, :src)"""), recs)
    return {"comments": len(recs), "linked_to_accounts": sum(1 for r in recs if r["a"])}


def write_usage(conn: Connection, tid: str, rows: list[dict]) -> dict:
    look = _account_lookup(conn, tid)
    conn.execute(text("DELETE FROM usage_weekly WHERE tenant_id = :t"), {"t": tid})
    recs, unknown = {}, set()
    for r in rows:
        a = look.get(r["customer_id"]) or look.get(r["customer_id"].lower())
        if not a:
            unknown.add(r["customer_id"])
            continue
        recs[(a, r["week"])] = {"t": tid, "a": a, "w": r["week"], "s": r["seats"], "au": r["active_users"], "e": r["events"]}
    if recs:
        conn.execute(text("""INSERT INTO usage_weekly (tenant_id, account_id, week, seats, active_users, events)
                             VALUES (:t, :a, :w, :s, :au, :e)"""), list(recs.values()))
    return {"rows": len(recs), "unmatched_customers": len(unknown)}


# ------------------------------------------------------------------ sync log
def start_run(conn: Connection, tid: str, source: str, user_id: str | None) -> str:
    return conn.execute(text("""INSERT INTO sync_run (tenant_id, source, status, created_by) VALUES (:t, :s, 'running', :u)
                                RETURNING id::text"""), {"t": tid, "s": source, "u": user_id}).scalar()


def finish_run(conn: Connection, run_id: str, ok: bool, stats: dict | None = None, warnings: list[str] | None = None,
               error: str | None = None) -> None:
    conn.execute(text("""UPDATE sync_run SET status = :st, finished_at = now(), stats = CAST(:stats AS JSONB),
                         warnings = CAST(:w AS JSONB), error = :e WHERE id = :id"""),
                 {"st": "ok" if ok else "error", "stats": json.dumps(stats or {}, default=str),
                  "w": json.dumps(warnings or []), "e": error, "id": run_id})

