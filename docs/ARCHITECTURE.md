# Architecture

Fred is built in increments. Each increment ships something a user can use, runs in the same
Docker stack, and keeps one rule: **every number that reaches the UI has a receipt**
(formula, formula version, input snapshot, inputs, seed).

## Decision: Python API, TypeScript web

| Layer | Choice | Why |
| --- | --- | --- |
| Compute (revcore) | Python + NumPy | Formulas and Monte Carlo already exist in Python; the ML layer (LightGBM, SHAP, MLflow) and LoRA serving (vLLM) are Python-only. One language for everything that computes. |
| API | FastAPI + Pydantic | Request and response contracts double as the LLM tool schemas in Increment 2. |
| DB | Postgres 16 + TimescaleDB | Time-series rollups for spend and usage; plain SQL for everything else. |
| Web | Next.js 15 (App Router) + TypeScript | A real product UI: server-rendered overview, interactive what-if, receipts drawer. It renders only; no business logic lives in the browser. |
| Edge | Caddy | One origin (`/api/*` to API, everything else to web), automatic HTTPS when `SITE_ADDRESS` is a domain. |

## Runtime topology (today)

```
browser ──► proxy (Caddy :80/:443)
              ├── /api/*  ──► api (FastAPI, uvicorn)  ──► db (Timescale/Postgres)
              └── /*      ──► web (Next.js standalone) ──► api (server-side fetch, internal network)
migrate (one-shot): alembic upgrade head → seed demo workspace if SEED_DEMO=true
```

Networks: `backend` (db, migrate, api) and `frontend` (api, web, proxy). The database is not
reachable from the proxy or from the web container.

## Code map

```
backend/
  app/revcore/        pure formulas + seeded simulation. No I/O. Version string in every receipt.
  app/metrics.py      compute_metrics(TenantData) is pure; load_tenant_data() is the only SQL.
  app/provenance.py   content-addressed receipts (same inputs → same receipt id), idempotent writes
  app/catalog.py      labels, units, health thresholds per metric (from the formula contract)
  app/main.py         HTTP: /health, /overview, /scenarios, /receipts/{id}
  app/seed.py         deterministic demo tenant (24 months, ~900 accounts)
  migrations/         alembic, raw SQL DDL
  tests/              revcore fixtures + hand-computed ledger (the "matches a spreadsheet" gate)
frontend/
  app/page.tsx        Overview (server component)
  app/scenarios/      What-if workbench (client, debounced auto-run)
  components/         ReceiptDrawer (global), MetricCard, MrrChart, FanChart, ScenarioWorkbench
deploy/Caddyfile
```

## Key design rules

1. **The math is pure.** `revcore` and `compute_metrics` take data in and return numbers out. Tests
   pin them to hand-computed values, including the blueprint's reference scenario.
2. **Receipts are content-addressed.** A receipt id is the hash of metric, value, inputs, snapshot,
   formula version and seed. Same question over the same data gives the same id.
3. **Snapshots pin inputs.** `snap_<month>_<tenant>_<hash>` is the hash of every input row used.
4. **Every response has the same envelope**: `warnings[]`, `data_coverage`, `snapshot_id`. The UI
   always shows warnings above results; the orchestrator (Increment 2) must lead with them.
5. **Formula changes bump `FORMULA_VERSION`** so old receipts stay explainable.

## The analyst team (Increment 2)

Each role is a set of jobs. Each job uses the cheapest method that does it well, and the method is
shown on every finding in the UI.

| Tier | Method | Cost | Used for |
| --- | --- | --- | --- |
| 1 | Rules and closed-form maths | free | Lead routing, playbooks, goal-seeking, cash paths |
| 2 | Statistics | free | Forecasts (driver model vs Holt, backtested), spend response curves (log-log OLS), anomalies (median/MAD), ICP significance tests |
| 3 | Trained ML | CPU seconds per data snapshot | Lead conversion and churn (gradient boosting, isotonic calibration, time-split holdout, counterfactual reason codes) |
| 4 | LLM | per call | Language only (parsing questions, drafting outreach). Never numbers. **Zero calls today.** |

| Role | Jobs | Engine | Code |
| --- | --- | --- | --- |
| Marketing analyst | Channel CAC, payback and LTV:CAC; spend anomaly QA; same-budget reallocation | Statistics + optimizer | `analysis/marketing.py` |
| Lead generation | Score every open lead, explain it, route it, estimate expected value; learn the ICP | ML + statistics + rules | `ml/leads.py` |
| Founder's office | Backtested 12-month forecast; goal planner with spend and retention levers, cash path and out-of-sample guard | Statistics + closed form | `analysis/forecast.py`, `analysis/planner.py` |
| Growth strategist | Churn gravity with reasons and plays; benchmark positioning; MBA playbooks | ML + knowledge base | `ml/churn.py`, `knowledge/` |

