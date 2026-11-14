"""Run the crew for one workspace: gather state, plan, write, then act (or leave drafts for approval).

Modes: off (nothing), draft (everything waits in the Crew inbox for a person to approve), auto (follow-up tasks
are created and emails sent straight away). Emails go only to people in the workspace.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .. import modules
from ..config import get_settings
from ..db import engine
from . import planner, writer

log = logging.getLogger("fred.agents")


def settings_row(conn: Connection, tid: str) -> dict:
    row = conn.execute(text("SELECT mode, owners, weekly_day, last_run_at FROM agent_setting WHERE tenant_id = :t"),
                       {"t": tid}).mappings().first()
    return dict(row) if row else {"mode": "draft", "owners": {}, "weekly_day": 0, "last_run_at": None}


def default_owner(conn: Connection, tid: str) -> str | None:
    return conn.execute(text("""SELECT user_id::text FROM membership WHERE tenant_id = :t AND role = 'owner'
                                ORDER BY created_at LIMIT 1"""), {"t": tid}).scalar()


def _state(conn: Connection, tid: str):
    from .. import team
    from ..team_api import collect_findings
    from ..work_api import _progress
    from ..workspace import load_workspace

    tenant = dict(conn.execute(text("""SELECT id::text AS id, slug, name, currency, gross_margin, annual_wacc,
                                              data_source FROM tenant WHERE id = :t"""), {"t": tid}).mappings().one())
    ws = load_workspace(conn, tenant)
    a = team.get(conn, ws)  # waits for the analysis (runs in the worker process)
    findings, _, _ = collect_findings(conn, a, {"tenant": tenant, "ws": ws})
    current = {f.id: f for f in findings} | {"_snapshot": a.snapshot_id}
    tasks = [dict(r) for r in conn.execute(text("""
        SELECT id::text, finding_id, module, title, owner, owner_user_id::text, due_date, status, baseline_impact,
               baseline_snapshot FROM task WHERE tenant_id = :t AND status IN ('open', 'in_progress')"""),
        {"t": tid}).mappings()]
    for t in tasks:
        t["progress"] = _progress(t, current)
        t["snapshot"] = a.snapshot_id
    members = {r["id"]: {"name": r["name"], "email": r["email"]} for r in conn.execute(text("""
        SELECT u.id::text, u.name, u.email FROM membership m JOIN app_user u ON u.id = m.user_id
        WHERE m.tenant_id = :t"""), {"t": tid}).mappings()}
    roles = {m: modules.MODULES[m].title for m in modules.hired(conn, tid)}
    return tenant, a, findings, tasks, members, roles


def run(tid: str, today: date | None = None, llm=None) -> dict:
    """One crew pass. Safe to call any number of times a day: dedupe keys stop repeats."""
    today = today or date.today()
    with engine.begin() as conn:
        cfg = settings_row(conn, tid)
        if cfg["mode"] == "off":
            return {"skipped": "crew is off"}
        tenant, a, findings, tasks, members, roles = _state(conn, tid)
        done = {r[0] for r in conn.execute(text("SELECT dedupe_key FROM agent_activity WHERE tenant_id = :t"),
                                           {"t": tid})}
        owners = {k: v for k, v in (cfg["owners"] or {}).items() if v in members}
        default = default_owner(conn, tid)  # roles nobody was assigned report to the workspace owner
        owners = {r: owners.get(r) or default for r in roles if owners.get(r) or default}
    actions = planner.plan(today, roles, owners, members, findings, tasks, done, cfg["weekly_day"])
    if not actions:
        _touch(tid)
        return {"actions": 0}
    texts = writer.write_all(actions, get_settings().public_url, llm=llm)
    by_finding = {f.id: f for f in findings}
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for act, (subject, body, how) in zip(actions, texts):
            f = by_finding.get(act.finding_id) if act.finding_id else None
            payload = {"facts": act.facts, "create_task": act.create_task,
                       "baseline": {"impact": f.impact_usd if f else None, "snapshot": a.snapshot_id,
                                    "title": f.title if f else None, "steps": f.actions if f else []}}
            aid = conn.execute(text("""
                INSERT INTO agent_activity (tenant_id, role, kind, status, recipient_user_id, recipient_email, subject,
                                            body, writer, finding_id, task_id, payload, dedupe_key)
                VALUES (:t, :r, :k, 'draft', CAST(:u AS UUID), :e, :s, :b, :w, :f, CAST(:task AS UUID),
                        CAST(:p AS JSONB), :d)
                ON CONFLICT (tenant_id, dedupe_key) DO NOTHING RETURNING id::text"""),
                {"t": tid, "r": act.role, "k": act.kind, "u": act.recipient_user_id, "e": act.recipient_email,
                 "s": subject, "b": body, "w": how, "f": act.finding_id, "task": act.task_id,
                 "p": json.dumps(payload, default=str), "d": act.dedupe_key}).scalar()
            if aid and cfg["mode"] == "auto":
                status = execute(conn, tid, aid, decided_by=None)
                counts[status] = counts.get(status, 0) + 1
            elif aid:
                counts["draft"] = counts.get("draft", 0) + 1
    _touch(tid)
    log.info("crew for %s: %s", tenant["slug"], counts)
    return {"actions": len(actions), **counts}


def _touch(tid: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO agent_setting (tenant_id, last_run_at) VALUES (:t, now())
                             ON CONFLICT (tenant_id) DO UPDATE SET last_run_at = now()"""), {"t": tid})


