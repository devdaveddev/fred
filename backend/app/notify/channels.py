"""Delivery: Slack incoming webhooks and SMTP email. No third-party SDKs; both are plain protocols."""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import urlparse

import httpx

from ..config import get_settings


class DeliveryError(RuntimeError):
    pass


def check_webhook(url: str) -> str:
    """Only https URLs on Slack's webhook host are accepted, so the field can't be used to reach other servers."""
    url = url.strip()
    u = urlparse(url)
    if u.scheme != "https" or (u.hostname or "").lower() not in get_settings().slack_webhook_hosts:
        raise DeliveryError("Paste a Slack incoming-webhook URL. It starts with https://hooks.slack.com/services/")
    if not u.path.startswith("/services/") or len(u.path) < 20:
        raise DeliveryError("That doesn't look like a complete webhook URL. Copy it again from Slack.")
    return url


def send_slack(url: str, payload: dict, transport: httpx.BaseTransport | None = None) -> None:
    try:
        with httpx.Client(timeout=10, transport=transport, follow_redirects=False) as http:
            r = http.post(url, json=payload)
    except httpx.HTTPError as e:
        raise DeliveryError(f"Could not reach Slack: {type(e).__name__}") from e
    if r.status_code == 200:
        return
    reason = r.text.strip()[:80]
    if r.status_code in (403, 404, 410) or reason in ("no_service", "channel_not_found", "channel_is_archived",
                                                      "invalid_token", "action_prohibited"):
        raise DeliveryError(f"Slack refused the message ({reason or r.status_code}). The webhook was probably "
                            "removed or its channel archived: add a new one.")
    raise DeliveryError(f"Slack error {r.status_code}: {reason}")


def email_available() -> bool:
    return bool(get_settings().smtp_host)


def send_email(to: str, subject: str, text: str, html: str | None = None, smtp_factory=None) -> None:
    s = get_settings()
    if not s.smtp_host:
        raise DeliveryError("Email is not set up on this server (SMTP_HOST is empty).")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg["Auto-Submitted"] = "auto-generated"  # stops out-of-office loops
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        if smtp_factory:
            server = smtp_factory(s.smtp_host, s.smtp_port)
        elif s.smtp_tls == "ssl":
            server = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
        with server:
            if s.smtp_tls == "starttls" and not smtp_factory:
                server.starttls(context=ssl.create_default_context())
            if s.smtp_user:
                server.login(s.smtp_user, s.smtp_password or "")
            server.send_message(msg)
    except (smtplib.SMTPException, OSError) as e:
        raise DeliveryError(f"Email could not be sent: {type(e).__name__}: {str(e)[:120]}") from e
