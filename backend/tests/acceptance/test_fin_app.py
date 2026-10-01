"""Test set 2 (financial formulas) and Test set 3 (approval / immutability)."""

from decimal import Decimal as D

import pytest

from app.services.economics import Assumptions, Line, compute
from tests.acceptance.helpers import preview
from tests.conftest import approved_cohort, assumptions, line, new_cohort


@pytest.fixture()
def base(client, world):
    return preview(client, world)["scenarios"]["base"]


def test_FIN_01_to_12_controlled_cohort(base):
    # Merchant-paid shipping is a direct cost; fees are a revenue deduction (net recognized revenue).
    assert base["gross_sale_proceeds"] == 18000  # FIN-01 60 x $300
    assert base["marketplace_fees"] == 1800  # FIN-02
    assert base["merchant_shipping_cost"] == 1200  # FIN-03 (direct cost)
    assert base["net_recognized_revenue"] == 16200  # FIN-04 gross - fees - refunds - discounts
    assert base["robot_cost"] == 2000  # FIN-05 40h x $50
    assert base["operating_direct_cost"] == 10900  # FIN-06 9,700 + 1,200 shipping
    assert base["contribution_margin"] == 5300  # FIN-07 (unchanged: same cash economics)
    assert round(base["cm_pct"], 4) == 0.3272  # FIN-08 CM / net recognized revenue (policy basis)
    assert round(base["cm_pct_gross_sale_proceeds"], 4) == 0.2944  # informational only
    assert base["margin_per_incoming_device"] == 53  # FIN-09
    assert round(base["margin_per_sale"], 2) == 88.33  # FIN-10
    assert base["cash_required"] == 9700  # FIN-12 cash out before any receipt
    assert base["units_sold"] == 60 and base["scheduled_robot_hours"] == 40


def test_FIN_11_break_even_price_and_sell_through_give_zero_margin(client, world, base):
    be_price, be_st = base["break_even_resale_price"], base["break_even_sell_through"]
    at_price = preview(
        client,
        world,
        lines=[
            line(
                units=100,
                process_minutes=24,
                exception_rate=0.2,
                first_pass_yield=0.8,
                repair_cost=50,
                resale_price=be_price,
                sell_through=0.75,
            )
        ],
    )
    assert abs(at_price["scenarios"]["base"]["contribution_margin"]) < 0.01
    at_st = preview(
        client,
        world,
        lines=[
            line(
                units=100,
                process_minutes=24,
                exception_rate=0.2,
                first_pass_yield=0.8,
                repair_cost=50,
                resale_price=300,
                sell_through=be_st,
            )
        ],
    )
    assert abs(at_st["scenarios"]["base"]["contribution_margin"]) < 0.01


def test_reconciliation_identities_hold_in_every_scenario(client, world):
    for scen in preview(client, world)["scenarios"].values():
        assert (
            abs(
                scen["contribution_margin"] - (scen["net_recognized_revenue"] - scen["operating_direct_cost"])
            )
            < 1e-6
        )
        net = (
            scen["gross_sale_proceeds"]
            - scen["refunds"]
            - scen["marketplace_fees"]
            - scen["discounts"]
            + scen["service_revenue"]
        )
        assert abs(scen["net_recognized_revenue"] - net) < 1e-6
        assert abs(scen["contribution_margin"] - (scen["revenue"] - scen["direct_cost"])) < 1e-6


def test_committed_vs_productive_robot_cost_views():
    ln = Line("x", 100, "B", D(24), D("0.2"), D("0.8"), D(50), D(300), D("0.75"))
    a = Assumptions(
        "supply_purchase", "operator_owned", (ln,), robot_cost_per_hour=D(50), planned_uptime=D("0.8")
    )
    e = compute(a)
    assert e["committed_robot_cost"] == D(50) * 50  # 40 productive h / 0.8 uptime = 50 scheduled h
    assert e["productive_robot_cost"] == D(50) * 40
    assert e["idle_capacity_leakage"] == D(50) * 10  # the cost of 10 idle scheduled hours


