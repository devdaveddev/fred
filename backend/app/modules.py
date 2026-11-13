"""Role modules: the 'team' a workspace can hire. Each module owns its jobs, pages and findings.

Adding a role = one entry here + its job code. Hiring and letting go is per workspace.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class Module:
    id: str
    title: str
    tagline: str
    replaces: str
    salary_equivalent: int  # typical fully loaded annual cost of the hire this role covers (USD, rough)
    delivers: list[str]
    methods: list[str]
    pages: list[dict]
    finding_roles: list[str]  # which finding.role values belong to this module
    needs: list[str] = field(default_factory=list)
    default_hired: bool = True


MODULES: dict[str, Module] = {m.id: m for m in [
    Module(
        id="founders_office", title="Founder's office",
        tagline="Runs the monthly close review, the plan and the board numbers.",
        replaces="BizOps / strategic finance lead", salary_equivalent=190_000,
        delivers=["Budget vs actual with the reason behind every variance",
                  "12-month forecast and goal planning with cash checks", "What-if scenarios for pricing and spend"],
        methods=["Rules", "Statistics", "Simulation"],
        pages=[{"href": "/finance", "label": "Budget vs actual"}, {"href": "/plan", "label": "Plan & forecast"},
               {"href": "/scenarios", "label": "What-if"}],
        finding_roles=["founders_office"], needs=["Accounting", "Payroll", "Billing"]),
    Module(
        id="business_analyst", title="Business analyst",
        tagline="Watches the metrics, predicts churn and reads what customers say.",
        replaces="Business / data analyst", salary_equivalent=120_000,
        delivers=["Health metrics with benchmarks", "Accounts likely to churn, with reasons and a play",
                  "Why customers leave, tagged from exit surveys and NPS"],
        methods=["Trained model", "Pre-trained language model", "Rules"],
        pages=[{"href": "/metrics", "label": "Metrics"}, {"href": "/accounts", "label": "At-risk accounts"},
               {"href": "/voice", "label": "Voice of customer"}],
        finding_roles=["growth", "business_analyst"], needs=["Billing", "Product usage", "Surveys"]),
    Module(
        id="market_researcher", title="Market researcher",
        tagline="Finds which kinds of customers you win and keep, and who to go after next.",
        replaces="Market research analyst", salary_equivalent=110_000,
        delivers=["Customer segments discovered from company descriptions",
                  "Which segments to double down on or stop chasing", "Lookalike prospects for the best segment"],
        methods=["Pre-trained language model", "Clustering"],
        pages=[{"href": "/market", "label": "Market segments"}],
        finding_roles=["market_researcher"], needs=["CRM"]),
    Module(
        id="lead_generator", title="Lead generator",
        tagline="Scores, explains and routes every inbound lead.",
        replaces="Lead researcher / SDR triage", salary_equivalent=85_000,
        delivers=["Every open lead scored and explained", "Routing to AE, SDR or nurture",
                  "Your real ICP, tested for significance"],
        methods=["Trained model", "Statistics"],
        pages=[{"href": "/leads", "label": "Leads"}], finding_roles=["lead_gen"], needs=["CRM", "Web analytics"]),
    Module(
        id="marketing_analyst", title="Marketing analyst",
        tagline="Knows what each channel really costs and where the next dollar should go.",
        replaces="Marketing / performance analyst", salary_equivalent=105_000,
        delivers=["Channel CAC, payback and LTV:CAC", "Same-budget reallocation", "Spend anomaly alerts"],
        methods=["Statistics", "Optimizer"],
        pages=[{"href": "/marketing", "label": "Marketing"}], finding_roles=["marketing_analyst"],
        needs=["Ad platforms", "CRM"]),
]}
ROLE_TO_MODULE = {r: m.id for m in MODULES.values() for r in m.finding_roles}


def hired(conn: Connection, tenant_id: str) -> set[str]:
    rows = conn.execute(text("SELECT module_id, hired FROM workspace_module WHERE tenant_id = :t"),
                        {"t": tenant_id}).all()
    state = {m: h for m, h in rows}
    return {mid for mid, mod in MODULES.items() if state.get(mid, False)}


def set_hired(conn: Connection, tenant_id: str, module_id: str, value: bool) -> None:
    if module_id not in MODULES:
        raise HTTPException(404, "Unknown role")
    conn.execute(text("""INSERT INTO workspace_module (tenant_id, module_id, hired) VALUES (:t, :m, :h)
                         ON CONFLICT (tenant_id, module_id) DO UPDATE SET hired = :h, hired_at = now()"""),
                 {"t": tenant_id, "m": module_id, "h": value})


def roster(conn: Connection, tenant_id: str) -> list[dict]:
    h = hired(conn, tenant_id)
    return [{**asdict(m), "hired": m.id in h} for m in MODULES.values()]
