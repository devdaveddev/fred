# Competitive analysis: what Fred replaces, and what it shouldn't try to

_Researched November 2026. Most vendors in this market don't publish prices. The figures below are third-party
estimates and median contract values (mostly Vendr/CostBench-derived), and the sources often disagree. Treat
them as ranges, not quotes._

## 1. The customer's real bill: tools plus the people who run them

The customer is a Seed to Series B B2B SaaS company (about $1–20M ARR, 15–80 people). Across the jobs Fred's
five roles cover, it typically buys:

| Job | Typical tools | What it costs per year (est.) |
|---|---|---|
| Planning, budget vs actual, forecast | Runway/cfo.ai, Cube, Abacum, Pigment, Drivetrain | cfo.ai ~$4.5K–24K · Cube median ~$22K · Abacum ~$37–44K · Pigment median ~$74K (Year 1 can reach $300K+) |
| SaaS metrics (MRR, churn, NRR) | ChartMogul, Baremetrics, Stripe | ~$1K–9K |
| Lead data, intent, scoring | Apollo, Clay, ZoomInfo, 6sense | Apollo $49/user/mo · Clay ~$650–5K · ZoomInfo median ~$32K · 6sense median ~$62K |
| Marketing attribution | Dreamdata, HockeyStack | Dreamdata median ~$35K · HockeyStack ~$28K |
| Churn risk and customer health | Vitally, ChurnZero, Planhat, Gainsight | ~$4K–20K small · Gainsight $60K+ · implementation often $5K–120K extra |
| Voice of customer / research | Dovetail, Unwrap, Enterpret | Dovetail median ~$22K · Unwrap ~$24K+ · Enterpret quote-only, volume-priced |

**Tool spend for a company that buys one tool per job: roughly $100K–250K a year.**

The tools don't run themselves. The same company hires people to operate them:

| Role | 2026 base salary (job-posting data) |
|---|---|
| Senior BizOps manager | median ~$144K (SF ~$188K) |
| FP&A analyst, 3–5 yrs | ~$110–150K |
| Business ops analyst | median ~$120K |
| RevOps manager | median ~$123K |

With a typical 1.25–1.35× loading for taxes and benefits (my assumption, not from the sources), three of
these hires cost **~$450–600K a year**.

**The core insight:** today the customer pays twice, once for the canvas (software) and again for the person
who paints on it. Every tool above sells a place to *do* analysis; none sells the *finished analysis*. Fred's
wedge is to sell the finished work: the variance explained, the memo written, the task assigned, the result
measured.

## 2. Why people actually pay for these tools

Ranked by how hard each reason is to replace.

1. **Proprietary data (cannot be replaced with code).** ZoomInfo, Apollo and 6sense sell contact databases
   and third-party intent signals. No model trained on a customer's own data produces a phone number or
   tells you who is researching your category. *Fred should integrate with these, not compete.*
2. **Data plumbing and one trusted number.** Most of the value in FP&A and metrics tools is connecting
   billing, accounting, CRM and payroll, then keeping the mapping clean. This is the moat, and it is
   tedious: chart-of-accounts mapping, MRR normalisation, deduplication. *Fred already does this for
   Stripe, HubSpot and CSV; breadth of integrations is the gap.*
3. **Collaboration and process.** In Cube, Abacum and Pigment, department heads submit budgets, versions are
   locked and approvals are tracked. The CFO is buying a process, not only maths. *Fred has none of this
   today.*
4. **Defensibility.** "The board pack came from Pigment" is a credibility signal. Buyers pay for an audit
   trail and for not being blamed. *Fred's receipts and number-guard are a real answer here: every number
   traces to a source, which most tools can't show.*
5. **Workflow for operators.** CS platforms give CSMs playbooks, task queues and health-score alerts, and
   attribution tools give marketers dashboards to defend budget. *Fred's work loop covers part of this; it
   doesn't push into the tools those people live in (CRM, Slack) beyond the digest.*
6. **Benchmarks.** ChartMogul and Baremetrics show how you compare with peers. *Needs many customers;
   Fred's YAML knowledge base is a stand-in.*

## 3. Where Fred stands, job by job

