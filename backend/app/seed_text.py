"""Customer text for the demo workspace: company descriptions, exit surveys, NPS comments.

Planted truths the pre-trained-model jobs must recover:
  * niches inside an industry retain very differently (payments infrastructure and devtools stay;
    coding bootcamps and D2C apparel churn), which the market researcher should find by clustering
    descriptions, without being told the niche
  * the most common reason mid-market and enterprise customers leave is missing integrations; poor-fit
    niches leave over budget. The business analyst should find this by tagging free text
Generated with its own RNG so the numeric demo data is unchanged.
"""
from __future__ import annotations

import numpy as np

# industry -> [(niche, quality, [what they do], [who they serve])]
NICHES: dict[str, list[tuple[str, str, list[str], list[str]]]] = {
    "fintech": [
        ("payments infrastructure", "great",
         ["builds payment APIs and card processing", "runs merchant acquiring and payouts infrastructure",
          "provides a payments gateway with fraud screening"], ["online merchants", "marketplaces", "SaaS platforms"]),
        ("consumer lending", "normal",
         ["offers instant personal loans", "underwrites buy-now-pay-later credit", "runs a digital lending platform"],
         ["young professionals", "online shoppers", "small borrowers"]),
    ],
    "saas": [
        ("developer tools", "great",
         ["builds CI/CD and observability tooling", "makes an API testing and monitoring platform",
          "ships developer infrastructure for deploying code"], ["engineering teams", "platform engineers", "startups"]),
        ("HR software", "normal",
         ["sells payroll and HR management software", "runs an employee onboarding platform",
          "builds performance review software"], ["HR teams", "mid-size employers", "people managers"]),
    ],
    "ecommerce": [
        ("D2C apparel", "poor",
         ["sells sustainable clothing online", "runs a direct-to-consumer fashion label", "sells streetwear through its own store"],
         ["shoppers", "Gen Z customers", "fashion buyers"]),
        ("B2B marketplace", "normal",
         ["operates a wholesale marketplace", "connects suppliers with retail buyers", "runs a procurement marketplace"],
         ["retailers", "distributors", "procurement teams"]),
    ],
    "healthcare": [
        ("telehealth", "normal",
         ["runs a network of virtual clinics", "provides online doctor consultations", "offers remote patient monitoring"],
         ["patients", "employers", "insurers"]),
        ("medical billing", "great",
         ["automates medical claims and billing", "provides revenue cycle management software",
          "handles insurance claims processing"], ["hospitals", "clinics", "physician groups"]),
    ],
    "media": [
        ("podcast network", "normal",
         ["produces and distributes podcasts", "runs an audio content network", "sells podcast advertising"],
         ["listeners", "advertisers", "creators"]),
        ("digital publishing", "poor",
         ["publishes online news and newsletters", "runs a digital magazine", "operates content websites"],
         ["readers", "subscribers", "advertisers"]),
    ],
    "manufacturing": [
        ("industrial IoT", "great",
         ["builds sensors and predictive maintenance software", "connects factory machines to the cloud",
          "monitors industrial equipment in real time"], ["factories", "plant managers", "OEMs"]),
        ("contract manufacturing", "normal",
         ["provides contract electronics manufacturing", "runs precision machining for hardware brands",
          "offers small-batch production"], ["hardware startups", "consumer brands", "OEMs"]),
    ],
    "education": [
        ("coding bootcamp", "poor",
         ["runs online coding bootcamps", "teaches career switchers to code", "offers intensive programming courses"],
         ["career switchers", "students", "job seekers"]),
        ("K-12 learning", "normal",
         ["builds a learning platform for schools", "makes classroom management software",
          "provides online tutoring for school students"], ["schools", "teachers", "parents"]),
    ],
}
QUALITY_HEALTH = {"great": (9.0, 2.0), "normal": (7.0, 2.6), "poor": (4.5, 3.4)}  # Beta params for health
QUALITY_MRR = {"great": 1.3, "normal": 1.0, "poor": 0.8}
SIZE_WORD = [(20, "tiny"), (60, "small"), (600, "growing"), (10**9, "large")]