`team.py` runs every job once per data snapshot (about 9 s of CPU on the demo, cached after that),
ranks findings by severity and dollar impact, and writes receipts plus model-registry rows.

**Knowledge base** (`app/knowledge/*.yaml`): versioned benchmarks with a stated basis, and playbooks
that map conditions on computed facts to diagnosis, actions and the management framework behind them.
It has an `as_of` date and a review interval; the UI warns when a review is overdue.

**Why gradient boosting, not deep learning:** a typical B2B SaaS company has thousands of leads and
hundreds of churn events. On tabular data that size, calibrated boosted trees beat neural networks and
train in seconds on a CPU. Deep sequence models go in when one of these is true: more than about 2,000
labelled churn events per tenant, raw clickstream sequences, or a cross-tenant model trained on pooled,
consented data (Increment 8).

**Ground-truth tests** (`tests/test_team.py`): the demo generator plants real structure (channel
elasticities, ICP segments, usage-driven churn, a spend anomaly). The tests require the team to find it:
AUC > 0.75, calibration error < 0.05, the planted anomaly found with no false alarms, elasticities
recovered within ±0.2, and no reason that contradicts the data.

## Strategy & finance: the BizOps hire (Increment 3a)

Product focus: do the job of the first BizOps / strategic-finance hire (the MBA a Series A-B company
hires at $150-220K). The monthly core is budget vs actual, built in `app/finance/`:

| Piece | Method | Exactness |
| --- | --- | --- |
| P&L vs approved plan, month + YTD | GL actuals mapped to P&L groups | Ties to the ledger (tested) |
| Revenue bridge | volume x plan ARPA + new x (ARPA gap) + churn gap + expansion gap | Components sum exactly to the revenue gap |
| Payroll bridge | Person-level: late starts, open/cancelled roles, unplanned hires, departures, pay vs plan | Sums exactly per department |
| Variable costs | Flexible budget: volume vs rate | Exact split |
| Other lines | Rules: one-off (one month = 70%+ of gap) vs run-rate change (3+ months, same sign, 8%+) | Deterministic |
| Materiality | Revenue 2%/$25K, payroll 3%/$15K, other 5%/$5K YTD; run-rate changes over $25K/yr flagged early | Configurable |
| Cash | Plan cash = opening cash + planned operating income | Reconciles to operating variance |

Data model (`migrations/0003`): `gl_account` (chart of accounts mapped to P&L groups), `gl_actual`,
`employee`, `payroll`, `plan`, `plan_line`, `plan_driver`, `plan_hire`, `plan_payroll`.
Demo data plants six stories (late hires, a cancelled role, a departure plus backfill, raises above
plan, software creep, a one-off legal bill, hosting overrun, new-logo shortfall); `tests/test_finance.py`
requires each one to be found and correctly labelled.

Next in this track: QuickBooks/Xero + Gusto/Rippling connectors with an account-mapping screen;
reforecast (rest of year from actual run-rates); headcount plan editor with runway impact; investor
update and board deck generation (the first LLM use: prose only, every number must carry a receipt).

## Production SaaS + modular roles (Increment 4)

**Accounts and workspaces** (`app/auth.py`, `migrations/0004`): email + password sign-up (scrypt, per-user
salt), 30-day sessions in an httpOnly SameSite=Lax cookie (only a SHA-256 of the token is stored),
failed-login throttling (5 per 15 min per email + IP), owner/admin/member roles. Every data endpoint
resolves the workspace from the session; nothing trusts a tenant id from the request. Sign-up can load
a private demo company; its ids are re-namespaced per workspace (`seed.scope_ids`) so workspaces never
collide. Models train in the background right after sign-up, and the per-snapshot cache keeps the 8
most recent workspaces warm.

**Role modules** (`app/modules.py`): the team a workspace hires. Each module declares its jobs, pages,
what it covers, the data it needs and its methods. Hiring or releasing a role is one click on
**Your team**; endpoints for un-hired roles return 403, and their findings disappear from the briefing.
Adding a new role is a registry entry plus its job code.

