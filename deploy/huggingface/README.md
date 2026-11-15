---
title: Fred
emoji: 📈
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 10000
pinned: false
short_description: Your analyst team, as software
---

# Fred

The analysis work of BizOps, business analysis, market research, lead qualification and marketing analysis, on
your own data. Source: https://github.com/devdaveddev/fred

This Space runs the single-service image (website + API). Set these in **Settings → Variables and secrets**:

| Secret | Value |
|---|---|
| `DATABASE_URL` | your Neon (or any Postgres) connection string |
| `FRED_SECRET_KEY` | any long random string; keep it stable |
| `PUBLIC_URL` | `https://<your-space-subdomain>.hf.space` |
| `FRED_TIMESCALE` | `off` (plain Postgres tables; nothing Fred needs) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | optional: email |
| `ANTHROPIC_API_KEY` | optional: the crew writes emails with Claude |