| Job | Incumbent | Fred today | Verdict |
|---|---|---|---|
| SaaS metrics | ChartMogul, Baremetrics | MRR from Stripe invoices, NRR/GRR, cohorts, receipts | **Replaces.** Commodity; include it, don't charge for it alone. |
| Budget vs actual | Cube, Abacum | Exact revenue and payroll bridges, one-off vs run-rate, materiality, explanations | **Replaces and beats** on "why". These tools show the variance; Fred explains it. |
| Forecast and scenarios | cfo.ai, Pigment | Backtested forecast, goal planner with an out-of-sample guard, simulations | **Mostly replaces** for founders. Lacks multi-user planning and versions. |
| Collaborative budgeting | Cube, Abacum, Pigment | None | **Gap.** Required to sell to a company with a CFO. |
| Lead scoring on your own funnel | MadKudu, HubSpot scoring | Calibrated model, reasons for each score, routing | **Replaces.** |
| Contact data and intent | ZoomInfo, Apollo, 6sense | None | **Do not compete. Integrate.** |
| Outbound execution | Apollo sequences, AI SDRs | None | **Do not build.** See section 5. |
| Attribution / channel analysis | Dreamdata, HockeyStack | Response curves, anomalies, reallocation from spend and leads | **Partial.** No touchpoint journeys; needs ad-platform and web-event integrations. |
| Churn risk | Vitally, ChurnZero, Gainsight | Calibrated churn model, reasons, at-risk list | **Replaces the analysis**, not the CSM's daily workspace. |
| Voice of customer | Dovetail, Unwrap, Enterpret | Theme tagging and segments from imported feedback | **Replaces for small volumes.** Needs Intercom, Zendesk and Gong connectors. |
| Board pack, investor update, memos | Analyst + PowerPoint | Generated, every number verified | **Unique.** No tool above produces the finished document. |

## 4. Competitive threats to take seriously

- **Planning tools are adding agents.** Runway rebranded as cfo.ai (Sept 2026) and launched "Ari", an AI CFO
  agent. Pigment ships Modeler and Analyst agents plus an MCP server. Datarails added finance agents.
  Fred's differentiation can't be "has AI". It has to be **finished, verified work across finance, revenue,
  marketing and research in one place**, while each incumbent is an agent bolted onto one silo.
- **Data tools expose themselves to general assistants.** Baremetrics has an MCP server; a founder can ask
  Claude about their MRR directly. Answering one-off questions is commoditising fast. What stays scarce is
  *proactive*, *correct* and *accountable* work: noticing unprompted, numbers you can defend, and an owner
  and outcome for each issue.
- **CRMs will bundle scoring and health.** HubSpot and Salesforce will keep absorbing lead scoring and simple
  churn flags. Fred must be better than "good enough and already paid for": explain why, and measure whether
  the fix worked.

## 5. A warning from AI SDRs

Widely repeated reports (traced to 2025 TechCrunch coverage, and not independently verified) say 11x lost
roughly 70–80% of early customers within about three months. Artisan's CEO acknowledged high churn and early
hallucinations. The reported causes: buyers expected the tool to **replace a whole team**, the underlying
**data was bad**, and the output **wasn't trusted**. The market settled on a hybrid where AI drafts and humans
own the result.

What this means for Fred:
- **Don't promise "fire your analyst."** Promise "you don't need to hire one yet": Fred does the analysis and
  reporting, and a named person owns each decision. That is already how the task loop works.
- **Lead with proof, not autonomy.** Before a customer relies on Fred, show its forecast backtest, its
  reconciliation to their accounting system, and its past variance explanations against what actually
  happened.
- **Never generate numbers with an LLM.** The rules → statistics → ML → language-model order, and the
  verifier that blocks untraced numbers, are a sales asset. Say so on the website.

## 6. How to win: position, price, sequence

**Position.** "Your first BizOps, finance and growth analyst team, not six tools and three hires. Every number
traced, every issue owned, and whether the fix worked measured." It is sold by role: hire the Founder's office
first, then add roles as the data arrives.

**Price (hypothesis to test with design partners).** Anchor against the stack, not against Cube alone:

- Roles at roughly $500–1,500 per month each.
- Bundle at roughly $3–5K per month ($36–60K per year).

That is one-fifth to one-tenth of the tools-plus-people bill, and close to a single mid-market tool. Free
SaaS metrics is the land; paid roles are the expand.

**Who first.** B2B SaaS, $1–20M ARR, founder or a lone finance person running the numbers in spreadsheets,
considering their first BizOps or FP&A hire. These buyers are poorly served by incumbents: too expensive, and
too much setup without an analyst to run them.

**Build next, in order of what unblocks revenue:**
1. **Integrations breadth:** QuickBooks and Xero via OAuth, then NetSuite, Salesforce, Intercom and Zendesk,
   Google, LinkedIn and Meta Ads, and Gusto, Rippling and Deel. Data plumbing is the main thing customers pay for.
2. **Proof of accuracy:** a "Fred vs your last six closes" report run on day one with real data.
3. **Write-back:** push lead scores, churn risk and reasons into HubSpot and Salesforce fields, so sales and
   CS see Fred inside their own tools.