def execute(conn: Connection, tid: str, activity_id: str, decided_by: str | None) -> str:
    """Carry out one drafted action: create its task (new_task) and send its email. Returns the new status."""
    from ..notify import channels
    from ..work_api import resolve_owner

    row = conn.execute(text("""SELECT * FROM agent_activity WHERE id = CAST(:i AS UUID) AND tenant_id = :t
                               FOR UPDATE"""), {"i": activity_id, "t": tid}).mappings().first()
    if not row or row["status"] != "draft":
        return row["status"] if row else "missing"
    p = row["payload"] or {}
    if row["kind"] == "new_task" and p.get("create_task"):
        ct, base = p["create_task"], p.get("baseline") or {}
        exists = conn.execute(text("""SELECT 1 FROM task WHERE tenant_id = :t AND finding_id = :f
                                      AND status IN ('open','in_progress')"""), {"t": tid, "f": ct["finding_id"]}).first()
        if not exists:
            conn.execute(text("""
                INSERT INTO task (tenant_id, finding_id, module, title, steps, owner, owner_user_id, due_date,
                                  baseline_impact, baseline_snapshot)
                VALUES (:t, :f, :m, :title, CAST(:steps AS JSONB), :o, CAST(:ou AS UUID), :d, :bi, :bs)"""),
                {"t": tid, "f": ct["finding_id"], "m": row["role"], "title": (base.get("title") or row["subject"])[:300],
                 "steps": json.dumps(base.get("steps") or []), "o": ct["owner"],
                 "ou": ct.get("owner_user_id") or resolve_owner(conn, tid, ct["owner"]), "d": ct["due_date"],
                 "bi": base.get("impact"), "bs": base.get("snapshot") or ""})
    status, err = "done", None
    if row["recipient_email"]:
        if channels.email_available():
            try:
                channels.send_email(row["recipient_email"], row["subject"], row["body"])
                status = "sent"
            except channels.DeliveryError as e:
                status, err = "failed", str(e)[:400]
        else:
            err = "Email isn't set up on this server (SMTP_HOST), so nothing was emailed; the task was still created." \
                if row["kind"] == "new_task" else "Email isn't set up on this server (SMTP_HOST), so this wasn't emailed."
            status = "done" if row["kind"] == "new_task" else "failed"
    conn.execute(text("""UPDATE agent_activity SET status = :s, error = :e, decided_at = :at,
                         decided_by = CAST(:by AS UUID) WHERE id = CAST(:i AS UUID)"""),
                 {"s": status, "e": err, "at": datetime.now(UTC), "by": decided_by, "i": activity_id})
    return status