def test_FIN_13_no_float_error_on_cent_values():
    ln = Line("x", 3, "B", D(60), D(0), D(1), D(0), D("0.10"), D(1))
    a = Assumptions("supply_purchase", "operator_owned", (ln,), acquisition_cost_per_unit=D("0.01"), inbound_logistics_per_unit=D("0.01"),
                    marketplace_fee_rate=D(0), return_rate=D(0), robot_cost_per_hour=D(0), repair_recovery_rate=D(0), planned_uptime=D(1))  # fmt: skip
    e = compute(a)
    assert (
        e["revenue"] == D("0.30") and e["direct_cost"] == D("0.06") and e["contribution_margin"] == D("0.24")
    )  # exact, not 0.24000000000000002


@pytest.mark.parametrize(
    "bad",
    [
        dict(lines=[line(units=0)]),  # FIN-14 zero units
        dict(lines=[line(units=-5)]),  # FIN-15 negatives...
        dict(lines=[line(repair_cost=-1)]),
        dict(lines=[line(resale_price=-1)]),
        dict(lines=[line(process_minutes=-3)]),
        dict(outbound_shipping_per_unit=-1),
        dict(marketplace_fee_rate=1.2),  # FIN-16 fee > 100%
        dict(lines=[line(sell_through=1.01)]),  # FIN-17 sell-through > 100%
        dict(lines=[line(exception_rate=25)]),  # percent entered as 25 instead of 0.25
    ],
)
def test_FIN_14_to_17_invalid_inputs_are_rejected(client, world, bad):
    r = client.post(
        "/api/underwrite/preview",
        json=dict(partner_id=world["partner"]["id"], assumptions=assumptions(**bad)),
    )
    assert r.status_code == 422, r.text


def test_FIN_18_return_reverses_revenue_and_margin(client, world):
    c = approved_cohort(client, world, code="F18", units=100)
    client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][0], start_date="2026-06-10", end_date="2026-06-30"),
    )
    client.post("/api/operations/daily", json=dict(cohort_id=c["id"], robot_cell_id=world["cells"][0], operation_date="2026-06-11", devices_received=100,
                                                   devices_started=50, devices_completed=50, first_pass_completions=50, quality_approved_count=50, productive_robot_hours=8))  # fmt: skip
    s = client.post(
        "/api/sales-transactions",
        json=dict(
            cohort_id=c["id"],
            sale_date="2026-06-12",
            units_sold=10,
            gross_sale_amount=3000,
            marketplace_fee=300,
            shipping_cost=200,
        ),
    ).json()
    a0 = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    client.post(
        f"/api/sales-transactions/{s['id']}/return", json=dict(units=1, refund_amount=300, return_cost=20)
    )
    a1 = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    assert a1["revenue"] == a0["revenue"] - 300  # refund reverses revenue
    assert (
        abs(a1["contribution_margin"] - (a0["contribution_margin"] - 320)) < 1e-6
    )  # refund + return shipping hit margin
    assert a1["sales"]["units_returned"] == 1
    assert (
        abs(a1["contribution_margin"] - (a1["net_revenue"] - a1["operating_direct_cost"])) < 1e-6
    )  # identity still holds


# ---------------------------------------------------------------------------------------------- Test set 3
def test_APP_01_draft_cohort_reserves_no_capacity(client, world):
    c = new_cohort(client, world, code="A01")
    assert c["status"] == "draft"
    cal = client.get("/api/capacity/calendar?start=2026-06-01&end=2026-08-31").json()
    assert all(float(d["assigned"]) == 0 for cell in cal["cells"] for d in cell["days"])


def test_APP_02_03_04_recommendations(client, world):
    ok = client.post(
        "/api/underwrite/preview",
        json=dict(
            partner_id=world["partner"]["id"], contract_id=world["contract"]["id"], assumptions=assumptions()
        ),
    )
    assert ok.json()["decision"] == "accept"  # APP-02: healthy base, capacity not checked (no dates)
    assert (
        preview(client, world, acquisition_cost_per_unit=400)["decision"] == "decline"
    )  # APP-03 negative base margin
    weak = preview(client, world, acquisition_cost_per_unit=110, lines=[line(units=100, resale_price=300)])
    assert 0.15 <= weak["scenarios"]["base"]["cm_pct"] < 0.30 and weak["decision"] == "review"  # APP-04


