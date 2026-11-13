"""Digest rules (only news), rendering, channel safety, and scheduling maths. No database or network."""
from datetime import UTC, date, datetime, timedelta
from typing import ClassVar

import httpx
import pytest

from app.notify import channels, digest, jobs
from app.team import Finding

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
TODAY = NOW.date()


def f(fid, sev="high", role="business_analyst", impact="ARR at risk", title=None):
    return Finding(id=fid, role=role, severity=sev, title=title or f"T {fid}.", summary="", method="rule", actions=[],
                   impact_usd=120_400, impact_label=impact, link="/accounts")


def task(tid, state, notified=None, due=TODAY + timedelta(days=3), owner="Priya Shah", status="open"):
    return {"id": tid, "title": f"Task {tid}", "owner": owner, "status": status, "due_date": due,
            "notified_state": notified, "progress": {"state": state, "text": f"{state} text"}}


def build(findings=(), alerted=None, tasks=(), failed=()):
    return digest.build("Testco", "https://fred.example.com/", list(findings), alerted or {}, list(tasks),
                        list(failed), TODAY, NOW)


def test_only_unseen_material_findings_are_news():
    d = build([f("a"), f("b"), f("c", sev="low"), f("d", sev="info")], alerted={"b": NOW - timedelta(days=1)})
    assert [x["id"] for x in d.findings] == ["a"]
    assert d.findings[0]["title"] == "T a" and d.findings[0]["impact"] == "$120K ARR at risk" and d.findings[0]["url"] == "https://fred.example.com/accounts"
    assert d.findings[0]["role"] == "Business analyst"


def test_finding_that_went_away_and_came_back_is_news_again():
    d = build([f("b")], alerted={"b": NOW - timedelta(days=20)})
    assert [x["id"] for x in d.findings] == ["b"]


def test_findings_are_capped_with_a_count_of_the_rest():
    d = build([f(str(i)) for i in range(8)])
    assert len(d.findings) == digest.MAX_FINDINGS and d.more_findings == 3
    assert len(d.finding_ids) == 8  # all eight count as told once delivered
    assert d.counts().startswith("8 new findings")


def test_task_progress_is_news_only_when_state_changes():
    d = build(tasks=[task("1", "improving"), task("2", "improving", notified="improving"), task("3", "flat"),
                     task("4", "worse", status="done"), task("5", "resolved", notified="worse")])
    assert [t["id"] for t in d.task_updates] == ["1", "5"]


def test_overdue_reminder_day_after_then_weekly():
    late = {n: task(str(n), "flat", due=TODAY - timedelta(days=n)) for n in (1, 2, 7, 8, 15)}
    d = build(tasks=late.values())
    assert sorted(t["days"] for t in d.overdue) == [1, 8, 15]


def test_nothing_new_means_empty_digest():
    d = build([f("a", sev="low")], tasks=[task("1", "flat")])
    assert d.empty and d.counts() == "nothing new"


def test_sync_failures_are_reported_with_a_fix_link():
    d = build(failed=[{"source": "stripe", "error": "Stripe rejected the key."},
                      {"source": "csv:hiring_plan", "error": None}])
    assert [p["source"] for p in d.sync_problems] == ["Stripe", "hiring plan upload"]
    assert d.sync_problems[1]["error"] == "Unknown error" and not d.empty


def test_impact_amount_only_when_the_headline_has_none():
    assert digest.impact(f("a", title="Revenue is $380K below plan")) is None
    assert digest.impact(f("a")) == "$120K ARR at risk"
    assert digest.money(1_250_000) == "$1.2M" and digest.money(-46_000) == "-$46K" and digest.money(900, "EUR") == "EUR 900"


def test_slack_render_escapes_and_links():
    d = build([f("a", impact="<b>&</b>")], tasks=[task("1", "worse")])
    payload = digest.render_slack(d)
    body = str(payload["blocks"])
    assert "&lt;b&gt;&amp;&lt;/b&gt;" in body and "<b>" not in body
    assert "<https://fred.example.com/accounts|T a>" in body
    assert payload["text"].startswith("Fred · Testco: 1 new finding, 1 task update")


