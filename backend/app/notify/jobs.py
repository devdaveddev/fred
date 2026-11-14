"""Scheduled work: the daily sync of every connected source, then the digest.

Exactly-once across workers: a job runs only if its (workspace, job, period) row could be inserted.
A claim left unfinished for two hours (a worker died mid-job) can be taken over.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text
from sqlalchemy.engine import Connection

from ..config import get_settings
from ..connectors import secrets
from ..db import engine
from . import channels, digest

log = logging.getLogger("fred.notify")
DEFAULTS = {"slack_enc": None, "slack_hint": None, "email_enabled": True, "daily_sync": True, "digest_hour": 8,
            "timezone": "UTC"}
STALE_CLAIM = timedelta(hours=2)


def settings_row(conn: Connection, tid: str) -> dict:
    row = conn.execute(text("""SELECT slack_enc, slack_hint, email_enabled, daily_sync, digest_hour, timezone
                               FROM notify_setting WHERE tenant_id = :t"""), {"t": tid}).mappings().first()
    return dict(row) if row else dict(DEFAULTS)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def due_period(now: datetime, hour: int, tz: str) -> str | None:
    """The local date whose daily run is due at `now`, or None if today's hour hasn't come yet."""
    local = now.astimezone(zone(tz))
    return local.date().isoformat() if local.hour >= hour else None


def next_run(now: datetime, hour: int, tz: str, done_today: bool) -> datetime:
    local = now.astimezone(zone(tz))
    at = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if done_today:
        return at + timedelta(days=1) if local >= at else at
    return max(at, local)  # past the hour and not done yet: due now (next worker pass)


def claim(conn: Connection, tid: str, job: str, period: str) -> bool:
    return conn.execute(text("""
        INSERT INTO scheduled_job (tenant_id, job, period) VALUES (:t, :j, :p)
        ON CONFLICT (tenant_id, job, period) DO UPDATE SET claimed_at = now()
          WHERE scheduled_job.finished_at IS NULL AND scheduled_job.claimed_at < now() - CAST(:stale AS INTERVAL)
        RETURNING 1"""), {"t": tid, "j": job, "p": period, "stale": f"{int(STALE_CLAIM.total_seconds())} seconds"}
    ).first() is not None


def finish(conn: Connection, tid: str, job: str, period: str, outcome: dict) -> None:
    conn.execute(text("""UPDATE scheduled_job SET finished_at = now(), outcome = CAST(:o AS JSONB)
                         WHERE tenant_id = :t AND job = :j AND period = :p"""),
                 {"t": tid, "j": job, "p": period, "o": json.dumps(outcome, default=str)})


# ------------------------------------------------------------------ composing
def _tenant(conn: Connection, tid: str) -> dict | None:
    row = conn.execute(text("""SELECT id::text AS id, slug, name, currency, gross_margin, annual_wacc, data_source
                               FROM tenant WHERE id = :t"""), {"t": tid}).mappings().first()
    return dict(row) if row else None


def compose(conn: Connection, tid: str, now: datetime | None = None, synced: list[str] | None = None,
            touch: bool = True) -> digest.Digest | None:
    """Build today's digest from the live analysis. touch=False for a preview that changes nothing."""
    from .. import team
    from ..team_api import collect_findings
    from ..work_api import _progress
    from ..workspace import load_workspace

    now = now or datetime.now(UTC)
    tenant = _tenant(conn, tid)
    if tenant is None:
        return None
    findings: list = []
    tasks: list[dict] = []
    if conn.execute(text("SELECT 1 FROM subscription_mrr WHERE tenant_id = :t LIMIT 1"), {"t": tid}).first():
        ws = load_workspace(conn, tenant)
        a = team.get(conn, ws)
        findings, _, _ = collect_findings(conn, a, {"tenant": tenant, "ws": ws})
        current = {f.id: f for f in findings} | {"_snapshot": a.snapshot_id}
        tasks = [dict(r) for r in conn.execute(text("""
            SELECT id::text, finding_id, title, owner, owner_user_id::text, due_date, status, baseline_impact,
                   baseline_snapshot, notified_state FROM task WHERE tenant_id = :t AND status IN ('open', 'in_progress')"""),
            {"t": tid}).mappings()]
        for t in tasks:
            t["progress"] = _progress(t, current)
    alerted = {r[0]: r[1] for r in conn.execute(text(
        "SELECT finding_id, last_seen_at FROM alerted_finding WHERE tenant_id = :t"), {"t": tid})}
    since = conn.execute(text("""SELECT max(created_at) FROM notification
                                 WHERE tenant_id = :t AND kind = 'digest' AND status = 'sent'"""), {"t": tid}).scalar()
    since = max(since or now - timedelta(days=1), now - timedelta(days=7))
    # the latest run per source, if it failed after the last digest
    failed = [dict(r) for r in conn.execute(text("""
        SELECT source, error FROM (
          SELECT DISTINCT ON (source) source, status, error, coalesce(finished_at, started_at) AS at
          FROM sync_run WHERE tenant_id = :t ORDER BY source, started_at DESC) last
        WHERE status = 'error' AND at > :since"""), {"t": tid, "since": since}).mappings()]
    tz = settings_row(conn, tid)["timezone"]
    d = digest.build(tenant["name"], get_settings().public_url, findings, alerted, tasks, failed,
                     today=now.astimezone(zone(tz)).date(), now=now, synced=synced, currency=tenant["currency"] or "USD")
    if touch:  # still-present findings stay "seen", so only a real return counts as news again
        present = [f.id for f in findings if f.id in alerted and f.id not in d.finding_ids]
        if present:
            conn.execute(text("""UPDATE alerted_finding SET last_seen_at = now()
                                 WHERE tenant_id = :t AND finding_id = ANY(:ids)"""), {"t": tid, "ids": present})
    d._tasks = tasks  # type: ignore[attr-defined]
    return d


