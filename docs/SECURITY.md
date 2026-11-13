# Security and the SOC 2 path

This document says what Fred does today, mapped to the SOC 2 Trust Services Criteria (Security, plus
Confidentiality). It also lists what's still needed before an audit. SOC 2 is an attestation about a *company's*
controls, not about the code alone. Items marked **org** are policies and processes the company running Fred
must put in place; code can't satisfy them.

## Controls in the product

| Area | What Fred does | Where | TSC |
|---|---|---|---|
| Authentication | scrypt password hashing (per-user salt, constant-time compare). Policy: at least 10 characters, common passwords and ones built from the email refused | `app/auth.py` | CC6.1 |
| Single sign-on | OIDC with PKCE, nonce and state. RS256 ID-token signature checked against the provider's JWKS. Checks iss, aud/azp, exp and email_verified. Optional enforcement turns off passwords for the company's domains, and is allowed only after one successful SSO sign-in | `app/sso.py` | CC6.1, CC6.2 |
| SSO isolation | Each email domain is claimed by at most one workspace, and the owner's own domain must be one of them. A session created by SSO can't switch into other workspaces | `app/sso.py`, `auth.switch` | CC6.1 |
| Sessions | 256-bit random token in an httpOnly, SameSite=Lax cookie (Secure in production); only its SHA-256 is stored. Absolute expiry after 30 days. Each person can see and revoke their sessions, or sign out everywhere else. Removing a person ends their sessions in that workspace immediately | `app/auth.py`, `app/security_api.py` | CC6.1, CC6.3 |
| Brute force | Failed sign-ins are throttled per email and IP (5 per 15 minutes), stored in the database, so the limit survives restarts and is shared across API replicas | `login_attempt` | CC6.1 |
| Authorisation | Roles: owner, admin, member. Every query is scoped to the workspace taken from the session, never from the request. Role changes are guarded: only the owner manages admins, and a workspace always keeps one owner | `app/auth.py`, `app/members_api.py` | CC6.1, CC6.3 |
| Access provisioning | Invites by email. The token is single-use, expires in 7 days, and only its hash is stored | `app/members_api.py` | CC6.2 |
| Audit log | Append-only record of sign-ins, failed and blocked sign-ins, invites, role changes, removals, data connections, imports, mapping changes, SSO and notification changes, exports and deletions. Each entry has the actor, IP and user agent. Admins can view and filter it | `app/audit.py`, Settings → Security | CC7.2, CC7.3 |
| Secrets at rest | Stripe and HubSpot keys, Slack webhooks and SSO client secrets are encrypted with Fernet (AES-128-CBC + HMAC), keyed from `FRED_SECRET_KEY`. They are never returned by the API; only the last four characters are shown | `app/connectors/secrets.py` | CC6.1, C1.1 |
| Outbound safety | Slack webhooks are restricted to `hooks.slack.com`. SSO issuers must use https and their discovery document must name the same issuer | `app/notify/channels.py`, `app/sso.py` | CC6.6 |
| Data export | The owner can download every table as CSV. Credentials and token hashes are excluded | Settings → Security | C1.2, P-series |
| Data deletion | The owner deletes the workspace by typing its name. All tenant rows are removed, and accounts with no other workspace are deleted. The deletion itself is logged without tenant data | `security_api.delete_workspace` | C1.2 |
| Transport | Caddy obtains TLS automatically when `SITE_ADDRESS` is a domain. Security headers: nosniff, `X-Frame-Options: DENY`, referrer policy. Cookies are redacted in proxy logs | `deploy/Caddyfile` | CC6.7 |
| Least privilege, infrastructure | Containers run as non-root users. The database is reachable only on the backend network, and the API only through the proxy. A read-only DB role (`fred_readonly`) exists for analytics | `docker-compose.yml`, Dockerfiles | CC6.1, CC6.6 |
| Change management | Schema changes only through versioned migrations. Automated tests (unit, plus round-trip import and SSO attack cases) gate changes | `migrations/`, `tests/` | CC8.1 |
| No LLM on numbers | Figures come only from rules, statistics and models trained on the customer's data. The document verifier blocks any number it can't trace to a receipt | `app/deliverables/facts.py` | PI1 (processing integrity) |

## Gaps before an audit (in order)

1. **org**: Write the policies (information security, access control, incident response, vendor management,
   business continuity, data retention) and have them approved. Run security awareness training and background checks.
2. **org**: Choose a compliance platform (Vanta, Drata, Secureframe) to collect evidence, and an auditor. Start
   a Type I report, then a 3–12 month observation window for Type II.
3. **org/infra**: Production hosting with encrypted disks and backups (managed Postgres with point-in-time
   recovery), restore tests, centralised logs with alerting, and an uptime monitor.
4. **code**: Domain ownership proof for SSO (a DNS TXT record) before a domain claim is accepted. Today a domain
   is accepted if unused and if it matches the owner's own email domain.
5. **code**: SCIM provisioning, so leavers removed in the identity provider lose access automatically. Today
   they lose access at their next SSO sign-in attempt, and existing sessions last until expiry or revocation.
6. **code**: An idle timeout and shorter session lifetime for SSO sessions. MFA for password accounts (TOTP).
7. **code**: Key rotation for `FRED_SECRET_KEY` (re-encrypt stored secrets with a new key). Audit-log retention
   and export to the customer's SIEM.
8. **org**: Annual penetration test, and a vulnerability management process (dependency scanning in CI, a
   patch SLA).

## Operating notes

- Behind another load balancer, configure Caddy `trusted_proxies` so the audit log records the real client IP.
- `FRED_SECRET_KEY` must stay stable. Losing it makes stored credentials unreadable, so people would need to
  reconnect their tools. Keep it in the deployment's secret manager.
