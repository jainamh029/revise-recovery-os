"""Cohort acceptance decision engine (PRD section 7). Pure function over economics + context."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..policy import Policy
from .economics import (
    Assumptions,
    compute_scenarios,
    max_acquisition_for_margin,
    quality_from_mix,
    to_plain,
)

D = Decimal


@dataclass
class UnderwritingContext:
    wipe_level: str = "standard"
    capacity_hours_available: Decimal | None = None  # scheduled hours free before SLA; None = not checked
    review_capacity_available: Decimal | None = None  # manual exception reviews free in window
    portfolio_cash_exposure: Decimal = D(0)  # exposure already committed by other active cohorts
    partner_flags: tuple = ()  # track-record warnings from this partner's live cohorts


def _check(key, label, status, detail, value=None, threshold=None):
    return {
        "key": key,
        "label": label,
        "status": status,
        "detail": detail,
        "value": value,
        "threshold": threshold,
    }


def pct(x) -> str:
    return "n/a" if x is None else f"{float(x) * 100:.1f}%"


def money(x) -> str:
    return f"${float(x):,.0f}"


def underwrite(a: Assumptions, policy: Policy, ctx: UnderwritingContext) -> dict:
    scen = compute_scenarios(a)
    base, down = scen["base"], scen["downside"]
    quality = quality_from_mix(a.lines)
    checks: list[dict] = []
    declines: list[str] = []
    reviews: list[str] = []
    conditions: list[str] = []

    # --- hard stops -------------------------------------------------------------------------------
    wipe_ok = ctx.wipe_level in policy.supported_wipe_levels
    checks.append(
        _check(
            "wipe",
            "Secure data-wipe requirement supported",
            "pass" if wipe_ok else "fail",
            f"Partner requires '{ctx.wipe_level}' wipe; supported: {', '.join(policy.supported_wipe_levels)}.",
        )
    )
    if not wipe_ok:
        declines.append("Secure-data-wipe requirement is unsupported -- quarantine / decline.")

    neg = base["contribution_margin"] < policy.critical_negative_margin_threshold
    bp = base["cm_pct"]  # CM / net recognised revenue; None when net revenue <= 0
    too_low = bp is None or bp < policy.base_case_review_cm_pct_net_revenue
    checks.append(
        _check(
            "base_cm_positive",
            "Base-case contribution margin dollars are positive",
            "fail" if neg else "pass",
            f"Base CM {money(base['contribution_margin'])} on {money(base['net_recognized_revenue'])} net recognized revenue.",
            float(base["contribution_margin"]),
            0,
        )
    )
    checks.append(
        _check(
            "base_cm_pct",
            "Base CM% (of net recognized revenue) meets policy",
            "fail" if too_low else "pass" if bp >= policy.base_case_accept_cm_pct_net_revenue else "warn",
            f"Base CM {pct(bp)} vs accept at {pct(policy.base_case_accept_cm_pct_net_revenue)}; decline below "
            f"{pct(policy.base_case_review_cm_pct_net_revenue)}.",
            None if bp is None else float(bp),
            float(policy.base_case_accept_cm_pct_net_revenue),
        )
    )
    if neg:
        declines.append("Base-case contribution margin dollars are negative.")
    elif too_low:
        declines.append(
            f"Base-case CM {pct(bp)} of net recognized revenue is below the "
            f"{pct(policy.base_case_review_cm_pct_net_revenue)} decline line."
        )
    elif bp < policy.base_case_accept_cm_pct_net_revenue:
        target = max_acquisition_for_margin(a, policy.base_case_accept_cm_pct_net_revenue)
        hint = (
            f" Reprice acquisition to <= {money(target)}/unit (currently {money(a.acquisition_cost_per_unit)}) to reach the accept line."
            if target is not None
            else ""
        )
        reviews.append(
            f"Base-case CM {pct(bp)} is between the {pct(policy.base_case_review_cm_pct_net_revenue)} decline line and the "
            f"{pct(policy.base_case_accept_cm_pct_net_revenue)} accept line.{hint}"
        )

    q_ok = quality >= policy.min_quality_index
    checks.append(
        _check(
            "quality",
            "Expected device quality above acceptance threshold",
            "pass" if q_ok else "fail",
            f"Condition-weighted quality index {float(quality):.2f} vs minimum {float(policy.min_quality_index):.2f}.",
            float(quality),
            float(policy.min_quality_index),
        )
    )
    if not q_ok:
        declines.append(
            "Expected device quality is below the minimum acceptance threshold -- decline or renegotiate."
        )

    exc_high = base["exception_rate"] > policy.max_exception_rate

    # --- review triggers --------------------------------------------------------------------------
    dp = down["cm_pct"]
    down_ok = dp is not None and dp >= policy.downside_case_min_cm_pct_net_revenue
    checks.append(
        _check(
            "downside_cm",
            "Downside CM% (of net recognized revenue) above risk floor",
            "pass" if down_ok else "fail",
            f"Downside CM {pct(dp)} vs floor {pct(policy.downside_case_min_cm_pct_net_revenue)}.",
            None if dp is None else float(dp),
            float(policy.downside_case_min_cm_pct_net_revenue),
        )
    )
    if not down_ok:
        reviews.append(
            f"Downside-case CM {pct(dp)} of net recognized revenue is below the "
            f"{pct(policy.downside_case_min_cm_pct_net_revenue)} risk floor."
        )

    if ctx.capacity_hours_available is None:
        checks.append(
            _check(
                "robot_capacity", "Robot capacity before SLA", "info", "Not checked (no intake/SLA dates)."
            )
        )
    else:
        need = base["scheduled_robot_hours"]
        ok = need <= ctx.capacity_hours_available
        checks.append(
            _check(
                "robot_capacity",
                "Robot capacity before SLA",
                "pass" if ok else "fail",
                f"Needs {float(need):.0f} scheduled robot-hours; {float(ctx.capacity_hours_available):.0f} free before SLA.",
                float(need),
                float(ctx.capacity_hours_available),
            )
        )
        if not ok:
            reviews.append("Required robot hours exceed available capacity before the SLA date.")

    if ctx.review_capacity_available is None:
        checks.append(
            _check(
                "review_capacity", "Manual-review capacity for expected exceptions", "info", "Not checked."
            )
        )
    else:
        need = base["exception_units"]
        ok = need <= ctx.review_capacity_available
        checks.append(
            _check(
                "review_capacity",
                "Manual-review capacity for expected exceptions",
                "pass" if ok else "fail",
                f"Expects {float(need):.0f} manual exceptions; {float(ctx.review_capacity_available):.0f} review slots free.",
                float(need),
                float(ctx.review_capacity_available),
            )
        )
        if not ok:
            reviews.append("Expected manual exceptions exceed available review capacity.")

    exposure_after = ctx.portfolio_cash_exposure + base["cash_required"]
    wc_ok = exposure_after <= policy.working_capital_limit
    checks.append(
        _check(
            "working_capital",
            "Cash exposure within working-capital limit",
            "pass" if wc_ok else "fail",
            f"{money(ctx.portfolio_cash_exposure)} committed + {money(base['cash_required'])} this cohort = "
            f"{money(exposure_after)} vs limit {money(policy.working_capital_limit)}.",
            float(exposure_after),
            float(policy.working_capital_limit),
        )
    )
    if not wc_ok:
        reviews.append("Combined cash exposure exceeds the working-capital limit.")

    # --- operational / non-financial conditions (Accept with conditions) --------------------------
    checks.append(
        _check(
            "exception_rate",
            "Expected exception rate within policy",
            "pass" if not exc_high else "warn",
            f"Expected {pct(base['exception_rate'])} vs maximum {pct(policy.max_exception_rate)}.",
            float(base["exception_rate"]),
            float(policy.max_exception_rate),
        )
    )
    if exc_high:
        conditions.append(
            f"Expected exception rate {pct(base['exception_rate'])} exceeds {pct(policy.max_exception_rate)}: add an exception-rate clawback / inspection gate."
        )

    fpy_ok = base["first_pass_yield"] >= policy.min_first_pass_yield
    checks.append(
        _check(
            "fpy",
            "First-pass yield above minimum",
            "pass" if fpy_ok else "warn",
            f"Expected FPY {pct(base['first_pass_yield'])} vs minimum {pct(policy.min_first_pass_yield)}.",
            float(base["first_pass_yield"]),
            float(policy.min_first_pass_yield),
        )
    )
    if not fpy_ok:
        conditions.append(
            f"First-pass yield {pct(base['first_pass_yield'])} is below {pct(policy.min_first_pass_yield)}: route to a supervised cell."
        )

    st_ok = base["sell_through"] >= policy.min_sell_through
    checks.append(
        _check(
            "sell_through",
            "Sell-through meets target",
            "pass" if st_ok else "warn",
            f"Expected {pct(base['sell_through'])} vs target {pct(policy.min_sell_through)}.",
            float(base["sell_through"]),
            float(policy.min_sell_through),
        )
    )
    if not st_ok:
        conditions.append(
            f"Sell-through {pct(base['sell_through'])} is below {pct(policy.min_sell_through)}: pre-arrange a bulk channel."
        )

    cd_ok = base["collection_days"] <= policy.target_days_to_sale + policy.target_collection_days
    checks.append(
        _check(
            "collection_days",
            "Cash conversion within target",
            "pass" if cd_ok else "warn",
            f"Expected {base['collection_days']} days to cash vs target {policy.target_days_to_sale + policy.target_collection_days}.",
            base["collection_days"],
            policy.target_days_to_sale + policy.target_collection_days,
        )
    )
    if not cd_ok:
        conditions.append(
            "Cash conversion is slower than target: shorten payment terms or require a deposit."
        )

    if ctx.partner_flags:
        checks.append(_check("partner_history", "Partner track record", "warn", " ".join(ctx.partner_flags)))
        conditions.extend(
            f"{f} Require an exception-rate clawback and a pre-shipment inspection sample."
            for f in ctx.partner_flags
        )
    else:
        checks.append(
            _check(
                "partner_history",
                "Partner track record",
                "pass",
                "No live cohorts from this partner are breaching plan.",
            )
        )

    if declines:
        decision, reasons = "decline", declines
    elif reviews:
        decision, reasons = "review", reviews
    elif conditions:
        decision, reasons = "accept_with_conditions", conditions
    else:
        decision, reasons = "accept", ["All financial, capacity, and quality criteria pass."]

    return {
        "decision": decision,
        "reasons": reasons,
        "conditions": conditions,
        "checks": checks,
        "scenarios": to_plain(
            {k: {kk: vv for kk, vv in v.items() if kk != "per_line"} for k, v in scen.items()}
        ),
        "per_line": to_plain(base["per_line"]),
        "quality_index": float(quality),
        "portfolio_cash_exposure": float(ctx.portfolio_cash_exposure),
        "cash_exposure_after": float(exposure_after),
    }


DECISION_LABEL = {
    "accept": "Accept",
    "accept_with_conditions": "Accept with conditions",
    "review": "Review required",
    "decline": "Decline",
}


def priority_score(econ: dict, policy: Policy) -> Decimal:
    """0-100 score used to rank cohorts competing for scarce robot hours (CM/robot-hour dominant)."""
    cm_hr = min(max(econ["cm_per_robot_hour"], D(0)) / D(500), D(1))
    cm_pct = min(max(econ["cm_pct"] or D(0), D(0)) / D("0.5"), D(1))
    low_exc = max(D(1) - econ["exception_rate"] / D("0.4"), D(0))
    cash_eff = (
        min(max(econ["contribution_margin"], D(0)) / econ["cash_required"] / D(2), D(1))
        if econ["cash_required"] > 0
        else D(0)
    )
    score = (
        policy.w_cm_per_hour * cm_hr
        + policy.w_cm_pct * cm_pct
        + policy.w_low_exception * low_exc
        + policy.w_cash_efficiency * cash_eff
    ) * 100
    return score.quantize(D("0.0001"))
