"""revcore: deterministic revenue math + seeded Monte Carlo scenarios.

Every function is pure: same inputs -> same outputs. No LLM touches this package.
The version string travels in every receipt; bump it whenever a formula changes.
"""
from .formulas import (  # noqa: F401
    FORMULA_VERSION,
    FORMULAS,
    burn_multiple,
    cac,
    cac_payback_months,
    cash_conversion_cycle,
    grr,
    ltv_cac,
    ltv_wacc,
    magic_number,
    nrr,
    rule_of_40,
    runway_months,
    som,
)
from .simulate import Baseline, Shock, compare, simulate  # noqa: F401
