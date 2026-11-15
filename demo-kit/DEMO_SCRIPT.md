# Fred: 3-minute demo (Lumen Desk)

Lumen Desk is a fictional helpdesk SaaS: ~180 customers, about $2.9M ARR, 22 people. The ten CSVs in this
folder are its billing, books, team, budget, hiring plan, ad spend, CRM leads, customer feedback and product
usage. They tie out with each other, and real-looking problems are planted in them for Fred to find.

## Before the call (30 minutes ahead)

1. Open https://fred-ejps.onrender.com and sign in. Go to **Your team** and hire all five roles.
2. Go to **Data sources → Upload your own data** and upload the files in order, `01` to `10` (each card's
   **Upload CSV**). Keep `09_customer_feedback.csv` back if you want to upload one live; it's quick.
3. Open **Today**. The first analysis takes a few minutes on the free server; wait until findings appear.
4. Open **Crew** and press **Run the crew now**, so drafts are waiting. Press **Send it now** on
   Notifications if you want a digest in your inbox to show.
5. Open **Documents** and create a **Board pack** once, so it renders instantly during the call.
6. Have these tabs ready: Today, Budget vs actual, At-risk accounts, Voice of customer, Leads, Marketing,
   Crew, Documents. Clicking between them is faster than navigating.

## The script

**0:00–0:20 · Why (landing page)**
> "Startups pay twice for analysis: about six tools, plus a BizOps lead, an analyst and a marketing analyst to run
> them. Fred is the analyst team itself. It reads a company's own data and does the work of five
> non-technical roles every morning. Every number traces back to its source, and no number comes from a chatbot."

Scroll to **Five roles. Pick yours.** and click a card to show roles being added.

**0:20–0:45 · Bring your data (Data sources)**
> "This is Lumen Desk, a helpdesk SaaS. I uploaded ten exports: billing, the QuickBooks P&L, the payroll
> roster, the budget, the hiring plan, ad spend, CRM leads, survey comments and product usage. Fred mapped
> every account in the chart of accounts and tells each role whether it has what it needs. Here, everything is
> green." Point at the readiness tiles. *(Optional: upload `09_customer_feedback.csv` live.)*

**0:45–1:10 · Today**
> "This is the morning briefing, ranked by money at stake. Revenue is $167K behind plan. Customers
> are leaving over missing integrations. Hosting costs are drifting. LinkedIn spend doubled with no extra leads."

Click **Revenue is $167K below plan** and open the **receipt**:
> "Every number has a receipt: the inputs, the formula and the data snapshot. A board member can audit it."

**1:10–1:35 · Founder's office (Budget vs actual)**
> "This is what a BizOps lead does at month-end, done automatically. The revenue gap is split into exact causes.
> Hosting is a run-rate problem: cost per revenue dollar has been 29% over plan since July, which matters every month. The legal
> bill was a one-off, so ignore it. Payroll is explained person by person: the Account Executive started
> three months late, the Marketing Manager was never hired, and an engineer left in July."

**1:35–1:55 · Business analyst (At-risk accounts → Voice of customer)**
> "A model trained on Lumen's own usage ranks who will churn next quarter. The 10% it flags churn at about 7
> times the base rate, each with a reason and a play. And customers say why: the top theme is a missing Jira and
> Salesforce integration. That points the roadmap at the revenue leak."

**1:55–2:15 · Growth roles (Leads → Marketing → Market segments)**
> "The lead generator scores every open lead and explains it. These ten belong with an AE today. Its real
> ideal customer profile is fintech companies and director-level buyers. The marketing analyst caught LinkedIn
> spend up 123% for twelve days with no lift in leads. The market researcher found the segments from what
> customers say they do: open models, running on our own server."

**2:15–2:35 · The crew (Crew)**
> "Each role is an agent. Each morning they turn findings into owned follow-ups and remind people before and after
> due dates. They work in 'ask me first' mode: here's a drafted follow-up for the hosting overrun."

Click **Approve: create & send**, then show the email arriving (phone or inbox).
> "The agents write the words with CrewAI and Claude, but every number must come from Fred's analysis. A guard
> blocks any figure that isn't traceable."

**2:35–2:50 · Deliverables and proof (Documents → Track record)**
> "It writes the board pack and the investor update, and refuses to publish a number it can't trace. Before you
> trust it, Track record replays the last six month-ends with only the data it had at the time and shows
> where it was right, and where it wasn't."

**2:50–3:00 · Close**
> "Single sign-on, an audit log, and export or delete at any time. It runs on Render, Neon Postgres and Brevo, all free.
> The point: a 30-person company shouldn't need to hire three analysts to understand its own numbers."

## If something goes wrong

- **A page says "Your team is working":** the analysis is still running. Talk through the previous screen; it
  refreshes on its own.
- **Slow first load:** the free server woke up. It's kept awake by a ping every 10 minutes, but give it a minute.
- **Regenerating the data:** `cd backend && uv run python ../deploy/demo_kit/make_kit.py` rebuilds identical
  files (fixed seed).