4. **Team accounts:** invites, so task owners are real users and alerts reach the right person.
5. **Collaborative budgeting:** department heads submit plans, with versions and approvals. Needed to
   move upmarket to companies with a CFO.
6. **Ask Fred:** a language model for questions only, answering from receipts. Never a source of numbers.
7. **Trust package:** SSO, audit log, and a SOC 2 path. Required before any company with a CFO signs.

**Integrate, don't replace:** contact and intent data (Apollo, ZoomInfo, Clay), outbound execution
(sequencers), accounting and close (QuickBooks, Xero, Rillet).

## 7. On replacing the roles, honestly

Fred replaces the **analysis and reporting** part of BizOps, FP&A, marketing analyst, market research and lead
qualification roles: pulling data, reconciling, finding variances, scoring, writing the monthly pack. It does
not replace negotiation, cross-team influence, or judgment calls with incomplete information. A named human
still owns those decisions. The credible claim, to be tested with design partners, is that a company can
delay its first non-technical analyst hire, and later hires fewer of them.

## Sources

- FP&A pricing: [CFO Shortlist benchmarks](https://www.cfoshortlist.com/reports/fpa-software-pricing-benchmarks), [Aleph pricing guide](https://www.getaleph.com/answers/fpa-software-pricing-guide-2026), [fpastack comparison](https://fpastack.com/fpa-software-pricing/), [Rework: Pigment alternatives](https://resources.rework.com/tools/fpa-planning/best-pigment-alternatives)
- Runway → cfo.ai and AI CFO agents: [Aleph: Runway alternatives](https://www.getaleph.com/answers/runway-alternatives-financial-planning), [Datarails](https://en.wikipedia.org/wiki/Datarails), [ChatFin buyer's guide](https://chatfin.ai/blog/ai-cfo-software-2026-buyers-guide-to-autonomous-finance-readiness/) (vendor-authored)
- SaaS metrics: [Toolradar: ChartMogul pricing](https://toolradar.com/tools/chartmogul/pricing), [CostBench: Baremetrics](https://www.costbench.com/software/product-analytics/baremetrics/), [Baremetrics vs ChartMogul vs Stripe](https://baremetrics.com/blog/baremetrics-vs-chartmogul-vs-stripe-analytics)
- Sales intelligence: [Cleanlist: Clay vs Apollo vs ZoomInfo](https://www.cleanlist.ai/blog/2026-07-24-clay-vs-apollo-vs-zoominfo), [Origami: Clay vs 6sense](https://origami.chat/blog/clay-vs-6sense), [cost comparison](https://www.vahorizon.site/b2b/guides/data-enrichment-intent-tool-costs-saas/)
- Customer success: [Skalin: CSP pricing](https://www.skalin.io/comparisons/pricing-customer-success-platforms), [CostBench CS benchmarks](https://costbench.com/stats/customer-success-pricing-2026/), [Rework: Gainsight alternatives](https://resources.rework.com/tools/customer-success/best-gainsight-alternatives)
- Attribution: [Docket: Dreamdata pricing](https://docket.io/resources/research/dreamdata-pricing), [Docket: HockeyStack pricing](https://docket.io/resources/research/hockeystack-pricing), [CostBench: Dreamdata](https://www.costbench.com/software/marketing-attribution/dreamdata/)
- Voice of customer: [Enterpret pricing](https://enterpret.com/pricing), [Toolradar: Unwrap](https://toolradar.com/tools/unwrap/pricing), [Usercall: Dovetail pricing](https://www.usercall.co/post/dovetail-pricing-explained-what-you-really-pay-who-it-s-for-and-smarter-alternatives)
- Salaries: [Recruiting from Scratch: Senior BizOps](https://www.recruitingfromscratch.com/roles/senior-bizops-manager), [Recruiting from Scratch: RevOps](https://www.recruitingfromscratch.com/roles/revenue-operations-manager), [ZipRecruiter: finance startup](https://www.ziprecruiter.com/Jobs/Finance-Startup)
- AI SDR churn: [TechCrunch on 11x](https://techcrunch.com/2025/03/24/a16z-and-benchmark-backed-11x-has-been-claiming-customers-it-doesnt-have), [TechCrunch on Artisan](https://techcrunch.com/2025/04/09/artisan-the-stop-hiring-humans-ai-agent-startup-raises-25m-and-is-still-hiring-humans), [Explorium on 11x](https://www.explorium.ai/blog/data-for-gtm/11x-ai-sdr-customer-loss-what-comes-next.md)