| Role | Jobs | Engines |
| --- | --- | --- |
| Founder's office | Budget vs actual, forecast and goal planning, what-if | Rules, statistics, simulation |
| Business analyst | Metrics, churn risk, voice of customer | Trained model, pre-trained model, rules |
| Market researcher | Segment discovery, double-down/stop calls, lookalike leads | Pre-trained model + clustering |
| Lead generator | Lead scoring, routing, ICP | Trained model, statistics |
| Marketing analyst | Channel economics, reallocation, anomalies | Statistics, optimizer |

**Pre-trained model** (`app/ml/embeddings.py`): `BAAI/bge-small-en-v1.5` (MIT, 67 MB, 384-dim) on
ONNX Runtime via fastembed. CPU-only, baked into the API image at build time (`HF_HUB_OFFLINE=1` at
runtime), with an in-process embedding cache. One model does three jobs: clustering company descriptions
into segments (k-means, k by silhouette, names by class-based TF-IDF), lookalike search, and zero-shot
tagging of exit surveys and NPS comments against editable theme descriptions (`knowledge/themes.yaml`).
On the demo data it recovers the hidden niches (most segments 100% pure, best = a planted high-retention
niche, worst = a planted poor-fit niche) and tags exit reasons with 100% accuracy. The demo text is
templated, so expect lower accuracy on real customer text. `tests/test_research.py` enforces
the bars (70% of segments at least 90% pure; tagging at least 85%).

**UI**: Today leads with at most four escalations as sentences; numbers, steps and receipts expand on
click. Each hired role has a desk card. Research pages lead with a verdict sentence and keep the
method in a collapsible "How this was found".

## Deliverables and the work loop (Increment 5)

**Documents** (`app/deliverables/`): investor update, board pack, and decision memos (hire, price
change, marketing budget). Written by code from structured results: same data, same document, zero
LLM calls. Exported as print-ready PDF (board pack: one section per page) or copy-as-text (Markdown).

**The number guard** (`deliverables/facts.py`): writers never format numbers themselves; every figure
goes through a `FactBook` that records which fact (and receipt) produced each displayed string. Before
a document is saved, the verifier scans all of its text; any numeric token no fact produced blocks
publication. Numbers that are not data claims are labelled explicitly: definitions ("24 months"),
user-written task text, and fixed playbook wording. This is the mechanism that will let an LLM polish
the prose later without being able to introduce a number. Tests check every document type, determinism,
and that an invented "$9.5M" is caught.

**Work loop** (`app/work_api.py`):
- *Assign*: any finding becomes a task with owner, due date, the playbook steps, and the finding's
  impact at that moment as a baseline.
- *Measure*: on each new data snapshot the tracker compares today's impact for the same finding with
  the baseline: improving, worse, no change, or resolved (no longer flagged).
- *Business memory*: "It's expected" plus a reason mutes a finding for 90 days and keeps the note;
  briefings and documents respect it; "Flag again" lifts it.
- Open tasks appear in the investor update ("what we're doing about it") and the board pack risk table
  with owners.

## Real data: connectors, imports, mapping (Increment 6)

**Sources** (Settings → Data sources):

| Source | How | Feeds |
| --- | --- | --- |
| Stripe | Restricted read-only key (Customers, Invoices: read) | Accounts, MRR history |
| HubSpot | Private-app token (companies + contacts read) | Leads, firmographics, company descriptions, acquisition channel |
| QuickBooks / Xero | "Profit and Loss by Month" CSV export, as-is (OAuth connectors next) | P&L actuals, chart of accounts |
| Gusto / Rippling / Deel | Roster CSV | People, monthly loaded payroll |
| Budget, hiring plan | CSV (long or wide) | Approved plan, person-level payroll plan |
| Ad platforms | Daily spend CSV | Channel detail for the marketing analyst |
| Surveys / support | CSV | Voice of customer |
| PostHog / Segment / Amplitude | Weekly usage CSV | Churn model |

**Stripe MRR** is rebuilt from invoice lines: recurring lines only (one-time items and prorations
excluded), net of discounts, spread over the service period (annual ÷ 12, quarterly ÷ 3), void,
draft and uncollectible invoices ignored, months after the last closed month dropped, and other
currencies skipped with a warning (no silent FX). Handles both the classic and the 2025 invoice-line
API shapes, pagination, `has_more` line expansion and rate-limit retries.

**Pipeline rules** (`connectors/pipeline.py`): each source refreshes its own slice idempotently with
per-workspace deterministic ids; the first real import removes the demo company; the general ledger
is the source of truth for spend totals (`spend.source = 'gl'`), while ad files only add channel
detail, so nothing is double counted; user edits to the account mapping survive re-imports.