def test_APP_05_to_08_freeze_reforecast_and_actuals_separate(client, world):
    c = approved_cohort(client, world, code="A05", units=100)
    assert client.get(f"/api/cohorts/{c['id']}").json()["status"] == "approved"
    versions = client.get(f"/api/cohorts/{c['id']}/budget-versions").json()
    plan = next(v for v in versions if v["is_approved"] and v["scenario_type"] == "base")
    # APP-06: no route edits a budget version; dates of an approved cohort are frozen; the model layer also refuses
    assert client.patch(f"/api/cohorts/{c['id']}", json=dict(intake_date="2026-07-01")).status_code == 409
    assert client.patch(f"/api/cohorts/{c['id']}/budget-versions/1", json={}).status_code in (404, 405)
    from app.errors import DomainError
    from app.models import CohortBudgetVersion
    from app.services.cohorts import assert_editable

    with pytest.raises(DomainError):
        assert_editable(CohortBudgetVersion(is_approved=True))
    # APP-07: reforecast with lower resale value creates a NEW version, plan untouched
    r = client.post(
        f"/api/cohorts/{c['id']}/budget-versions", json=assumptions(lines=[line(units=100, resale_price=150)])
    )
    assert r.status_code == 201 and r.json()["scenario_type"] == "reforecast"
    after = client.get(f"/api/cohorts/{c['id']}/budget-versions").json()
    plan_after = next(v for v in after if v["is_approved"] and v["scenario_type"] == "base")
    assert (
        plan_after["expected_contribution_margin"] == plan["expected_contribution_margin"]
        and plan_after["id"] == plan["id"]
    )
    rf = next(v for v in after if v["scenario_type"] == "reforecast")
    assert rf["expected_contribution_margin"] < plan["expected_contribution_margin"] and not rf["is_approved"]
    # APP-08: actual repair cost moves variance, not the plan
    client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][0], start_date="2026-06-16", end_date="2026-07-10"),
    )
    r = client.post("/api/operations/daily", json=dict(cohort_id=c["id"], robot_cell_id=world["cells"][0], operation_date="2026-06-15", devices_received=100,
                                                   devices_started=40, devices_completed=40, first_pass_completions=30, manual_exception_count=8,
                                                   quality_approved_count=40, productive_robot_hours=8, repair_parts_cost=5000))  # fmt: skip
    assert r.status_code == 201, r.text
    var = {x["key"]: x for x in client.get(f"/api/cohorts/{c['id']}/variance").json()["rows"]}
    assert var["repair_cost"]["actual"] == 5000 and var["repair_cost"]["status"] == "off_track"
    final = next(
        v
        for v in client.get(f"/api/cohorts/{c['id']}/budget-versions").json()
        if v["is_approved"] and v["scenario_type"] == "base"
    )
    assert final["expected_repair_cost"] == plan["expected_repair_cost"]


def test_APP_09_declined_cohort_cannot_receive_capacity(client, world):
    c = new_cohort(client, world, code="A09", acquisition_cost_per_unit=500)
    client.post(f"/api/cohorts/{c['id']}/underwrite")
    assert (
        client.post(
            f"/api/cohorts/{c['id']}/decline", json=dict(approver="cfo", rationale="negative margin")
        ).status_code
        == 200
    )
    r = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][0], start_date="2026-06-16", end_date="2026-06-30"),
    )
    assert r.status_code == 409 and r.json()["error"] == "not_approved"


def test_APP_10_audit_trail_has_actor_time_entity_and_states(client, world):
    a = approved_cohort(client, world, code="A10a", units=100)
    client.post(
        f"/api/cohorts/{a['id']}/budget-versions", json=assumptions(lines=[line(units=100, resale_price=200)])
    )
    b = new_cohort(client, world, code="A10b", acquisition_cost_per_unit=500)
    client.post(f"/api/cohorts/{b['id']}/underwrite")
    client.post(f"/api/cohorts/{b['id']}/decline", json=dict(approver="maya", rationale="policy"))
    log_a = client.get(f"/api/cohorts/{a['id']}/audit").json()
    approval = next(e for e in log_a if e["action"] == "approved")
    assert (
        approval["user_id"] == "cfo"
        and approval["entity_type"] == "cohort"
        and approval["created_at"]
        and approval["new_value"]["rationale"] == "meets policy"
    )
    assert any(e["action"] == "reforecast_created" for e in log_a)
    change = next(
        e for e in log_a if e["action"] == "status_change" and e["new_value"]["status"] == "approved"
    )
    assert change["old_value"] == {"status": "under_review"}
    log_b = client.get(f"/api/cohorts/{b['id']}/audit").json()
    assert next(e for e in log_b if e["action"] == "declined")["user_id"] == "maya"
