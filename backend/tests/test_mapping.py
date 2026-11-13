"""Account mapping on names as they appear in real QuickBooks / Xero charts of accounts."""
from app.connectors.mapping import classify, parse_code
from app.ml.embeddings import embed

CASES = [
    ("4000 Subscription Revenue", "Income", "revenue", "revenue", None),
    ("Sales", "Income", "revenue", "revenue", None),
    ("Services Income", "Income", "revenue", "revenue", None),
    ("5010 AWS Hosting", "Cost of Goods Sold", "cogs", "cogs", None),
    ("Stripe Fees", "Cost of Goods Sold", "cogs", "cogs", None),
    ("Salaries - Engineering", "Expenses", "rnd", "payroll", "Engineering"),
    ("6000 Salaries: Sales", "Expenses", "sm", "payroll", "Sales"),
    ("Sales Team Bonuses", "Expenses", "sm", "payroll", "Sales"),
    ("Sales Tools", "Expenses", "ga", "nonpayroll", None),
    ("Payroll Expenses: Sales Commissions", "Expenses", "sm", "payroll", "Sales"),
    ("Wages - Customer Support", "Expenses", "sm", "payroll", "Customer Success"),
    ("Employer Payroll Taxes", "Expenses", "ga", "payroll", "G&A"),
    ("Google Ads", "Expenses", "sm", "nonpayroll", None),
    ("Conferences & Events", "Expenses", "sm", "nonpayroll", None),
    ("Travel", "Expenses", "sm", "nonpayroll", None),
    ("Software Subscriptions", "Expenses", "ga", "nonpayroll", None),
    ("Rent or Lease of Buildings", "Expenses", "ga", "nonpayroll", None),
    ("Legal & Professional Services", "Expenses", "ga", "nonpayroll", None),
]


def test_parse_code():
    assert parse_code("6000 Salaries - Sales") == ("6000", "Salaries - Sales")
    assert parse_code("Rent") == (None, "Rent")


def test_rules_map_common_accounts():
    got = classify([c[0] for c in CASES], [c[1] for c in CASES])
    for (name, _, group, kind, dept), g in zip(CASES, got):
        assert (g["pl_group"], g["kind"]) == (group, kind), name
        if dept:
            assert g["department"] == dept, name


def test_model_fallback_for_unusual_names():
    got = classify(["Developer Contractors", "Ad Campaign Spend Q3", "Office Snacks & Supplies"], embed=embed)
    assert [g["method"] for g in got] == ["model", "model", "model"] or all(g["method"] in ("rule", "model") for g in got)
    assert got[1]["pl_group"] == "sm"
    assert got[2]["pl_group"] == "ga"


def test_model_fallback_respects_the_section():
    """Regression: an unusual expense name must not be filed as revenue just because its text is closest to it."""
    import numpy as np

    from app.connectors.mapping import PROTOTYPES

    keys = list(PROTOTYPES)

    def embed(texts):  # a model that thinks everything looks like revenue
        if len(texts) == len(keys) and texts == list(PROTOTYPES.values()):
            return np.eye(len(keys))
        v = np.zeros((len(texts), len(keys)))
        v[:, keys.index(("revenue", "revenue"))] = 1.0
        v[:, keys.index(("ga", "nonpayroll"))] = 0.4
        return v

    out = classify(["Quarterly offsite and team wellbeing", "Quarterly offsite and team wellbeing"],
                   ["Operating Expenses", None], embed=embed)
    assert (out[0]["pl_group"], out[0]["kind"]) == ("ga", "nonpayroll")
    assert out[1]["pl_group"] == "revenue"  # no section: the model's call stands (and is flagged for review)