**Account mapping**: rules first (payroll before revenue; nothing under an Expenses section can be
revenue), the pre-trained model as a zero-shot fallback for unusual names, every account marked
rule / guess / you and editable in the UI.

**Credentials**: Fernet-encrypted at rest with `FRED_SECRET_KEY`, validated against the provider
before storing, never returned (UI sees the last 4 characters). Syncs run in the background and are
logged with stats, warnings and errors. CSV imports are one transaction: a failed import changes
nothing.

**Graceful degradation**: each job declares the data it needs (`team.readiness`). Jobs without it
are skipped and Today lists exactly what to connect; the API answers 409 with the missing input.

**Proof** (all in CI): Stripe and HubSpot clients against realistic recorded payloads (pagination,
retries, annual/quarterly/discount/proration/void/FX cases, new and classic line shapes); CSV round
trip of the whole demo company, including a QuickBooks-style export with title rows, sections,
subtotals and `$`/`( )` formatting; every demo account maps back to its original line; a billing-only
workspace runs without errors. End to end on the running stack: a workspace built only from the
sample files matches the demo workspace to the dollar on every P&L line and on cash; the Stripe sync
ran through the real database with an encrypted credential and a logged run.

## Increments

| # | Ship | User can | Done when | Status |
| --- | --- | --- | --- | --- |
| 1 | Spine + what-if: canonical tables, revcore metrics with receipts, scenario engine, overview + workbench, Docker stack | See 14 health metrics with status and receipts; run price, churn, spend and CAC what-ifs | Hand-computed ledger test and blueprint reference run pass; stack boots with one command | **Done** |
| 2 | Analyst team: lead scoring and routing, churn gravity, channel economics, budget optimizer, anomaly QA, backtested forecast, goal planner, versioned knowledge base, Today briefing | Start the day with a ranked to-do list from four "analysts", each finding explained, sourced and actionable | Ground-truth tests pass; 0 LLM calls | **Done** |
| 3 | Real data: Stripe + HubSpot connectors, lead/contact sync, product usage (PostHog/Segment), nightly snapshot, morning brief by email/Slack | The team runs on their own data from day one | Metrics match one real company's spreadsheet to the cent | Next |
| 4 | Auth + multi-tenancy: login, workspaces, Postgres RLS, roles | Invite a teammate; data isolated per workspace | Cross-tenant reads return nothing | |
| 5 | Close the loop: push routed leads and at-risk owners back to the CRM (with approval), track whether each recommendation was acted on and what happened | Recommendations become tasks; the team learns which plays work | Uplift measured per playbook | |
| 6 | Orchestrator: plain-English questions to tool calls (LLM tier 4), receipt-checking guard; outreach and QBR drafting from structured facts | Ask "why did CAC rise?" and get a sourced answer | 50-question eval: zero numbers without receipts | |
| 7 | Deeper models: deal staleness curves, Markov/Shapley attribution, survival model for churn timing, price elasticity fitted from history, grandfathered pricing in what-if | Attribution and pricing decisions on real evidence | Back-tests against past changes | |
| 8 | Deep learning where data earns it: sequence models on raw usage, cross-tenant priors (consented, pooled), LoRA adapters for call/objection extraction | Better accuracy for large tenants and cold starts | Beats boosted trees on holdout, or does not ship | |

## Known gaps (tracked, not hidden)

- Scenario applies a price change to all existing customers at month 1 (blueprint reference
  behaviour). Grandfathering is Increment 5.
- The simulation uses logo churn as revenue churn, so simulated NRR (about 0.94) runs above
  historical NRR (about 0.92) for the demo tenant. Fixed by the churn split in Increment 5.
- Single workspace (`DEFAULT_TENANT_SLUG`) until auth lands in Increment 3. All queries are already
  tenant-scoped.
- Metrics are computed per request (fast at demo scale). The nightly precompute lands with
  Increment 3.
- The team recommends; it does not act. It doesn't send emails, call leads or move budget. Write-back
  with human approval is Increment 5.
- The demo data is synthetic. Model quality on a real tenant depends on that tenant's history: the
  model cards show holdout AUC and calibration for whatever data is loaded.
- The planner assumes changes take effect from month 1. It warns when a goal is out of sample but
  does not yet model hiring ramps.

## Keeps working on its own: daily sync and digest (Increment 7)

