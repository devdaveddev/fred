from app.auth import hash_password, verify_password
from app.modules import MODULES, ROLE_TO_MODULE


def test_password_hash_roundtrip_and_salting():
    h1, h2 = hash_password("correct horse battery"), hash_password("correct horse battery")
    assert h1 != h2  # unique salt per hash
    assert verify_password("correct horse battery", h1)
    assert not verify_password("wrong password!!", h1)
    assert not verify_password("anything", "garbage")


def test_every_finding_role_maps_to_a_module():
    for role in ("marketing_analyst", "lead_gen", "founders_office", "growth", "market_researcher",
                 "business_analyst"):
        assert ROLE_TO_MODULE[role] in MODULES
    for m in MODULES.values():
        assert m.pages and m.delivers


def test_demo_ids_are_unique_per_workspace():
    import uuid
    from datetime import date

    from app.seed import generate, scope_ids

    a = scope_ids(generate(today=date(2026, 11, 12)), str(uuid.uuid4()))
    b = scope_ids(generate(today=date(2026, 11, 12)), str(uuid.uuid4()))
    assert not {x["id"] for x in a.accounts} & {x["id"] for x in b.accounts}
    assert not {x["id"] for x in a.leads} & {x["id"] for x in b.leads}
    assert a.plan["id"] != b.plan["id"]
    # references stay consistent inside one workspace
    ids = {x["id"] for x in a.accounts}
    assert {r["a"] for r in a.mrr} <= ids
    assert {l["account_id"] for l in a.leads if l["account_id"]} <= ids
    emp = {e["id"] for e in a.people.employees}
    assert {h["employee_id"] for h in a.people.planned_hires if h["employee_id"]} <= emp