def test_email_puts_the_readers_own_tasks_first():
    d = build([f("a")], tasks=[task("1", "worse", owner="priya shah"), task("2", "improving", owner="Sam")])
    subject, text_body, html_body = digest.render_email(d, "Priya Shah", {"Priya Shah", "priya@x.test"})
    assert subject == "Testco: 1 new finding, 2 task updates"
    assert text_body.index("Your tasks") < text_body.index("New") < text_body.index("Is the fix working?")
    assert "Task 1" in text_body.split("New")[0] and "Task 2" not in text_body.split("New")[0]
    assert "settings/notifications" in html_body and "Hi Priya" in html_body


def test_email_escapes_html():
    d = build([f("a")])
    d.findings[0]["title"] = "<script>x</script>"
    _, _, html_body = digest.render_email(d, "A", set())
    assert "<script>" not in html_body


@pytest.mark.parametrize("url", ["http://hooks.slack.com/services/T0/B0/xyz123456",
                                 "https://evil.example.com/services/T0/B0/xyz",
                                 "https://hooks.slack.com.evil.com/services/T0/B0/xyz",
                                 "https://localhost/services/T0/B0/xyz12345678",
                                 "https://hooks.slack.com/x"])
def test_webhook_must_be_slack(url):
    with pytest.raises(channels.DeliveryError):
        channels.check_webhook(url)


def test_valid_webhook_accepted():
    url = "https://hooks.slack.com/services/T000/B000/abcdefghijkl"
    assert channels.check_webhook(f"  {url} ") == url


def test_send_slack_success_and_revoked_hook():
    seen = []

    def ok(req):
        seen.append(req)
        return httpx.Response(200, text="ok")

    channels.send_slack("https://hooks.slack.com/services/a/b/c", {"text": "hi"}, transport=httpx.MockTransport(ok))
    assert seen[0].method == "POST" and b'"text":"hi"' in seen[0].content.replace(b" ", b"")
    gone = httpx.MockTransport(lambda r: httpx.Response(404, text="no_service"))
    with pytest.raises(channels.DeliveryError, match="add a new one"):
        channels.send_slack("https://hooks.slack.com/services/a/b/c", {"text": "hi"}, transport=gone)


class FakeSMTP:
    sent: ClassVar[list] = []

    def __init__(self, host, port):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def test_send_email_builds_multipart(monkeypatch):
    monkeypatch.setattr(channels.get_settings(), "smtp_host", "smtp.test")
    channels.send_email("a@x.test", "Subj", "plain", "<p>html</p>", smtp_factory=FakeSMTP)
    msg = FakeSMTP.sent[-1]
    assert msg["To"] == "a@x.test" and msg["Auto-Submitted"] == "auto-generated"
    assert {p.get_content_type() for p in msg.iter_parts()} == {"text/plain", "text/html"}


def test_email_off_without_smtp_host(monkeypatch):
    monkeypatch.setattr(channels.get_settings(), "smtp_host", None)
    assert not channels.email_available()
    with pytest.raises(channels.DeliveryError, match="SMTP_HOST"):
        channels.send_email("a@x.test", "s", "t")


def test_due_period_uses_local_time():
    # 09:00 UTC is 14:30 in Kolkata, 04:00 in New York
    assert jobs.due_period(NOW, 8, "UTC") == "2026-11-13"
    assert jobs.due_period(NOW, 8, "America/New_York") is None
    assert jobs.due_period(NOW, 14, "Asia/Kolkata") == "2026-11-13"
    assert jobs.due_period(datetime(2026, 11, 13, 20, 0, tzinfo=UTC), 8, "Asia/Kolkata") is None  # 01:30 local
    assert jobs.due_period(datetime(2026, 11, 14, 3, 0, tzinfo=UTC), 8, "Asia/Kolkata") == "2026-11-14"
    assert jobs.due_period(NOW, 8, "Not/AZone") == "2026-11-13"  # unknown zone falls back to UTC


def test_next_run():
    assert jobs.next_run(NOW, 8, "UTC", done_today=True) == datetime(2026, 11, 14, 8, 0, tzinfo=jobs.zone("UTC"))
    assert jobs.next_run(NOW, 8, "UTC", done_today=False) == NOW  # due now
    assert jobs.next_run(NOW, 11, "UTC", done_today=False).hour == 11
    assert isinstance(TODAY, date)