EXIT_REASONS = {
    "missing_integration": [
        "We needed a native Salesforce sync and it never shipped.",
        "No integration with NetSuite so our finance team couldn't use it.",
        "Couldn't connect it to our data warehouse, too much manual exporting.",
        "Lack of a HubSpot connector made it a dead end for our ops team.",
        "Our stack runs on Snowflake and the connector was missing.",
    ],
    "price_budget": [
        "Budget got cut this quarter and we had to drop tools.",
        "Too expensive for what we used it for.",
        "We are tightening spend and this was not essential.",
        "Price went up and finance would not approve the renewal.",
        "Couldn't justify the cost after our funding round fell through.",
    ],
    "low_adoption": [
        "The team never really adopted it.",
        "Only two people logged in, everyone else stayed in spreadsheets.",
        "We didn't have time to set it up properly.",
        "Onboarding was confusing and people gave up.",
    ],
    "competitor": [
        "Moved to a competitor that came bundled with our CRM.",
        "Switched to another vendor with a lower price.",
        "Our new CFO preferred a different tool.",
    ],
    "support_quality": [
        "Support took days to reply to urgent issues.",
        "Bugs in reporting kept breaking our board pack and nobody fixed them.",
        "We kept hitting errors and the help desk was slow.",
    ],
    "business_closed": [
        "We shut down the company.",
        "We were acquired and the parent company has its own tools.",
        "Our business pivoted and we no longer need this.",
    ],
}
NPS_PRAISE = [
    "Love the dashboards, saves us hours every week.", "Setup was fast and the support team is great.",
    "The forecasting is the best part, our board loves it.", "Easy to use, the whole team is on it.",
    "Receipts on every number made our CFO trust it.",
]
NPS_NEUTRAL = ["It's fine, does the job.", "Useful but a bit pricey.", "Good product, a few rough edges."]


def size_word(emp: int) -> str:
    return next(w for limit, w in SIZE_WORD if emp < limit)


class TextGen:
    def __init__(self, seed: int):
        self.rng = np.random.default_rng(seed)

    def niche(self, industry: str) -> tuple[str, str]:
        name, quality, _, _ = NICHES[industry][int(self.rng.integers(len(NICHES[industry])))]
        return name, quality

    def description(self, company: str, industry: str, niche: str, emp: int) -> str:
        _, _, does, serves = next(n for n in NICHES[industry] if n[0] == niche)
        r = self.rng
        templates = [
            "{c} is a {s} company that {d} for {w}.",
            "{c} {d} for {w}.",
            "A {s} {i} business: {c} {d} for {w}.",
            "{c} ({s}, {i}) {d}, mainly for {w}.",
        ]
        return templates[int(r.integers(len(templates)))].format(
            c=company, s=size_word(emp), d=does[int(r.integers(len(does)))], w=serves[int(r.integers(len(serves)))],
            i=industry)

    def exit_reason(self, segment: str, quality: str, health: float) -> tuple[str, str]:
        r = self.rng
        if quality == "poor" and r.random() < 0.55:
            theme = "price_budget"
        elif segment != "SMB" and r.random() < 0.45:
            theme = "missing_integration"
        elif health < 0.3 and r.random() < 0.5:
            theme = "low_adoption"
        else:
            theme = str(r.choice(list(EXIT_REASONS), p=[0.25, 0.2, 0.2, 0.15, 0.1, 0.1]))
        opts = EXIT_REASONS[theme]
        return theme, opts[int(r.integers(len(opts)))]

    def nps(self, health: float, segment: str) -> tuple[int, str]:
        r = self.rng
        score = int(np.clip(round(health * 11 + r.normal(0, 1.2) - 0.5), 0, 10))
        if score >= 9:
            return score, NPS_PRAISE[int(r.integers(len(NPS_PRAISE)))]
        if score >= 7:
            return score, NPS_NEUTRAL[int(r.integers(len(NPS_NEUTRAL)))]
        theme = "missing_integration" if segment != "SMB" and r.random() < 0.5 else \
            str(r.choice(["low_adoption", "support_quality", "price_budget"]))
        opts = EXIT_REASONS[theme]
        return score, opts[int(r.integers(len(opts)))].replace("We ", "We may ").replace("we had to", "we might")