# ------------------------------------------------------------------ delivering
def recipients(conn: Connection, tid: str) -> list[dict]:
    return [dict(r) for r in conn.execute(text("""
        SELECT u.id::text, u.email, u.name FROM membership m JOIN app_user u ON u.id = m.user_id
        WHERE m.tenant_id = :t AND m.email_digest ORDER BY u.email"""), {"t": tid}).mappings()]


def _log(conn: Connection, tid: str, kind: str, channel: str, to: str, subject: str, err: str | None) -> None:
    conn.execute(text("""INSERT INTO notification (tenant_id, kind, channel, recipient, subject, status, error)
                         VALUES (:t, :k, :c, :r, :s, :st, :e)"""),
                 {"t": tid, "k": kind, "c": channel, "r": to, "s": subject[:300], "st": "failed" if err else "sent",
                  "e": err})


def deliver(conn: Connection, tid: str, d: digest.Digest, kind: str = "digest",
            slack_transport=None, smtp_factory=None) -> dict:
    cfg = settings_row(conn, tid)
    sent, failed = [], []
    if cfg["slack_enc"]:
        subject = f"Slack: {d.counts()}"
        try:
            channels.send_slack(secrets.decrypt(cfg["slack_enc"]), digest.render_slack(d), transport=slack_transport)
            sent.append("slack")
            _log(conn, tid, kind, "slack", f"webhook …{cfg['slack_hint']}", subject, None)
        except Exception as e:
            failed.append({"to": "slack", "error": str(e)})
            _log(conn, tid, kind, "slack", f"webhook …{cfg['slack_hint']}", subject, str(e)[:500])
    if cfg["email_enabled"] and (channels.email_available() or smtp_factory):
        for r in recipients(conn, tid):
            subject, body, html = digest.render_email(d, r["name"], {r["name"], r["email"], r["id"]})
            try:
                channels.send_email(r["email"], subject, body, html, smtp_factory=smtp_factory)
                sent.append(r["email"])
                _log(conn, tid, kind, "email", r["email"], subject, None)
            except Exception as e:
                failed.append({"to": r["email"], "error": str(e)})
                _log(conn, tid, kind, "email", r["email"], subject, str(e)[:500])
    if sent and kind == "digest":
        mark_told(conn, tid, d)
    return {"sent": sent, "failed": failed}


def mark_told(conn: Connection, tid: str, d: digest.Digest) -> None:
    if d.finding_ids:
        conn.execute(text("""INSERT INTO alerted_finding (tenant_id, finding_id) SELECT :t, unnest(CAST(:ids AS TEXT[]))
                             ON CONFLICT (tenant_id, finding_id) DO UPDATE SET first_alerted_at = now(),
                               last_seen_at = now()"""), {"t": tid, "ids": d.finding_ids})
    for t in getattr(d, "_tasks", []):
        conn.execute(text("UPDATE task SET notified_state = :s WHERE id = CAST(:id AS UUID) AND tenant_id = :t"),
                     {"s": t["progress"]["state"], "id": t["id"], "t": tid})


