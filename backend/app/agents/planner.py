"""What each role agent should do today. Pure: state in, actions out (no database, no network, no LLM).

Rules (each action carries a dedupe key, so it happens once):
  new_task  an important finding (critical/high) in the agent's area that nobody owns yet -> a follow-up task
            for the role's owner, due in 7 days, with an email
  due_soon  an open task due tomorrow or the day after -> a reminder to its owner
  overdue   an open task past its due date -> a reminder the day after, then every 3 days
  worse     Fred measured a task's problem getting worse since it was assigned -> tell the owner
  weekly    on the chosen weekday, each role's owner gets a short summary of what's open
The agents decide nothing about numbers: every figure in an action's facts comes from Fred's analysis.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

URGENT = {"critical", "high"}
TASK_DAYS = 7
NEW_PER_ROLE = 2  # new follow-ups per role per day, most important first; the rest come on later days


@dataclass
class Action:
    role: str
    kind: str
    recipient_user_id: str
    recipient_name: str
    recipient_email: str
    dedupe_key: str
    facts: dict
    finding_id: str | None = None
    task_id: str | None = None
    create_task: dict | None = None  # for new_task: what to create on send/approve
    extra: dict = field(default_factory=dict)


def plan(today: date, roles: dict[str, str], owners: dict[str, str], members: dict[str, dict], findings: list,
         tasks: list[dict], done_keys: set[str], weekly_day: int = 0) -> list[Action]:
    """roles: hired role id -> title; owners: role id -> user id; members: user id -> {name, email};
    findings: team.Finding (role already mapped to the module id); tasks: open tasks with progress."""
    out: list[Action] = []

    def person(uid: str | None) -> dict | None:
        return members.get(uid) if uid else None

    def add(a: Action) -> None:
        if a.dedupe_key not in done_keys:
            done_keys.add(a.dedupe_key)
            out.append(a)

    tasked = {t["finding_id"] for t in tasks}
    per_role: dict[str, int] = {}
    # 1. new important findings nobody owns (findings arrive most important first)
    for f in findings:
        if f.role not in roles or f.severity not in URGENT or f.id in tasked:
            continue
        if per_role.get(f.role, 0) >= NEW_PER_ROLE or f"new_task:{f.id}" in done_keys:
            continue
        per_role[f.role] = per_role.get(f.role, 0) + 1
        owner = person(owners.get(f.role))
        if not owner:
            continue
        due = today + timedelta(days=TASK_DAYS)
        add(Action(role=f.role, kind="new_task", recipient_user_id=owners[f.role], recipient_name=owner["name"],
                   recipient_email=owner["email"], dedupe_key=f"new_task:{f.id}", finding_id=f.id,
                   facts={"finding": f.title, "why": f.summary, "impact": f.impact_label and
                          (f"{f.impact_label}" if f.impact_usd is None else f"{_money(f.impact_usd)} {f.impact_label}"),
                          "next_steps": f.actions[:3], "due": due.strftime("%A %d %B"), "link": f.link or "/"},
                   create_task={"finding_id": f.id, "owner": owner["name"], "owner_user_id": owners[f.role],
                                "due_date": due.isoformat()}))
    # 2-4. reminders on open tasks
    for t in tasks:
        uid = t.get("owner_user_id") or owners.get(t["module"])
        who = person(uid)
        if not who:
            continue
        role = t["module"]
        base = {"task": t["title"], "due": t["due_date"].strftime("%A %d %B"), "link": "/actions"}
        days = (t["due_date"] - today).days
        if days in (1, 2):
            add(Action(role=role, kind="due_soon", recipient_user_id=uid, recipient_name=who["name"],
                       recipient_email=who["email"], dedupe_key=f"due_soon:{t['id']}:{t['due_date']}", task_id=t["id"],
                       facts=base | {"days_left": days}))
        elif days < 0 and (-days == 1 or (-days - 1) % 3 == 0):
            add(Action(role=role, kind="overdue", recipient_user_id=uid, recipient_name=who["name"],
                       recipient_email=who["email"], dedupe_key=f"overdue:{t['id']}:{today}", task_id=t["id"],
                       facts=base | {"days_late": -days}))
        prog = t.get("progress") or {}
        if prog.get("state") == "worse":
            add(Action(role=role, kind="worse", recipient_user_id=uid, recipient_name=who["name"],
                       recipient_email=who["email"], dedupe_key=f"worse:{t['id']}:{t.get('snapshot', today)}",
                       task_id=t["id"], facts=base | {"measured": prog.get("text")}))
    # 5. weekly note per role owner
    if today.weekday() == weekly_day:
        week = today.isocalendar()
        for role, title in roles.items():
            who = person(owners.get(role))
            if not who:
                continue
            mine = [f for f in findings if f.role == role][:5]
            open_tasks = [t for t in tasks if t["module"] == role]
            if not mine and not open_tasks:
                continue
            add(Action(role=role, kind="weekly", recipient_user_id=owners[role], recipient_name=who["name"],
                       recipient_email=who["email"], dedupe_key=f"weekly:{role}:{week.year}-W{week.week}",
                       facts={"role": title, "findings": [f.title for f in mine],
                              "open_tasks": [f"{t['title']} (due {t['due_date']:%d %b})" for t in open_tasks[:8]],
                              "overdue": sum(1 for t in open_tasks if t["due_date"] < today), "link": "/"}))
    return out


def _money(v: float) -> str:
    a = abs(v)
    s = f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.0f}K" if a >= 1e3 else f"${a:.0f}"
    return ("-" if v < 0 else "") + s
