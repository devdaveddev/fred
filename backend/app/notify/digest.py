"""The daily digest: only what changed since the team was last told. Nothing new, no message.

Pure functions: build() decides what is news, render_*() turn it into Slack blocks or an email.
What counts as news:
  * a finding (medium severity or above) the team hasn't been told about, or one that went away for
    two weeks and came back
  * a task whose measured progress changed state (improving, getting worse, resolved)
  * a task that became overdue yesterday, then a weekly reminder while it stays overdue
  * a data sync that failed
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .. import modules

NEWS_SEVERITY = {"critical", "high", "medium"}
MAX_FINDINGS = 5
RETURN_AFTER = timedelta(days=14)
PROGRESS_NEWS = {"improving", "worse", "resolved"}
SOURCE_LABEL = {"stripe": "Stripe", "hubspot": "HubSpot"}


@dataclass
class Digest:
    workspace: str
    base_url: str
    findings: list[dict] = field(default_factory=list)
    more_findings: int = 0
    task_updates: list[dict] = field(default_factory=list)
    overdue: list[dict] = field(default_factory=list)
    sync_problems: list[dict] = field(default_factory=list)
    synced: list[str] = field(default_factory=list)
    finding_ids: list[str] = field(default_factory=list)  # to mark as told once delivered

    @property
    def empty(self) -> bool:
        return not (self.findings or self.task_updates or self.overdue or self.sync_problems)

    def counts(self) -> str:
        parts = []
        if self.findings:
            n = len(self.findings) + self.more_findings
            parts.append(f"{n} new finding{'s' * (n != 1)}")
        if self.task_updates:
            parts.append(f"{len(self.task_updates)} task update{'s' * (len(self.task_updates) != 1)}")
        if self.overdue:
            parts.append(f"{len(self.overdue)} overdue")
        if self.sync_problems:
            parts.append("a data sync failed")
        return ", ".join(parts) or "nothing new"


def money(v: float, currency: str = "USD") -> str:
    sign, v = ("-" if v < 0 else ""), abs(v)
    sym = "$" if currency == "USD" else f"{currency} "
    if v >= 1e6:
        return f"{sign}{sym}{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{sign}{sym}{v / 1e3:.0f}K"
    return f"{sign}{sym}{v:.0f}"


def impact(f, currency: str = "USD") -> str | None:
    """'$813K ARR lost to this reason'; skipped when the headline already states the amount."""
    if f.impact_usd is None or not f.impact_label or "$" in f.title:
        return None
    return f"{money(f.impact_usd, currency)} {f.impact_label}"


def source_label(source: str) -> str:
    if source.startswith("csv:"):
        return f"{source[4:].replace('_', ' ')} upload"
    return SOURCE_LABEL.get(source, source)


def build(workspace: str, base_url: str, findings: list, alerted: dict[str, datetime],
          tasks: list[dict], failed_runs: list[dict], today: date, now: datetime,
          synced: list[str] | None = None, currency: str = "USD") -> Digest:
    d = Digest(workspace=workspace, base_url=base_url.rstrip("/"), synced=synced or [])
    fresh = []
    for f in findings:
        if f.severity not in NEWS_SEVERITY:
            continue
        seen = alerted.get(f.id)
        if seen is None or now - seen > RETURN_AFTER:
            fresh.append(f)
    for f in fresh[:MAX_FINDINGS]:
        mod = modules.MODULES.get(f.role)
        d.findings.append({"id": f.id, "title": f.title.rstrip("."), "impact": impact(f, currency), "severity": f.severity,
                           "role": mod.title if mod else f.role, "url": d.base_url + (f.link or "/")})
    d.more_findings = max(0, len(fresh) - MAX_FINDINGS)
    d.finding_ids = [f.id for f in fresh]
    for t in tasks:
        if t["status"] not in ("open", "in_progress"):
            continue
        state = t["progress"]["state"]
        if state in PROGRESS_NEWS and state != t.get("notified_state"):
            d.task_updates.append({"id": t["id"], "title": t["title"], "owner": t["owner"],
                                   "owner_user_id": t.get("owner_user_id"), "state": state,
                                   "text": t["progress"]["text"], "url": d.base_url + "/actions"})
        late = (today - t["due_date"]).days
        if late >= 1 and late % 7 == 1:
            d.overdue.append({"id": t["id"], "title": t["title"], "owner": t["owner"],
                              "owner_user_id": t.get("owner_user_id"), "days": late,
                              "url": d.base_url + "/actions"})
    for r in failed_runs:
        d.sync_problems.append({"source": source_label(r["source"]), "error": (r["error"] or "Unknown error")[:300],
                                "url": d.base_url + "/settings/data"})
    return d


def mine(d: Digest, who: set[str]) -> tuple[list[dict], list[dict]]:
    """The reader's own task updates and overdue tasks (owner matched on name or email, case-insensitive)."""
    who = {w.strip().lower() for w in who if w}
    def is_mine(t: dict) -> bool:
        return t["owner"].strip().lower() in who or (t.get("owner_user_id") or "").lower() in who
    return [t for t in d.task_updates if is_mine(t)], [t for t in d.overdue if is_mine(t)]