def send_digest(tid: str, force: bool = False, synced: list[str] | None = None, **kw) -> dict:
    with engine.begin() as conn:
        d = compose(conn, tid, synced=synced)
        if d is None:
            return {"sent": [], "failed": [], "skipped": "workspace not found"}
        if d.empty and not force:
            return {"sent": [], "failed": [], "skipped": "nothing new"}
        return deliver(conn, tid, d, **kw) | {"summary": d.counts()}


def send_test(tid: str, channel: str, user: dict | None = None, **kw) -> None:
    """A one-line message proving the channel works. Raises DeliveryError with a fixable message."""
    s = get_settings()
    with engine.begin() as conn:
        cfg = settings_row(conn, tid)
        name = (_tenant(conn, tid) or {}).get("name", "your workspace")
        if channel == "slack":
            if not cfg["slack_enc"]:
                raise channels.DeliveryError("Slack is not connected.")
            channels.send_slack(secrets.decrypt(cfg["slack_enc"]), {
                "text": f"Fred is connected to this channel for {name}. The daily digest will arrive here."},
                transport=kw.get("slack_transport"))
            _log(conn, tid, "test", "slack", f"webhook …{cfg['slack_hint']}", "Test message", None)
        else:
            if not user:
                raise channels.DeliveryError("No recipient.")
            channels.send_email(user["email"], f"Fred test email for {name}",
                                f"Email delivery works. Your digest will come from this address.\n\n{s.public_url}/",
                                smtp_factory=kw.get("smtp_factory"))
            _log(conn, tid, "test", "email", user["email"], "Test message", None)


# ------------------------------------------------------------------ the daily job
def run_daily(tid: str) -> dict:
    from ..sources_api import run_sync

    with engine.begin() as conn:
        cfg = settings_row(conn, tid)
        kinds = [r[0] for r in conn.execute(text("SELECT kind FROM connection WHERE tenant_id = :t ORDER BY kind"),
                                            {"t": tid})]
    synced = []
    if cfg["daily_sync"]:
        for kind in kinds:
            run_sync(tid, kind, None)  # records its own success or failure in sync_run
            synced.append(kind)
    out = {"synced": synced, "digest": send_digest(tid, synced=synced)}
    try:  # then the crew: follow-ups, reminders, weekly notes
        from ..agents.runner import run as run_crew

        out["crew"] = run_crew(tid)
    except Exception as e:
        log.exception("crew run failed for %s", tid)
        out["crew"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    return out


def tick(now: datetime | None = None) -> list[dict]:
    """One scheduler pass: run the daily job for every workspace whose local digest hour has come."""
    now = now or datetime.now(UTC)
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT t.id::text, coalesce(n.digest_hour, 8), coalesce(n.timezone, 'UTC') FROM tenant t
            LEFT JOIN notify_setting n ON n.tenant_id = t.id
            WHERE EXISTS (SELECT 1 FROM subscription_mrr m WHERE m.tenant_id = t.id)
               OR EXISTS (SELECT 1 FROM connection c WHERE c.tenant_id = t.id)""")).all()
    done = []
    for tid, hour, tz in rows:
        period = due_period(now, hour, tz)
        if period is None:
            continue
        with engine.begin() as conn:
            if not claim(conn, tid, "daily", period):
                continue
        try:
            outcome = run_daily(tid)
        except Exception as e:
            log.exception("daily job failed for %s", tid)
            outcome = {"error": f"{type(e).__name__}: {e}"[:500]}
        with engine.begin() as conn:
            finish(conn, tid, "daily", period, outcome)
        log.info("daily job for %s (%s): %s", tid, period, outcome)
        done.append({"tenant": tid, "period": period, **outcome})
    return done


def today_done(conn: Connection, tid: str, now: datetime, tz: str) -> bool:
    period = now.astimezone(zone(tz)).date().isoformat()
    return conn.execute(text("""SELECT 1 FROM scheduled_job WHERE tenant_id = :t AND job = 'daily' AND period = :p"""),
                        {"t": tid, "p": period}).first() is not None

