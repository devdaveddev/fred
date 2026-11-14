"""Turn planned actions into emails.

Two writers:
  template  plain, deterministic wording (always available, costs nothing)
  crewai    one CrewAI agent per role writes its own messages, using Claude through CrewAI's Anthropic provider,
            when ANTHROPIC_API_KEY is set. The agent only gets the action's facts. Every number it writes must
            appear in those facts, or the message is discarded and the template is used instead.
"""
from __future__ import annotations

import json
import logging
import os
import re

from ..config import get_settings
from ..deliverables.facts import NUM_RE, _norm
from ..modules import MODULES
from .planner import Action

log = logging.getLogger("fred.agents")
SUBJECT = {"new_task": "New for you: {finding}", "due_soon": "Due {due}: {task}", "overdue": "Overdue: {task}",
           "worse": "Getting worse: {task}", "weekly": "Your week: {role}"}


# ------------------------------------------------------------------ template writer
def template(a: Action, base_url: str) -> tuple[str, str]:
    f = a.facts
    first = a.recipient_name.split()[0] if a.recipient_name else "there"
    agent = f"{MODULES[a.role].title} agent" if a.role in MODULES else "Fred"
    link = base_url.rstrip("/") + f.get("link", "/")
    if a.kind == "new_task":
        steps = "".join(f"\n  - {s}" for s in f.get("next_steps") or [])
        body = (f"Hi {first},\n\nI found something that needs an owner, and I've put it on your list:\n\n"
                f"{f['finding']}\n{f['why']}\n" + (f"At stake: {f['impact']}\n" if f.get("impact") else "")
                + (f"\nSuggested next steps:{steps}\n" if steps else "") + f"\nDue {f['due']}. Details: {link}")
    elif a.kind == "due_soon":
        body = f"Hi {first},\n\nA reminder that \"{f['task']}\" is due {f['due']}.\n\nUpdate it here: {link}"
    elif a.kind == "overdue":
        body = (f"Hi {first},\n\n\"{f['task']}\" was due {f['due']} and is still open. If it's done, mark it done; "
                f"if the plan changed, move the date.\n\n{link}")
    elif a.kind == "worse":
        body = (f"Hi {first},\n\nThe latest data shows \"{f['task']}\" moving the wrong way. {f.get('measured') or ''}"
                f"\n\nWorth a look: {link}")
    else:
        lines = [f"Hi {first},", "", f"Here's where things stand for {f['role']}."]
        if f["findings"]:
            lines += ["", "What I'm watching:", *[f"  - {x}" for x in f["findings"]]]
        if f["open_tasks"]:
            lines += ["", "Open follow-ups:", *[f"  - {x}" for x in f["open_tasks"]]]
        lines += ["", f"Today's briefing: {link}"]
        body = "\n".join(lines)
    subject = SUBJECT[a.kind].format(**{k: v for k, v in f.items() if isinstance(v, str)})
    return subject[:200], body + f"\n\n{agent}, on behalf of your Fred workspace"


# ------------------------------------------------------------------ number guard
def untraced(text: str, facts: dict) -> list[str]:
    """Numbers in text that don't appear in the facts (dates, amounts, counts all count as numbers)."""
    allowed = {_norm(t) for t in NUM_RE.findall(json.dumps(facts, default=str))}
    allowed |= {_norm(t) for t in re.findall(r"\d+", json.dumps(facts, default=str))}
    return sorted({t for t in NUM_RE.findall(text) if _norm(t) not in allowed})


# ------------------------------------------------------------------ CrewAI writer
def llm_enabled() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) and get_settings().agent_llm.startswith("anthropic/")


def _llm():
    """The model used by every agent. Tests swap this for an offline stand-in."""
    from crewai import LLM

    return LLM(model=get_settings().agent_llm, temperature=0.2, max_tokens=700)


def _parse(raw: str) -> tuple[str, str] | None:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    subject, body = str(d.get("subject", "")).strip(), str(d.get("body", "")).strip()
    return (subject[:200], body) if subject and body else None


def crew_write(actions: list[Action], base_url: str, llm=None) -> dict[int, tuple[str, str]]:
    """One Crew, one agent per role involved, one task per message. Returns {action index: (subject, body)} for
    the messages that passed the number guard."""
    os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")  # no usage data leaves the server
    os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    from crewai import Agent, Crew, Process, Task

    llm = llm or _llm()
    agents: dict[str, Agent] = {}
    tasks: list[Task] = []
    for a in actions:
        if a.role not in agents:
            mod = MODULES.get(a.role)
            agents[a.role] = Agent(
                role=f"{mod.title if mod else a.role} agent",
                goal=f"Keep the work moving: {mod.tagline if mod else ''} Write short, specific emails a busy "
                     "colleague acts on.",
                backstory=(f"You do the work of a {mod.replaces if mod else 'analyst'} inside the Fred workspace. "
                           "You never invent facts or numbers: you only use the facts you're given."),
                llm=llm, allow_delegation=False, verbose=False, max_iter=2)
        tasks.append(Task(
            description=(
                f"Write one {a.kind.replace('_', ' ')} email to {a.recipient_name}.\n"
                f"Facts (the only information you may use):\n{json.dumps(a.facts, default=str, indent=1)}\n"
                f"Link to include: {base_url.rstrip('/')}{a.facts.get('link', '/')}\n"
                "Rules: under 120 words; plain text; first name greeting; no number, date or amount that isn't in "
                "the facts; no promises; sign off as the agent."),
            expected_output='JSON only: {"subject": "...", "body": "..."}',
            agent=agents[a.role]))
    crew = Crew(agents=list(agents.values()), tasks=tasks, process=Process.sequential, verbose=False)
    crew.kickoff()
    out: dict[int, tuple[str, str]] = {}
    for i, (a, t) in enumerate(zip(actions, tasks)):
        parsed = _parse(getattr(t.output, "raw", "") if t.output else "")
        if not parsed:
            log.warning("agent %s: unreadable output for %s; using the template", a.role, a.kind)
            continue
        bad = untraced(parsed[0] + "\n" + parsed[1], a.facts | {"link": base_url})
        if bad:
            log.warning("agent %s: blocked untraced numbers %s in %s; using the template", a.role, bad, a.kind)
            continue
        out[i] = parsed
    return out


def write_all(actions: list[Action], base_url: str, llm=None) -> list[tuple[str, str, str]]:
    """(subject, body, writer) for each action. Agents write when possible; templates fill every gap."""
    written: dict[int, tuple[str, str]] = {}
    model = get_settings().agent_llm
    if actions and (llm is not None or llm_enabled()):
        try:
            written = crew_write(actions, base_url, llm=llm)
        except Exception as e:  # the crew is an upgrade, never a dependency
            log.warning("crew run failed (%s); using templates", e)
    return [(*written[i], f"crewai:{model}") if i in written else (*template(a, base_url), "template")
            for i, a in enumerate(actions)]
