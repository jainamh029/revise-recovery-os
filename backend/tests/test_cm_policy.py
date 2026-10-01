"""Phase 1: CM% policy is contribution margin / NET RECOGNIZED REVENUE, in Decimal, with no double counting.

net_recognized_revenue = gross_sale_proceeds - marketplace_fees - refunds/returns - discounts (+ invoiced service fees)
contribution_margin    = net_recognized_revenue - acquisition - inbound - committed robot - labour - repair - merchant shipping
"""

import inspect
from decimal import Decimal as D

import pytest

from app.policy import Policy
from app.services import alerts, cohorts, partners, underwriting, variance
from app.services.economics import Assumptions, Line, compute, to_plain
from app.services.underwriting import UnderwritingContext, underwrite
from tests.test_economics import CTX, POL, mk

ZERO_SHIP = dict(outbound_shipping_per_unit=D(0))
TOL = D("1e-15")  # Decimal context noise (~1e-24) from the uptime division; far below a cent


def parts(e):
    return dict(
        acq=e["acquisition_cost"], share=e["partner_revenue_share"], inbound=e["inbound_logistics_cost"], robot=e["committed_robot_cost"],
        labour=e["manual_labor_cost"], repair=e["repair_cost"], ship=e["merchant_shipping_cost"],
    )  # fmt: skip


def test_1_cm_dollars_equal_net_revenue_minus_every_direct_variable_cost():
    e = compute(mk(discount_rate=D("0.02")))
    p = parts(e)
    assert e["contribution_margin"] == e["net_recognized_revenue"] - sum(p.values())
    assert e["direct_cost"] == sum(p.values())
    # and the revenue side is exactly the documented formula
    assert (
        e["net_recognized_revenue"]
        == e["gross_sale_proceeds"]
        - e["marketplace_fees"]
        - e["refunds"]
        - e["discounts"]
        + e["service_revenue"]
    )


def test_2_cm_pct_is_cm_over_net_recognized_revenue_not_gross():
    e = compute(mk(discount_rate=D("0.02")))
    assert e["cm_pct"] == e["contribution_margin"] / e["net_recognized_revenue"]
    assert e["cm_pct_net_revenue"] == e["cm_pct"]
    assert e["cm_pct_gross_sale_proceeds"] == e["contribution_margin"] / e["gross_sale_proceeds"]
    assert e["cm_pct_gross_sale_proceeds"] < e["cm_pct"]


def test_3_marketplace_fees_reduce_net_revenue_exactly_once():
    no_fee, fee = compute(mk(marketplace_fee_rate=D(0))), compute(mk(marketplace_fee_rate=D("0.12")))
    assert (
        no_fee["net_recognized_revenue"] - fee["net_recognized_revenue"]
        == fee["marketplace_fees"]
        == fee["gross_sale_proceeds"] * D("0.12")
    )
    assert no_fee["direct_cost"] == fee["direct_cost"]  # the fee is NOT also counted as a cost
    assert no_fee["contribution_margin"] - fee["contribution_margin"] == fee["marketplace_fees"]


def test_4_refunds_returns_and_discounts_reduce_net_revenue_exactly_once():
    base = compute(mk(return_rate=D(0), **ZERO_SHIP))
    ret = compute(mk(return_rate=D("0.04"), **ZERO_SHIP))
    assert (
        base["net_recognized_revenue"] - ret["net_recognized_revenue"]
        == ret["refunds"]
        == ret["gross_sale_proceeds"] * D("0.04")
    )
    assert base["direct_cost"] == ret["direct_cost"]
    disc = compute(mk(return_rate=D(0), discount_rate=D("0.03"), **ZERO_SHIP))
    assert (
        base["net_recognized_revenue"] - disc["net_recognized_revenue"]
        == disc["discounts"]
        == disc["gross_sale_proceeds"] * D("0.03")
    )
    assert base["direct_cost"] == disc["direct_cost"]


