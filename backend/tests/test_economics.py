from decimal import Decimal as D

from app.policy import Policy
from app.services.economics import SCENARIOS, Assumptions, Line, Shock, compute, q2
from app.services.underwriting import UnderwritingContext, underwrite

POL = Policy()


def mk(**kw) -> Assumptions:
    ln = Line("Latitude", 100, "B", D(30), D("0.10"), D("0.90"), D(50), D(300), D("0.90"))
    base = dict(
        contract_type="supply_purchase",
        ownership="operator_owned",
        lines=(ln,),
        acquisition_cost_per_unit=D(60),
        inbound_logistics_per_unit=D(3),
        outbound_shipping_per_unit=D(13),
        marketplace_fee_rate=D("0.12"),
        return_rate=D("0.04"),
    )
    base.update(kw)
    return Assumptions(**base)


def test_contribution_margin_hand_calculation():
    e = compute(mk())
    # sellable = 100 * (0.90 + 0.10*0.85) = 98.5 ; sold = 98.5 * 0.90 = 88.65 ; gross = 88.65*300
    assert e["units_sellable"] == D("98.5")
    assert e["units_sold"] == D("88.65")
    gross = D("88.65") * 300
    assert e["gross_sale_proceeds"] == gross
    assert e["refunds"] == gross * D("0.04")
    fees = gross * D("0.12")
    assert e["marketplace_fees"] == fees
    net = gross - gross * D("0.04") - fees
    assert e["net_recognized_revenue"] == net
    outbound = D("88.65") * D("1.04") * 13  # merchant-paid: a DIRECT COST, not a revenue deduction
    robot = (D(100) * 30 / 60) / D("0.9") * 42
    manual = D(10) * 35 / 60 * 32
    repair = D(10) * 50
    cost = 100 * 60 + 100 * 3 + robot + manual + repair + outbound
    assert abs(e["direct_cost"] - cost) < D("0.0001")
    assert abs(e["contribution_margin"] - (net - cost)) < D("0.0001")
    assert e["cm_pct"] == e["contribution_margin"] / net
    assert e["cm_per_robot_hour"] == e["contribution_margin"] / D(50)
    assert e["margin_per_incoming_device"] == e["contribution_margin"] / 100


def test_break_even_resale_price_yields_zero_margin():
    a = mk()
    be = compute(a)["break_even_resale_price"]
    ln = a.lines[0]
    at_be = mk(
        lines=(
            Line(
                ln.model_name,
                ln.units,
                "B",
                ln.process_minutes,
                ln.exception_rate,
                ln.first_pass_yield,
                ln.repair_cost,
                be,
                ln.sell_through,
            ),
        )
    )
    assert abs(compute(at_be)["contribution_margin"]) < D("0.01")


def test_break_even_sell_through_yields_zero_margin():
    a = mk()
    be = compute(a)["break_even_sell_through"]
    ln = a.lines[0]
    at_be = mk(
        lines=(
            Line(
                ln.model_name,
                ln.units,
                "B",
                ln.process_minutes,
                ln.exception_rate,
                ln.first_pass_yield,
                ln.repair_cost,
                ln.resale_price,
                be,
            ),
        )
    )
    assert abs(compute(at_be)["contribution_margin"]) < D("0.01")


def test_cash_required_excludes_sale_side_costs():
    e = compute(mk())
    assert (
        e["cash_required"]
        == e["acquisition_cost"]
        + e["inbound_logistics_cost"]
        + e["robot_cost"]
        + e["manual_labor_cost"]
        + e["repair_cost"]
    )


def test_scenarios_are_ordered():
    a = mk()
    d, b, u = (compute(a, SCENARIOS[s])["contribution_margin"] for s in ("downside", "base", "upside"))
    assert d < b < u


def test_uptime_reduces_margin_because_idle_cells_still_cost_money():
    hi, lo = compute(mk(), Shock(uptime=D("0.95"))), compute(mk(), Shock(uptime=D("0.60")))
    assert lo["robot_cost"] > hi["robot_cost"] and lo["contribution_margin"] < hi["contribution_margin"]


def test_processing_service_revenue_is_fee_based():
    a = mk(
        contract_type="processing_service",
        ownership="partner_owned",
        service_fee_per_unit=D(25),
        performance_fee=D(500),
    )
    e = compute(a)
    assert e["revenue"] == D(100) * 25 + 500 and e["acquisition_cost"] == 0 and e["marketplace_fees"] == 0


def test_money_rounding_only_at_presentation():
    assert q2(D("10.005")) == D("10.01") and isinstance(compute(mk())["revenue"], D)


# ---- acceptance logic -------------------------------------------------------------------------
CTX = UnderwritingContext(capacity_hours_available=D(9999), review_capacity_available=D(9999))


def test_accept_when_everything_passes():
    assert underwrite(mk(), POL, CTX)["decision"] == "accept"


def test_negative_base_margin_declines():
    assert underwrite(mk(acquisition_cost_per_unit=D(400)), POL, CTX)["decision"] == "decline"


def test_weak_downside_requires_review_even_when_base_clears_the_accept_line():
    # Base CM% is at/above 30% but the downside case is below the risk floor -> Review, never Accept.
    strict = Policy(downside_case_min_cm_pct_net_revenue=D("0.25"))
    r = underwrite(mk(acquisition_cost_per_unit=D(100)), strict, CTX)
    base, down = r["scenarios"]["base"]["cm_pct"], r["scenarios"]["downside"]["cm_pct"]
    assert base >= D("0.30") and down < D("0.25")
    assert r["decision"] == "review" and any("risk floor" in x for x in r["reasons"])


def test_downside_floor_is_configurable():
    strict = Policy(downside_case_min_cm_pct_net_revenue=D("0.60"))
    assert underwrite(mk(), strict, CTX)["decision"] == "review"
    lax = Policy(downside_case_min_cm_pct_net_revenue=D("0.01"))
    assert underwrite(mk(), lax, CTX)["decision"] == "accept"


def test_loss_making_base_case_declines():
    ln = Line("Acer", 100, "C", D(40), D("0.30"), D("0.65"), D(110), D(120), D("0.80"))
    r = underwrite(mk(lines=(ln,), acquisition_cost_per_unit=D(70)), POL, CTX)
    assert r["scenarios"]["base"]["contribution_margin"] < 0 and r["decision"] == "decline"


def test_capacity_shortfall_requires_review():
    r = underwrite(
        mk(), POL, UnderwritingContext(capacity_hours_available=D(10), review_capacity_available=D(9999))
    )
    assert r["decision"] == "review" and any("capacity" in x.lower() for x in r["reasons"])


def test_review_capacity_shortfall_requires_review():
    r = underwrite(
        mk(), POL, UnderwritingContext(capacity_hours_available=D(9999), review_capacity_available=D(3))
    )
    assert r["decision"] == "review"


def test_working_capital_limit_requires_review():
    r = underwrite(
        mk(),
        POL,
        UnderwritingContext(
            capacity_hours_available=D(9999),
            review_capacity_available=D(9999),
            portfolio_cash_exposure=D(500000),
        ),
    )
    assert r["decision"] == "review"


def test_unsupported_wipe_level_declines():
    r = underwrite(
        mk(),
        POL,
        UnderwritingContext(
            wipe_level="nist_purge_certified",
            capacity_hours_available=D(9999),
            review_capacity_available=D(9999),
        ),
    )
    assert r["decision"] == "decline"


def test_poor_quality_declines():
    bad = mk(lines=(Line("Junk", 100, "D", D(40), D("0.10"), D("0.90"), D(50), D(300), D("0.9")),))
    assert underwrite(bad, POL, CTX)["decision"] == "decline"


def test_base_margin_between_decline_and_accept_lines_is_review_with_a_reprice_hint():
    r = underwrite(mk(acquisition_cost_per_unit=D(130)), POL, CTX)
    assert D("0.15") <= r["scenarios"]["base"]["cm_pct"] < D("0.30")
    assert r["decision"] == "review" and any("Reprice" in x for x in r["reasons"])