# ------------------------------------------------------------------ Slack
STATE_ICON = {"improving": ":chart_with_downwards_trend:", "worse": ":warning:", "resolved": ":white_check_mark:"}
SEV_ICON = {"critical": ":red_circle:", "high": ":large_orange_circle:", "medium": ":large_yellow_circle:"}


def _sl(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_slack(d: Digest) -> dict:
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": f"Fred · {d.workspace}"}},
                          {"type": "context", "elements": [{"type": "mrkdwn", "text": _sl(d.counts().capitalize())}]}]
    if d.findings:
        lines = [f"{SEV_ICON.get(f['severity'], '•')} <{f['url']}|{_sl(f['title'])}>"
                 + (f" · {_sl(f['impact'])}" if f["impact"] else "") + f"  _{_sl(f['role'])}_" for f in d.findings]
        if d.more_findings:
            lines.append(f"…and {d.more_findings} more on <{d.base_url}/|Today>")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*New*\n" + "\n".join(lines)}})
    if d.task_updates:
        lines = [f"{STATE_ICON[t['state']]} *{_sl(t['title'])}* ({_sl(t['owner'])}): {_sl(t['text'])}"
                 for t in d.task_updates]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Is the fix working?*\n" + "\n".join(lines)}})
    if d.overdue:
        lines = [f":hourglass: *{_sl(t['title'])}*, {_sl(t['owner'])}, {t['days']} day{'s' * (t['days'] != 1)} late"
                 for t in d.overdue]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*Overdue*\n" + "\n".join(lines)}})
    if d.sync_problems:
        lines = [f":x: *{_sl(p['source'])}*: {_sl(p['error'])}" for p in d.sync_problems]
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                                                   "text": f"*Data problems* · <{d.base_url}/settings/data|Fix>\n"
                                                           + "\n".join(lines)}})
    blocks.append({"type": "actions", "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": "Open today's briefing"}, "url": d.base_url + "/"}]})
    return {"text": f"Fred · {d.workspace}: {d.counts()}", "blocks": blocks}


# ------------------------------------------------------------------ email
def render_email(d: Digest, reader_name: str, reader_keys: set[str]) -> tuple[str, str, str]:
    my_updates, my_overdue = mine(d, reader_keys)
    subject = f"{d.workspace}: {d.counts()}"
    e = html.escape
    text: list[str] = [f"Hi {reader_name.split()[0] if reader_name else 'there'},", ""]
    parts: list[str] = [f"<p>Hi {e(reader_name.split()[0]) if reader_name else 'there'},</p>"]

    def section(heading: str, rows: list[tuple[str, str, str | None]]) -> None:
        if not rows:
            return
        text.extend([heading, *[f"  - {t}{(' (' + s + ')') if s else ''}  {u}" for t, s, u in rows], ""])
        items = "".join(f'<li style="margin:6px 0"><a href="{e(u)}" style="color:#1f3a8a">{e(t)}</a>'
                        + (f'<br><span style="color:#555">{e(s)}</span>' if s else "") + "</li>" for t, s, u in rows)
        parts.append(f'<h3 style="font-size:15px;margin:22px 0 6px">{e(heading)}</h3>'
                     f'<ul style="padding-left:18px;margin:0">{items}</ul>')

    section("Your tasks", [(t["title"], t["text"], t["url"]) for t in my_updates]
            + [(t["title"], f"{t['days']} day{'s' * (t['days'] != 1)} overdue", t["url"]) for t in my_overdue])
    section("New", [(f["title"], " · ".join(x for x in (f["impact"], f["role"]) if x), f["url"]) for f in d.findings]
            + ([(f"…and {d.more_findings} more", None, d.base_url + "/")] if d.more_findings else []))
    others = [t for t in d.task_updates if t not in my_updates]
    section("Is the fix working?", [(t["title"], f"{t['owner']}: {t['text']}", t["url"]) for t in others])
    section("Overdue", [(t["title"], f"{t['owner']}, {t['days']} days late", t["url"])
                        for t in d.overdue if t not in my_overdue])
    section("Data problems", [(p["source"], p["error"], p["url"]) for p in d.sync_problems])

    settings = d.base_url + "/settings/notifications"
    text += [f"Open today's briefing: {d.base_url}/", "",
             (f"You get this because you're in {d.workspace} on Fred. Change it: {settings}")]
    parts.append(f'<p style="margin:26px 0"><a href="{e(d.base_url)}/" style="background:#1f3a8a;color:#fff;'
                 'padding:10px 16px;border-radius:8px;text-decoration:none">Open today\'s briefing</a></p>'
                 f'<p style="color:#888;font-size:12px">You get this because you\'re in {e(d.workspace)} on Fred. '
                 f'<a href="{e(settings)}" style="color:#888">Change email settings</a></p>')
    body = ('<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;font-size:14px;'
            f'line-height:1.5;color:#1a1a1a;max-width:560px">{"".join(parts)}</div>')
    return subject, "\n".join(text), body