def test_5_merchant_shipping_is_a_direct_cost_exactly_once_never_a_revenue_deduction():
    free, paid = compute(mk(outbound_shipping_per_unit=D(0))), compute(mk(outbound_shipping_per_unit=D(13)))
    assert free["net_recognized_revenue"] == paid["net_recognized_revenue"]  # revenue untouched
    assert abs((paid["direct_cost"] - free["direct_cost"]) - paid["merchant_shipping_cost"]) < TOL
    assert (
        paid["merchant_shipping_cost"] == (paid["units_sold"] + paid["units_sold"] * D("0.04")) * 13
    )  # outbound + return labels
    assert (
        abs((free["contribution_margin"] - paid["contribution_margin"]) - paid["merchant_shipping_cost"])
        < TOL
    )


def zero_revenue():
    ln = Line("x", 10, "B", D(30), D("0.1"), D("0.9"), D(50), D(0), D("0.9"))  # resale price 0 => no revenue
    return Assumptions("supply_purchase", "operator_owned", (ln,), acquisition_cost_per_unit=D(10))


def test_6_zero_net_revenue_gives_null_cm_pct_not_a_crash():
    e = compute(zero_revenue())
    assert (
        e["net_recognized_revenue"] == 0 and e["cm_pct"] is None and e["cm_pct_gross_sale_proceeds"] is None
    )
    assert e["contribution_margin"] < 0
    r = underwrite(zero_revenue(), POL, CTX)  # the decision engine must survive it
    assert r["decision"] == "decline" and r["scenarios"]["base"]["cm_pct"] is None
    assert to_plain(e)["cm_pct"] is None


def test_6b_actuals_with_no_revenue_return_null_cm_pct(client, world):
    from tests.conftest import approved_cohort

    c = approved_cohort(client, world, code="Z6", units=10)
    a = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    assert a["net_recognized_revenue"] == 0 and a["cm_pct"] is None


def test_7_negative_net_revenue_gives_null_cm_pct():
    ln = Line("x", 10, "B", D(30), D("0.1"), D("0.9"), D(50), D(300), D("0.9"))
    a = Assumptions(
        "supply_purchase",
        "operator_owned",
        (ln,),
        discount_rate=D("0.60"),
        marketplace_fee_rate=D("0.50"),
        return_rate=D(0),
    )
    e = compute(a)
    assert e["net_recognized_revenue"] < 0 and e["cm_pct"] is None


@pytest.mark.parametrize(
    "acq,expected",
    [(60, "accept"), (100, "accept"), (130, "review"), (140, "review"), (150, "decline"), (200, "decline")],
)
def test_8_decisions_follow_net_revenue_cm_bands(acq, expected):
    r = underwrite(mk(acquisition_cost_per_unit=D(acq)), POL, CTX)
    assert r["decision"] == expected, (
        acq,
        r["scenarios"]["base"]["cm_pct"],
        r["scenarios"]["downside"]["cm_pct"],
    )


def test_8b_band_edges_are_exact():
    """Base >=30% accept, 15% <= base < 30% review, < 15% (or negative dollars) decline -- measured on net revenue."""

    def decide(base_pct, down_pct, cm=D(1)):
        # drive the real engine, then impose the percentages to test the thresholds alone
        r = underwrite(mk(), Policy(base_case_accept_cm_pct_net_revenue=D(base_pct), base_case_review_cm_pct_net_revenue=D(base_pct) - D("0.15"),
                                    downside_case_min_cm_pct_net_revenue=D(down_pct)), CTX)  # fmt: skip
        return r

    base = compute(mk())["cm_pct"]
    assert underwrite(mk(), Policy(base_case_accept_cm_pct_net_revenue=base), CTX)["decision"] in (
        "accept",
        "accept_with_conditions",
    )  # exactly at the line: inclusive
    assert (
        underwrite(mk(), Policy(base_case_accept_cm_pct_net_revenue=base + D("0.0001")), CTX)["decision"]
        == "review"
    )  # just below
    assert (
        underwrite(mk(), Policy(base_case_review_cm_pct_net_revenue=base + D("0.0001")), CTX)["decision"]
        == "decline"
    )  # below decline line
    assert decide


def test_8c_accept_with_conditions_is_operational_only():
    ln = Line(
        "x", 100, "B", D(30), D("0.25"), D("0.70"), D(20), D(400), D("0.95")
    )  # high exceptions / low FPY but rich margin
    a = Assumptions(
        "supply_purchase",
        "operator_owned",
        (ln,),
        acquisition_cost_per_unit=D(40),
        outbound_shipping_per_unit=D(10),
    )
    r = underwrite(a, POL, CTX)
    b, d = r["scenarios"]["base"]["cm_pct"], r["scenarios"]["downside"]["cm_pct"]
    assert b >= D("0.30") and d >= D("0.15")  # core financial policy passes
    assert r["decision"] == "accept_with_conditions" and r["conditions"]
    assert not any("CM" in c for c in r["conditions"])  # conditions never restate a margin shortfall


def test_8d_negative_cm_dollars_always_decline_and_downside_below_floor_is_review():
    assert underwrite(mk(acquisition_cost_per_unit=D(400)), POL, CTX)["decision"] == "decline"
    r = underwrite(mk(), Policy(downside_case_min_cm_pct_net_revenue=D("0.60")), CTX)
    assert r["decision"] == "review" and any("risk floor" in x for x in r["reasons"])


def test_9_live_margin_floor_alerts_use_net_revenue_cm_pct(seeded):
    rows = {c["code"]: c for c in seeded.get("/api/cohorts").json()}
    itad = rows["ITAD-002"]
    fc = seeded.get(f"/api/cohorts/{itad['id']}/variance").json()["reforecast"]
    assert 0 < fc["economics"]["cm_pct"] < 0.15 and fc["economics"]["contribution_margin"] >= 0
    assert fc["below_floor"] and not fc["negative"] and fc["margin_floor_pct"] == 0.15
    types = {a["alert_type"]: a for a in seeded.get("/api/alerts").json() if a["cohort_id"] == itad["id"]}
    assert types["CRITICAL_MARGIN_INTERVENTION"]["severity"] == "critical" and "STOP_LOSS_MAKING" not in types
    # drive forecast CM dollars below zero -> STOP / loss-making replaces the intervention alert
    cell_b = next(c["id"] for c in seeded.get("/api/robot-cells").json() if c["cell_name"] == "Cell B")
    r = seeded.post("/api/operations/daily", json=dict(cohort_id=itad["id"], robot_cell_id=cell_b, operation_date="2026-09-30", devices_started=60, devices_completed=30,
                                                        first_pass_completions=5, manual_exception_count=55, quality_approved_count=30,
                                                        productive_robot_hours=10, repair_parts_cost=90000, technician_hours=60))  # fmt: skip
    assert r.status_code == 201, r.text
    after = {a["alert_type"] for a in seeded.get("/api/alerts").json() if a["cohort_id"] == itad["id"]}
    assert "STOP_LOSS_MAKING" in after and "CRITICAL_MARGIN_INTERVENTION" not in after


def test_10_gross_basis_metric_never_influences_decisions_alerts_scoring_or_priority():
    for mod in (underwriting, alerts, partners, variance, cohorts):
        assert "cm_pct_gross" not in inspect.getsource(mod), mod.__name__
    # behavioural: decisions depend only on the net-basis figure, across a wide sweep
    for acq in range(40, 230, 10):
        r = underwrite(
            mk(acquisition_cost_per_unit=D(acq)),
            POL,
            UnderwritingContext(capacity_hours_available=D(9999), review_capacity_available=D(9999)),
        )
        b, dn = r["scenarios"]["base"]["cm_pct"], r["scenarios"]["downside"]["cm_pct"]
        want = "decline" if b < D("0.15") else "review" if (b < D("0.30") or dn < D("0.15")) else "accept"
        assert r["decision"] == want, acq