A `worker` service (same image as the API, `python -m app.worker`) wakes every minute. For each workspace
whose local digest hour has come (default 8:00, workspace time zone), it claims `(workspace, 'daily', date)`
in `scheduled_job`. The claim is an `INSERT … ON CONFLICT`, so with any number of workers each workspace
runs exactly once a day. A claim left unfinished for two hours is taken over. The daily job:

1. syncs every connected API source (Stripe, HubSpot), recording success or failure in `sync_run`;
2. builds the digest from the live analysis and sends it to Slack (incoming webhook, stored encrypted)
   and by email over SMTP to each member who hasn't opted out, with their own tasks first.

The digest only carries news (`app/notify/digest.py`, pure and unit-tested):
- findings of medium severity or above the team hasn't been told about (`alerted_finding`), or that went
  away for 14 days and came back;
- tasks whose measured progress changed state (improving, worse, resolved; `task.notified_state`);
- a reminder the day after a task is due, then weekly;
- the latest sync per source if it failed since the last digest.

If there's no news, no message is sent. Every attempt is logged in `notification` and shown under
Settings → Notifications, along with a preview of the next message.

Safety:
- Webhook URLs must be `https://hooks.slack.com/services/…`, so the field can't be used to reach
  internal hosts.
- Email is off until `SMTP_HOST` is set (any provider).
- Emails carry `Auto-Submitted: auto-generated`, so out-of-office replies don't loop.

## Team, security and SSO (Increment 8)

- **People:** invites by email (single-use token, 7-day expiry, hash stored), owner/admin/member roles with
  last-owner protection, and workspace switching. Task owners link to real members by name or email, and their
  follow-ups come first in their own digest.
- **Audit log:** an append-only `audit_event` table, with IP and user agent from request middleware.
  Settings → Security shows it.
- **Throttling:** failed sign-ins are throttled in Postgres (`login_attempt`), so the limit survives restarts and
  holds across replicas.
- **Sessions:** each person can list and revoke their own sessions.
- **Data:** owners can export everything as CSV (credentials excluded) and delete the workspace.
- **SSO:** generic OIDC with PKCE, nonce and state; the RS256 signature is checked against the provider's JWKS.
  Users are provisioned just in time. Enforcement is allowed only after one verified sign-in. SSO sessions are
  pinned to their workspace.
- **Compliance:** see [SECURITY.md](SECURITY.md) for the control mapping and the remaining SOC 2 work.

## Track record (Increment 9)

`app/accuracy.py` replays the last six month-ends with data cut off at each point, then compares Fred's calls
with what happened:
- forecasts against a "no change" baseline;
- run-rate warnings against the months that followed;
- how much of each budget gap the bridges explain;
- billing against the books;
- out-of-time model hit rates.

Misses are shown, not hidden. On the demo company, one of the two early warnings didn't hold.

## More sources and CRM write-back (Increment 9)

`connectors/registry.py` lists every source. A sync is split into `fetch()`, which runs with no transaction
open, and `write()`, which runs in one transaction. Every source produces the same shapes as the CSV importers,
so mapping, user overrides and the bridges are shared.

| Source | Auth | Gives |
|---|---|---|
| QuickBooks, Xero | OAuth (refresh tokens rotated and saved immediately) | monthly P&L by account, bank balances |
| Salesforce | OAuth | leads (status, source, conversion), accounts |
| Gusto | OAuth | roster with pay annualised; leavers kept |
| Google Ads | OAuth + developer token | daily paid-search spend |
| Meta Ads, Zendesk, Intercom | API key | ad spend; tickets, CSAT and ratings linked to customers |

Ad spend and feedback are replaced per source, so several tools coexist.

Write-back pushes lead scores and churn risk to the CRM, each with a reason:
- **HubSpot:** Fred creates its own properties.
- **Salesforce:** an admin adds four custom fields, and Fred names the missing ones.

Only records Fred imported from that CRM are touched.

## Shared budgeting (Increment 10)

1. An admin opens a budget round, and each department gets a draft:
   - spend at its current run rate;
   - current staff carried over at their salaries;
   - revenue planned from drivers (new customers, price, churn, expansion).
2. Owners add hires and submit.
3. Fred's review flags:
   - spend growing much faster than revenue;
   - salaries outside the team's range;
   - hires dated in the past;
   - cash runway.
4. Admins approve each department or send it back with a note.
5. Publishing writes the plan through the same tables as the CSV budget. Payroll plan lines equal the
   person-level payroll plan exactly.

Budget vs actual keeps measuring against the plan for the year whose books are open, so a published plan for
next year takes over in January.
