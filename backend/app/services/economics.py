"""Pure, deterministic cohort economics. No DB, no I/O, Decimal everywhere.

Modelling notes (documented deviations from the literal PRD formula):
  * Sellable units = Units x (FPY + (1 - FPY) x repair_recovery_rate). Devices that miss first pass
    are repaired/reworked and a share of them is recovered; the PRD formula (Units x FPY x ...) is the
    special case repair_recovery_rate = 0.
  * expected_revenue is gross resale revenue net of returns. Marketplace fees, outbound shipping and
    return shipping are direct costs (so every PRD cost line is visible), not netted into revenue.
  * Robot cost accrues on *scheduled* hours (productive hours / planned uptime): a cell costs money
    while it is down, which is how uptime reaches margin.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

D = Decimal
ZERO = D(0)
ONE = D(1)
CENT = D("0.01")

CONDITION_INDEX = {"A": D("1.0"), "B": D("0.75"), "C": D("0.45"), "D": D("0.15")}


def d(x) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return D(str(x)) if x is not None else ZERO


def q2(x: Decimal) -> Decimal:
    """Round to cents -- only at presentation / ledger posting."""
    return d(x).quantize(CENT, rounding=ROUND_HALF_UP)


def clamp(x: Decimal, lo: Decimal = ZERO, hi: Decimal = ONE) -> Decimal:
    return max(lo, min(hi, x))


def safe_div(a: Decimal, b: Decimal, default: Decimal | None = None) -> Decimal | None:
    return default if b == 0 else a / b


@dataclass(frozen=True)
class Line:
    model_name: str
    units: int
    condition_grade: str
    process_minutes: Decimal
    exception_rate: Decimal
    first_pass_yield: Decimal
    repair_cost: Decimal  # per exception unit
    resale_price: Decimal  # gross, per sold unit
    sell_through: Decimal
    device_model_profile_id: str | None = None


@dataclass(frozen=True)
class Assumptions:
    contract_type: str  # supply_purchase | processing_service | revenue_share | hybrid
    ownership: str  # operator_owned | partner_owned | shared | unknown
    lines: tuple[Line, ...]
    acquisition_cost_per_unit: Decimal = ZERO
    inbound_logistics_per_unit: Decimal = ZERO
    outbound_shipping_per_unit: Decimal = ZERO
    marketplace_fee_rate: Decimal = D("0.12")
    return_rate: Decimal = D("0.04")
    discount_rate: Decimal = ZERO  # promotional discounts as a share of gross sale proceeds
    service_fee_per_unit: Decimal = ZERO
    revenue_share_pct: Decimal = ZERO
    performance_fee: Decimal = ZERO
    repair_recovery_rate: Decimal = D("0.85")
    robot_cost_per_hour: Decimal = D("42")
    labor_rate_per_hour: Decimal = D("32")
    manual_minutes_per_exception: Decimal = D("35")
    planned_uptime: Decimal = D("0.90")
    days_to_sale: int = 45
    payout_days: int = 7  # marketplace payout lag / partner payment terms
    margin_floor_pct: Decimal = D("0.10")

    @property
    def units(self) -> int:
        return sum(ln.units for ln in self.lines)


@dataclass(frozen=True)
class Shock:
    """Multiplicative perturbation applied to a scenario (downside / upside / sliders / reforecast)."""

    price_mult: Decimal = ONE
    sell_through_mult: Decimal = ONE
    exception_mult: Decimal = ONE
    process_mult: Decimal = ONE
    repair_mult: Decimal = ONE
    volume_mult: Decimal = ONE
    uptime: Decimal | None = None  # absolute override
    return_rate: Decimal | None = None  # absolute override


SCENARIOS: dict[str, Shock] = {
    "base": Shock(),
    "downside": Shock(
        price_mult=D("0.92"),
        sell_through_mult=D("0.93"),
        exception_mult=D("1.30"),
        process_mult=D("1.10"),
        repair_mult=D("1.20"),
    ),
    "upside": Shock(
        price_mult=D("1.05"),
        sell_through_mult=D("1.04"),
        exception_mult=D("0.85"),
        process_mult=D("0.95"),
        repair_mult=D("0.90"),
    ),
}


def compute(a: Assumptions, shock: Shock = Shock()) -> dict:
    """Compute full cohort economics for one scenario. Returns Decimals (unrounded)."""
    uptime = clamp(shock.uptime if shock.uptime is not None else a.planned_uptime, D("0.05"), ONE)
    return_rate = clamp(shock.return_rate if shock.return_rate is not None else a.return_rate)
    operator_resells = a.ownership != "partner_owned" or a.contract_type == "revenue_share"

    received = processed = ZERO
    productive_hours = exception_units = sellable = sold = gross = ZERO
    manual_cost = repair_cost = ZERO
    quality_num = ZERO
    per_line = []

    for ln in a.lines:
        u = d(ln.units) * shock.volume_mult
        exc = clamp(ln.exception_rate * shock.exception_mult)
        fpy = clamp(ln.first_pass_yield - (exc - ln.exception_rate))
        mins = ln.process_minutes * shock.process_mult
        st = clamp(ln.sell_through * shock.sell_through_mult)
        price = ln.resale_price * shock.price_mult

        hrs = u * mins / 60
        exc_u = u * exc
        sell_u = u * (fpy + (ONE - fpy) * a.repair_recovery_rate)
        sold_u = sell_u * st
        l_repair = exc_u * ln.repair_cost * shock.repair_mult
        l_manual = exc_u * a.manual_minutes_per_exception / 60 * a.labor_rate_per_hour

        received += u
        processed += u
        productive_hours += hrs
        exception_units += exc_u
        sellable += sell_u
        sold += sold_u
        gross += sold_u * price
        repair_cost += l_repair
        manual_cost += l_manual
        quality_num += u * CONDITION_INDEX.get(ln.condition_grade.upper(), D("0.5"))
        per_line.append(
            {
                "model_name": ln.model_name,
                "units": float(u),
                "hours": float(hrs),
                "exception_units": float(exc_u),
                "sold_units": float(sold_u),
                "gross": float(sold_u * price),
                "repair": float(l_repair),
            }
        )

    scheduled_hours = productive_hours / uptime
    robot_cost = scheduled_hours * a.robot_cost_per_hour
    inbound = received * a.inbound_logistics_per_unit
    returned_units = sold * return_rate
    # ---- revenue side (Decimal throughout) ---------------------------------------------------------------
    #   net_recognized_revenue = gross_sale_proceeds - marketplace_fees - refunds/returns - discounts (+ invoiced service fees)
    # Each deduction is taken exactly once. Merchant-paid shipping is NOT a revenue deduction: it is a direct variable cost.
    discounts = gross * a.discount_rate
    refunds = gross * return_rate
    fees = gross * a.marketplace_fee_rate if operator_resells else ZERO
    shipping = (sold + returned_units) * a.outbound_shipping_per_unit if operator_resells else ZERO
    service_revenue = processed * a.service_fee_per_unit + a.performance_fee

    acquisition = ZERO
    partner_share = ZERO
    gross_proceeds = gross
    if a.ownership == "partner_owned":
        if a.contract_type == "revenue_share":
            # The operator earns its share of the resale pool; fees, refunds and shipping are borne inside the pool,
            # so they are not separately deducted from (or charged to) the operator.
            pool = gross - discounts - refunds - fees - shipping
            resale_net = max(pool, ZERO) * a.revenue_share_pct
            gross_proceeds = resale_net
            discounts = refunds = fees = shipping = ZERO
        else:
            resale_net = gross_proceeds = discounts = refunds = fees = shipping = (
                ZERO  # partner keeps the resale
            )
    else:
        acquisition = received * a.acquisition_cost_per_unit
        resale_net = gross - discounts - refunds - fees
        if a.contract_type in ("revenue_share", "hybrid") and a.revenue_share_pct > 0:
            partner_share = (gross - discounts - refunds) * a.revenue_share_pct

    net_revenue = resale_net + service_revenue
    direct_cost = acquisition + partner_share + inbound + robot_cost + manual_cost + repair_cost + shipping
    cm = net_revenue - direct_cost
    # Authoritative policy metric. Undefined (None) when net revenue is zero or negative -- never inf / NaN / exception.
    cm_pct = cm / net_revenue if net_revenue > 0 else None
    cm_pct_gross = (
        cm / gross_proceeds if gross_proceeds > 0 else None
    )  # informational only; never used by policy

    # Cash the operator must put up before any recovery arrives.
    cash_required = acquisition + inbound + robot_cost + manual_cost + repair_cost

    # Break-even resale price / sell-through (variable: fees, shipping, returns scale with sales).
    fixed = acquisition + inbound + robot_cost + manual_cost + repair_cost + partner_share
    avg_price = safe_div(gross, sold, ZERO)
    per_sold_net = (
        ONE - return_rate - a.discount_rate - a.marketplace_fee_rate
    )  # net recognised revenue per $ of gross price
    # Solve sold x (P x per_sold_net - ship x (1 + r)) = fixed - service_revenue for P.
    ship_per_sold = a.outbound_shipping_per_unit * (ONE + return_rate)
    if operator_resells and a.ownership != "partner_owned" and sold > 0 and per_sold_net > 0:
        be_price = ((fixed - service_revenue) / sold + ship_per_sold) / per_sold_net
    else:
        be_price = None
    contribution_per_sold = (avg_price * per_sold_net - ship_per_sold) if sold > 0 else ZERO
    be_sell_through = None
    if sellable > 0 and contribution_per_sold > 0 and operator_resells and a.ownership != "partner_owned":
        be_sell_through = max((fixed - service_revenue) / contribution_per_sold / sellable, ZERO)

    collection_days = a.days_to_sale + a.payout_days if resale_net > 0 else a.payout_days

    quality_index = safe_div(quality_num, received, ZERO)
    blended_exc = safe_div(exception_units, processed, ZERO)
    sell_through_blend = safe_div(sold, sellable, ZERO)
    fpy_blend = safe_div(
        sum(
            d(ln.units)
            * shock.volume_mult
            * clamp(
                ln.first_pass_yield - (clamp(ln.exception_rate * shock.exception_mult) - ln.exception_rate)
            )
            for ln in a.lines
        ),
        processed,
        ZERO,
    )

    return {
        "units_received": received,
        "units_processed": processed,
        "units_sellable": sellable,
        "units_sold": sold,
        "exception_units": exception_units,
        "exception_rate": blended_exc,
        "first_pass_yield": fpy_blend,
        "sell_through": sell_through_blend,
        "quality_index": quality_index,
        "robot_hours": productive_hours,
        "scheduled_robot_hours": scheduled_hours,
        "avg_resale_price": avg_price,
        "gross_sale_proceeds": gross_proceeds,
        "marketplace_fees": fees,
        "refunds": refunds,
        "discounts": discounts,
        "net_recognized_revenue": net_revenue,
        "revenue": net_revenue,  # alias of net_recognized_revenue (kept for older consumers)
        "resale_revenue": resale_net,
        "service_revenue": service_revenue,
        "acquisition_cost": acquisition,
        "partner_revenue_share": partner_share,
        "inbound_logistics_cost": inbound,
        "robot_cost": robot_cost,
        "committed_robot_cost": robot_cost,  # scheduled hours x hourly cost: the cohort's economic commitment
        "productive_robot_cost": productive_hours * a.robot_cost_per_hour,
        "idle_capacity_leakage": robot_cost - productive_hours * a.robot_cost_per_hour,
        "manual_labor_cost": manual_cost,
        "repair_cost": repair_cost,
        "merchant_shipping_cost": shipping,
        "outbound_fulfillment_cost": shipping,  # alias of merchant_shipping_cost
        "direct_cost": direct_cost,
        "operating_direct_cost": direct_cost,
        "contribution_margin": cm,
        "cm_pct": cm_pct,  # CM / net recognised revenue  (authoritative policy basis)
        "cm_pct_net_revenue": cm_pct,  # explicit alias
        "cm_pct_gross_sale_proceeds": cm_pct_gross,  # informational only
        "margin_per_incoming_device": safe_div(cm, received, ZERO),
        "margin_per_sale": safe_div(cm, sold, ZERO),
        "cm_per_robot_hour": safe_div(cm, productive_hours, ZERO),
        "cash_required": cash_required,
        "break_even_resale_price": be_price,
        "break_even_sell_through": be_sell_through,
        "collection_days": int(collection_days),
        "per_line": per_line,
    }


def compute_scenarios(a: Assumptions) -> dict[str, dict]:
    return {name: compute(a, s) for name, s in SCENARIOS.items()}


def quality_from_mix(lines: tuple[Line, ...]) -> Decimal:
    units = sum(ln.units for ln in lines)
    if not units:
        return ZERO
    return (
        sum(d(ln.units) * CONDITION_INDEX.get(ln.condition_grade.upper(), D("0.5")) for ln in lines) / units
    )


def max_acquisition_for_margin(a: Assumptions, target_cm_pct: Decimal) -> Decimal | None:
    """Highest acquisition cost/unit that still yields target CM% in the base case (linear in acquisition)."""
    if a.ownership == "partner_owned":
        return None
    base = compute(a)
    rev = base["net_recognized_revenue"]
    units = base["units_received"]
    if units == 0 or rev <= 0:
        return None
    allowed_cost = rev * (ONE - target_cm_pct)
    excess = base["direct_cost"] - allowed_cost
    return max(a.acquisition_cost_per_unit - excess / units, ZERO)


def with_lines(a: Assumptions, **changes) -> Assumptions:
    return replace(a, **changes)


def assumptions_from_dict(data: dict) -> Assumptions:
    lines = tuple(
        Line(
            model_name=ln["model_name"],
            units=int(ln["units"]),
            condition_grade=ln.get("condition_grade", "B"),
            process_minutes=d(ln["process_minutes"]),
            exception_rate=d(ln["exception_rate"]),
            first_pass_yield=d(ln["first_pass_yield"]),
            repair_cost=d(ln["repair_cost"]),
            resale_price=d(ln["resale_price"]),
            sell_through=d(ln["sell_through"]),
            device_model_profile_id=ln.get("device_model_profile_id"),
        )
        for ln in data["lines"]
    )
    simple = {k: v for k, v in data.items() if k not in ("lines",) and k in Assumptions.__dataclass_fields__}
    money_like = {
        k: (d(v) if k not in ("contract_type", "ownership", "days_to_sale", "payout_days") else v)
        for k, v in simple.items()
    }
    for k in ("days_to_sale", "payout_days"):
        if k in money_like:
            money_like[k] = int(money_like[k])
    return Assumptions(lines=lines, **money_like)


def assumptions_to_dict(a: Assumptions) -> dict:
    out: dict = {}
    for k, v in a.__dict__.items():
        if k == "lines":
            out["lines"] = [
                {
                    **{fk: (str(fv) if isinstance(fv, Decimal) else fv) for fk, fv in ln.__dict__.items()},
                }
                for ln in v
            ]
        else:
            out[k] = str(v) if isinstance(v, Decimal) else v
    return out


def to_plain(obj):
    """Convert Decimals in a nested result to floats (for JSON snapshots). Presentation only."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_plain(v) for v in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj
