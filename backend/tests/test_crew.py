"""The crew: what agents decide to do, and that their writing can't introduce numbers. No network, no API key."""
import json
import socket
from datetime import date, timedelta

import pytest

from app.agents import planner, writer
from app.team import Finding

TODAY = date(2026, 11, 16)  # a Monday
ROLES = {"founders_office": "Founder's office", "business_analyst": "Business analyst"}
MEMBERS = {"u1": {"name": "Ana Lopez", "email": "ana@acme.test"}, "u2": {"name": "Bo Ng", "email": "bo@acme.test"}}
OWNERS = {"founders_office": "u1", "business_analyst": "u2"}


def f(fid, role="founders_office", sev="high"):
    return Finding(id=fid, role=role, severity=sev, title=f"Hosting cost is 12% over plan ({fid})",
                   summary="Cost per revenue dollar has run above plan since August.", method="rule",
                   actions=["Review the AWS bill", "Check reserved instances"], impact_usd=124_800,
                   impact_label="a year at this run-rate", link="/finance")


def task(tid, due, role="founders_office", owner="u1", state="flat"):
    return {"id": tid, "finding_id": f"x{tid}", "module": role, "title": f"Task {tid}", "owner": "Ana",
            "owner_user_id": owner, "due_date": due, "status": "open", "progress": {"state": state, "text": "Up 14%."}}


def kinds(actions):
    return sorted((a.kind, a.dedupe_key.split(":")[1]) for a in actions)


def test_new_important_findings_become_owned_tasks():
    acts = planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [f("a"), f("b", sev="medium"), f("c", role="market_researcher")],
                        [task("1", TODAY + timedelta(days=5))], set(), weekly_day=3)
    new = [a for a in acts if a.kind == "new_task"]
    assert [a.finding_id for a in new] == ["a"]  # medium skipped; unhired role skipped
    assert new[0].recipient_email == "ana@acme.test" and new[0].create_task["due_date"] == "2026-11-23"
    assert "$125K a year at this run-rate" == new[0].facts["impact"]


def test_already_tasked_findings_are_left_alone():
    acts = planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [f("a")], [task("1", TODAY + timedelta(days=9)) | {"finding_id": "a"}],
                        set(), weekly_day=3)
    assert not [a for a in acts if a.kind == "new_task"]


@pytest.mark.parametrize("offset,expect", [(1, "due_soon"), (2, "due_soon"), (3, None), (-1, "overdue"), (-2, None),
                                           (-4, "overdue"), (-7, "overdue"), (-8, None)])
def test_reminder_cadence(offset, expect):
    acts = planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [], [task("1", TODAY + timedelta(days=offset))], set(), weekly_day=3)
    assert [a.kind for a in acts] == ([expect] if expect else [])


def test_worse_progress_is_flagged_and_everything_runs_once():
    done: set[str] = set()
    t = [task("1", TODAY + timedelta(days=10), state="worse")]
    first = planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [f("a")], t, done, weekly_day=0)
    assert {a.kind for a in first} == {"worse", "new_task", "weekly"}
    again = planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [f("a")], t, done, weekly_day=0)
    assert again == []  # same day: nothing repeats


def test_weekly_note_only_on_its_day_and_only_with_content():
    t = [task("1", TODAY + timedelta(days=10))]
    assert [a.kind for a in planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [], t, set(), weekly_day=0)] == ["weekly"]
    assert planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [], t, set(), weekly_day=2) == []


def test_no_owner_no_email():
    acts = planner.plan(TODAY, ROLES, {}, MEMBERS, [f("a")], [task("1", TODAY + timedelta(days=1), owner=None)],
                        set(), weekly_day=0)
    assert acts == []


def test_templates_only_use_numbers_from_the_facts():
    acts = planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [f("a")],
                        [task("1", TODAY + timedelta(days=1)), task("2", TODAY - timedelta(days=4)),
                         task("3", TODAY + timedelta(days=12), state="worse")], set(), weekly_day=0)
    assert {a.kind for a in acts} == {"new_task", "due_soon", "overdue", "worse", "weekly"}
    for a in acts:
        subject, body = writer.template(a, "https://fred.example.com")
        assert writer.untraced(subject + "\n" + body, a.facts | {"link": "https://fred.example.com"}) == [], a.kind
        assert body.startswith("Hi Ana") and "agent, on behalf of your Fred workspace" in body


def test_guard_catches_invented_numbers():
    facts = {"task": "Hosting review", "due": "Friday 20 November"}
    assert writer.untraced("Due Friday 20 November.", facts) == []
    assert writer.untraced("This could save $500K, about 30%.", facts) == ["$500K", "30%"]


class Fake:
    """Stand-in model for CrewAI: returns whatever the test scripts, records prompts."""

    def __init__(self, replies):
        from crewai.llms.base_llm import BaseLLM

        replies_iter = iter(replies)
        prompts = self.prompts = []

        class _LLM(BaseLLM):
            def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None,
                     from_agent=None, response_model=None):
                prompts.append(json.dumps(messages, default=str) if not isinstance(messages, str) else messages)
                return "Thought: done.\nFinal Answer: " + json.dumps(next(replies_iter))

            def supports_function_calling(self):
                return False

        self.llm = _LLM(model="offline-test-model")


def test_crewai_agents_write_and_the_guard_blocks_invented_numbers(monkeypatch):
    attempts = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: attempts.append(a) or (_ for _ in ()).throw(OSError()))
    acts = planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [f("a")],
                        [task("1", TODAY + timedelta(days=1), role="business_analyst", owner="u2")], set(), weekly_day=3)
    fake = Fake([
        {"subject": "New for you: hosting costs", "body": "Hi Ana, hosting runs 12% over plan, worth $125K a year. Due Monday 23 November."},
        {"subject": "Reminder", "body": "Hi Bo, this will save us $2M if done today."},  # invented
    ])
    out = writer.write_all(acts, "https://fred.example.com", llm=fake.llm)
    assert out[0][2].startswith("crewai:") and "12% over plan" in out[0][1]
    assert out[1][2] == "template" and "$2M" not in out[1][1]  # blocked, fell back to the template
    assert any("Founder's office agent" in p for p in fake.prompts) and any("Business analyst agent" in p for p in fake.prompts)
    assert attempts == []  # CrewAI made no network calls (telemetry off)


def test_crew_failure_falls_back_to_templates():
    acts = planner.plan(TODAY, ROLES, OWNERS, MEMBERS, [f("a")], [], set(), weekly_day=3)

    class Broken:
        pass

    out = writer.write_all(acts, "https://fred.example.com", llm=Broken())
    assert [w for _, _, w in out] == ["template"]


def test_new_follow_ups_are_capped_per_role_per_day():
    fs = [f(c) for c in "abcde"]
    day1 = planner.plan(TODAY, ROLES, OWNERS, MEMBERS, fs, [], set(), weekly_day=3)
    assert [a.finding_id for a in day1 if a.kind == "new_task"] == ["a", "b"]
    done = {a.dedupe_key for a in day1}
    day2 = planner.plan(TODAY + timedelta(days=1), ROLES, OWNERS, MEMBERS, fs,
                        [task(x, TODAY + timedelta(days=8)) | {"finding_id": x} for x in "ab"], done, weekly_day=3)
    assert [a.finding_id for a in day2 if a.kind == "new_task"] == ["c", "d"]
