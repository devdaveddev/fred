# Fred: revenue intelligence

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/devdaveddev/fred)

The analyst between your CRO and your CFO. Every number has a receipt.

- **Today:** a ranked briefing from four analyst roles (marketing analyst, lead generation, founder's
  office, growth strategist). Every finding names its method (rule, statistics, ML model, optimizer),
  its dollar impact, actions and receipts. Zero LLM calls.
- **Leads:** every open lead scored by a calibrated model, explained, valued and routed; ICP learned from data.
- **At-risk accounts:** churn odds from product usage, with reasons and a play per account.
- **Marketing:** channel CAC and payback, spend response curves, same-budget reallocation, spend anomaly QA.
- **Plan & forecast:** backtested forecast; goal planner with spend and retention levers and cash checks.
- **Models & knowledge:** model cards, method policy, versioned benchmarks and playbooks.
- **Metrics:** 14 revenue health metrics (ARR, NRR, GRR, CAC payback, WACC-adjusted LTV:CAC,
  Magic Number, burn multiple, runway and more), each with a health status and a receipt.
- **What-if:** change price, churn, spend or CAC and see ARR, retention, unit economics and cash move
  together across 5,000 seeded simulations. Same inputs, same answer.
- **Receipts:** click any number to see its formula, inputs, data snapshot and formula version.

Stack: FastAPI + NumPy (Python) · Next.js (TypeScript) · Postgres/TimescaleDB · Caddy · Docker Compose.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and the increment plan.

## Use it

1. `make up`, then open http://localhost:8080 and **create a workspace** (tick "Start with a demo company").
2. On **Your team**, add the roles you want: founder's office, business analyst, market researcher,
   lead generator, marketing analyst. Only hired roles run and appear in the app.
3. **Today** shows what your team found, in sentences. Expand any item for the numbers, next steps and
   "show the working".

## Run it

```bash
make up            # creates .env from .env.example, builds and starts everything
open http://localhost:8080
```

Before deploying anywhere public, set a real `POSTGRES_PASSWORD` in `.env`. Set
`SITE_ADDRESS=app.yourdomain.com` to get automatic HTTPS from Caddy (ports 80/443 must reach the host;
set `HTTP_PORT=80 HTTPS_PORT=443`).

| Command | What it does |
| --- | --- |
| `make up` / `make down` | Start / stop the production stack (data kept in the `pgdata` volume) |
| `make dev` | Hot reload: web on :3000, API on :8000 (docs at /docs), DB on :5432 |
| `make test` | Backend tests (revcore + hand-computed ledger) and frontend typecheck |
| `make reseed` | Rebuild the demo workspace |
| `make logs`, `make psql` | Tail logs, open a SQL shell |
| `make clean` | Stop and **delete** all data |

### Docker without the docker group (Fedora / rootless Podman)

```bash
podman system service --time=0 unix:///tmp/podman.sock &
export DOCKER_HOST=unix:///tmp/podman.sock
make up
```

## API

| Method | Path | Returns |
| --- | --- | --- |
| GET | `/api/health` | status + formula version |
| GET | `/api/overview` | metrics with receipts, MRR series, warnings, snapshot id |
| POST | `/api/scenarios` | baseline vs scenario: ARR path bands, NRR, CAC, payback, LTV:CAC, cash-out risk, receipts |
| GET | `/api/receipts/{id}` | one receipt with its snapshot details |

Interactive docs: `http://localhost:8000/docs` in `make dev`.

### Notifications (Increment 7)
`docker compose up` also starts `worker`, which refreshes connected sources daily and sends a "what changed" digest
to Slack and/or email. Set it up under **Settings → Notifications**. Email needs `SMTP_HOST`, `SMTP_USER`,
`SMTP_PASSWORD` and `SMTP_FROM` in `.env` (see `.env.example`). Set `PUBLIC_URL` so links in messages point at your domain.

### Teams, SSO, budgeting, more sources (Increments 8–10)
- **Settings → People:** invites and roles. **Settings → Security:** SSO, sessions, audit log, export and delete
  ([docs/SECURITY.md](docs/SECURITY.md)).
- **Budget:** department-by-department planning with review and publishing. **Track record:** Fred's accuracy
  over the last six closes.
- **Direct connections:** QuickBooks, Xero, Salesforce, Gusto and Google Ads need an OAuth app per provider
  (see `.env.example`). Meta Ads, Zendesk and Intercom use keys the customer pastes in. HubSpot and Salesforce
  support write-back of scores.

## Deploying on Render

**Option A: one Web Service (simplest).** The root `Dockerfile` runs the website and API together.
1. Create a Render Postgres (any plan), then a Web Service from this repo with runtime **Docker** and instance type
   **Standard (2 GB)**. It uses ~750 MB, so 512 MB plans run out of memory.
2. Environment: `DATABASE_URL` = the database's *Internal* URL, and `FRED_SECRET_KEY` = any long random string
   (Render's "Generate" button works). The public address is picked up automatically from Render.
3. Health check path: `/api/health`. Migrations run on every start.

**Option B: Blueprint (separate services).**

1. Click **Deploy to Render** above (sign in with GitHub). Render reads `render.yaml`, which defines three things:
   - `fred-web`: public, with HTTPS.
   - `fred-api`: private; it also runs the daily scheduler.
   - `fred-db`: managed Postgres 16.
2. When asked for `PUBLIC_URL`, enter the address people will use, e.g. `https://fred-web.onrender.com` (or your
   custom domain). Leave the optional SMTP and OAuth fields empty for now.
3. The first build takes 10–15 minutes, because it bakes the embedding model into the image. Migrations run
   automatically before every deploy.
4. If Render gave the web service a different address, update `PUBLIC_URL` on `fred-api` and redeploy. It's used
   in email links and for SSO/OAuth redirects.
5. To add a custom domain: fred-web → Settings → Custom Domains. Then update `PUBLIC_URL`.

Set `ALLOW_SIGNUP=false` on `fred-api` if only invited people should get in.
