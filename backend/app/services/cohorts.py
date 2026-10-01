"""Cohort lifecycle: budget versions, underwriting, approval, status transitions."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import today
from ..errors import DomainError, Invalid, NotFound
from ..models import (
    Alert,
    CohortAssignment,
    CohortBudgetVersion,
    CohortModelMix,
    DeviceCohort,
    Partner,
    PartnerContract,
    utcnow,
)
from ..policy import Policy
from . import audit, capacity
from .economics import (
    SCENARIOS,
    Assumptions,
    Line,
    assumptions_from_dict,
    assumptions_to_dict,
    compute,
    d,
    q2,
    to_plain,
)
from .underwriting import UnderwritingContext, priority_score, underwrite

D = Decimal
ACTIVE_STATES = (
    "in_intake",
    "in_processing",
    "exception_review",
    "quality_control",
    "listed_for_sale",
    "partially_sold",
)
COMMITTED_STATES = ("approved", "scheduled")

TRANSITIONS: dict[str, set[str]] = {
    "draft": {"under_review", "cancelled"},
    "under_review": {"approved", "declined", "draft", "cancelled"},
    "approved": {"scheduled", "cancelled"},
    "scheduled": {"in_intake", "in_processing", "cancelled"},
    "in_intake": {"in_processing", "exception_review"},
    "in_processing": {"exception_review", "quality_control"},
    "exception_review": {"in_processing", "quality_control"},
    "quality_control": {"listed_for_sale", "in_processing"},
    "listed_for_sale": {"partially_sold", "closed"},
    "partially_sold": {"closed", "listed_for_sale"},
    "closed": set(),
    "declined": set(),
    "cancelled": set(),
}


def get_cohort(db: Session, cohort_id: str) -> DeviceCohort:
    c = db.get(DeviceCohort, cohort_id)
    if not c:
        raise NotFound("Cohort")
    return c


def transition(db: Session, cohort: DeviceCohort, new_status: str) -> None:
    if new_status not in TRANSITIONS.get(cohort.status, set()):
        raise DomainError(
            f"Illegal status transition {cohort.status} -> {new_status}.", code="bad_transition"
        )
    if new_status in ("scheduled", "in_intake", "in_processing") and not cohort.approved_budget_version_id:
        raise DomainError(
            "Cohort needs an approved budget before it can be scheduled or processed.", code="not_approved"
        )
    if new_status in ("in_intake", "in_processing") and cohort.status == "scheduled":
        has = db.scalar(
            select(func.count())
            .select_from(CohortAssignment)
            .where(CohortAssignment.cohort_id == cohort.id, CohortAssignment.status != "cancelled")
        )
        if not has:
            raise DomainError("Cohort has no capacity assignment.", code="no_assignment")
    if new_status == "closed":
        blockers = closeout_blockers(db, cohort)
        if blockers:
            crit = any(b["code"] == "critical_alerts_open" for b in blockers)
            raise DomainError(
                "Cannot close: " + "; ".join(b["message"] for b in blockers),
                code="critical_alerts_open" if crit else "closeout_blocked",
                details={"blockers": blockers},
            )
    old = cohort.status
    cohort.status = new_status
    audit.log(db, "cohort", cohort.id, "status_change", {"status": old}, {"status": new_status})


# ---------------------------------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------------------------------
def resolve_assumptions(
    payload: dict, contract: PartnerContract | None, partner: Partner, policy: Policy
) -> Assumptions:
    """Merge user inputs with contract terms and policy defaults. User-supplied values win."""
    if not payload.get("lines"):
        raise Invalid("A cohort needs at least one device-model line.")
    ctype = payload.get("contract_type") or (contract.contract_type if contract else "supply_purchase")
    ownership = payload.get("ownership") or (
        contract.inventory_ownership_model if contract else "operator_owned"
    )

    def pick(key, contract_val=None, default=D(0)):
        if payload.get(key) is not None:
            return d(payload[key])
        return d(contract_val) if contract_val is not None else default

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
        for ln in payload["lines"]
    )
    for ln in lines:
        if ln.units <= 0:
            raise Invalid("Units per line must be positive.")
        for name in ("exception_rate", "first_pass_yield", "sell_through"):
            if not D(0) <= getattr(ln, name) <= D(1):
                raise Invalid(f"{name} must be a decimal between 0 and 1 (e.g. 0.25, not 25).")
        if ln.process_minutes <= 0 or ln.resale_price < 0 or ln.repair_cost < 0:
            raise Invalid("Process minutes must be positive; prices and costs cannot be negative.")
        if ln.condition_grade.upper() not in ("A", "B", "C", "D"):
            raise Invalid("Condition grade must be A, B, C or D.")

    terms = partner.payment_terms_days or 30
    a = Assumptions(
        contract_type=ctype,
        ownership=ownership,
        lines=lines,
        acquisition_cost_per_unit=pick(
            "acquisition_cost_per_unit", contract.acquisition_cost_per_unit if contract else None
        ),
        inbound_logistics_per_unit=pick("inbound_logistics_per_unit"),
        outbound_shipping_per_unit=pick("outbound_shipping_per_unit"),
        marketplace_fee_rate=pick("marketplace_fee_rate", default=D("0.12")),
        return_rate=pick("return_rate", default=D("0.04")),
        discount_rate=pick("discount_rate", default=D("0")),
        service_fee_per_unit=pick(
            "service_fee_per_unit", contract.service_fee_per_unit if contract else None
        ),
        revenue_share_pct=pick("revenue_share_pct", contract.revenue_share_pct if contract else None),
        performance_fee=pick("performance_fee"),
        repair_recovery_rate=pick("repair_recovery_rate", default=D("0.85")),
        robot_cost_per_hour=pick("robot_cost_per_hour", default=policy.robot_cost_per_hour),
        labor_rate_per_hour=pick("labor_rate_per_hour", default=policy.labor_rate_per_hour),
        manual_minutes_per_exception=pick(
            "manual_minutes_per_exception", default=policy.manual_minutes_per_exception
        ),
        planned_uptime=pick("planned_uptime", default=D("0.90")),
        days_to_sale=int(payload.get("days_to_sale") or policy.target_days_to_sale),
        # Lag between sale and cash: the partner's payment terms only apply when the partner is the payer (service work).
        payout_days=int(
            payload["payout_days"]
            if payload.get("payout_days") is not None
            else (terms if ctype == "processing_service" else 7)
        ),
        margin_floor_pct=pick("margin_floor_pct", default=policy.live_margin_floor_cm_pct_net_revenue),
    )
    for name in (
        "marketplace_fee_rate",
        "return_rate",
        "revenue_share_pct",
        "repair_recovery_rate",
        "planned_uptime",
        "margin_floor_pct",
    ):
        if not D(0) <= getattr(a, name) <= D(1):
            raise Invalid(f"{name} must be a decimal between 0 and 1.")
    return a


def live_context(
    db: Session,
    a: Assumptions,
    partner: Partner,
    policy: Policy,
    *,
    intake: date | None,
    target: date | None,
    exclude_cohort_id: str | None = None,
    portfolio_exposure: Decimal | None = None,
) -> UnderwritingContext:
    from .cohort_metrics import expected_exceptions_by_cohort, portfolio_cash_exposure

    base = compute(a)
    start = max(intake or today(), today()) if intake else today()
    ctx = UnderwritingContext(wipe_level=partner.data_security_requirement or "standard")
    if target:
        ctx.capacity_hours_available = capacity.free_hours(db, start, target)
        ctx.review_capacity_available = capacity.review_capacity_available(
            db, policy, start, target, exclude_cohort_id, expected_exceptions_by_cohort(db)
        )
    ctx.portfolio_cash_exposure = (
        portfolio_exposure
        if portfolio_exposure is not None
        else portfolio_cash_exposure(db, exclude_cohort_id)
    )
    ctx.partner_flags = _partner_flags(db, partner)
    _ = base
    return ctx


def _partner_flags(db: Session, partner: Partner) -> tuple:
    from .variance import reforecast

    flags = []
    for c in db.scalars(
        select(DeviceCohort).where(
            DeviceCohort.partner_id == partner.id, DeviceCohort.status.in_(ACTIVE_STATES)
        )
    ):
        fc = reforecast(db, c, today())
        if fc and (fc["below_floor"] or fc["negative"]):
            fpct = fc["economics"]["cm_pct"]
            flags.append(
                f"{partner.name}'s active cohort {c.cohort_code} is forecasting "
                f"{'n/a' if fpct is None else f'{fpct * 100:.1f}%'} CM on net revenue "
                f"(plan {(fc['plan_cm_pct'] or 0) * 100:.1f}%), below the {fc['margin_floor_pct'] * 100:.0f}% floor."
            )
    return tuple(flags)


# ---------------------------------------------------------------------------------------------------
# Budget versions
# ---------------------------------------------------------------------------------------------------
def _version_row(
    cohort_id: str, number: int, scenario: str, econ: dict, a: Assumptions, created_by: str | None
) -> CohortBudgetVersion:
    return CohortBudgetVersion(
        cohort_id=cohort_id,
        version_number=number,
        scenario_type=scenario,
        expected_units_received=int(econ["units_received"]),
        expected_units_processed=int(econ["units_processed"]),
        expected_units_sold=int(round(econ["units_sold"])),
        expected_acquisition_cost=q2(econ["acquisition_cost"] + econ["partner_revenue_share"]),
        expected_inbound_logistics_cost=q2(econ["inbound_logistics_cost"]),
        expected_robot_hours=q2(econ["robot_hours"]),
        expected_robot_cost=q2(econ["robot_cost"]),
        expected_manual_labor_cost=q2(econ["manual_labor_cost"]),
        expected_repair_cost=q2(econ["repair_cost"]),
        expected_outbound_fulfillment_cost=q2(econ["outbound_fulfillment_cost"]),
        expected_marketplace_fees=q2(econ["marketplace_fees"]),
        expected_revenue=q2(econ["revenue"]),
        expected_contribution_margin=q2(econ["contribution_margin"]),
        expected_cash_exposure=q2(econ["cash_required"]),
        expected_collection_days=econ["collection_days"],
        assumptions=assumptions_to_dict(a),
        economics=to_plain({k: v for k, v in econ.items() if k != "per_line"}),
        created_by=created_by,
    )


def add_budget_version(
    db: Session,
    cohort: DeviceCohort,
    a: Assumptions,
    created_by: str | None = "demo.user",
    scenario_set: tuple[str, ...] = ("base", "downside", "upside"),
) -> int:
    if cohort.status in ("declined", "cancelled", "closed"):
        raise DomainError(f"Cohort is {cohort.status}; budgets can no longer be added.", code="bad_status")
    n = (
        db.scalar(
            select(func.max(CohortBudgetVersion.version_number)).where(
                CohortBudgetVersion.cohort_id == cohort.id
            )
        )
        or 0
    ) + 1
    for s in scenario_set:
        db.add(_version_row(cohort.id, n, s, compute(a, SCENARIOS[s]), a, created_by))
    cohort.expected_device_count = a.units
    cohort.margin_floor_pct = a.margin_floor_pct
    db.query(CohortModelMix).filter(CohortModelMix.cohort_id == cohort.id).delete()
    for ln in a.lines:
        if ln.device_model_profile_id:
            db.add(
                CohortModelMix(
                    cohort_id=cohort.id,
                    device_model_profile_id=ln.device_model_profile_id,
                    expected_unit_count=ln.units,
                    expected_condition_grade=ln.condition_grade,
                    expected_resale_price=ln.resale_price,
                    expected_process_minutes=ln.process_minutes,
                    expected_exception_rate_pct=ln.exception_rate,
                    expected_repair_cost=ln.repair_cost,
                )
            )
    audit.log(db, "cohort", cohort.id, "budget_version_created", None, {"version": n})
    db.flush()
    return n


def versions(db: Session, cohort_id: str) -> list[CohortBudgetVersion]:
    return list(
        db.scalars(
            select(CohortBudgetVersion)
            .where(CohortBudgetVersion.cohort_id == cohort_id)
            .order_by(CohortBudgetVersion.version_number, CohortBudgetVersion.scenario_type)
        )
    )


def latest_base(db: Session, cohort_id: str) -> CohortBudgetVersion:
    v = db.scalar(
        select(CohortBudgetVersion)
        .where(CohortBudgetVersion.cohort_id == cohort_id, CohortBudgetVersion.scenario_type == "base")
        .order_by(CohortBudgetVersion.version_number.desc())
    )
    if not v:
        raise DomainError("Cohort has no budget version yet.", code="no_budget")
    return v


def assert_editable(v: CohortBudgetVersion) -> None:
    if v.is_approved:
        raise DomainError(
            "Approved budget versions are immutable. Create a new reforecast version instead.",
            code="immutable_budget",
        )


def run_underwriting(
    db: Session,
    cohort: DeviceCohort,
    policy: Policy,
    *,
    portfolio_exposure: Decimal | None = None,
    persist: bool = True,
    ctx: UnderwritingContext | None = None,
) -> dict:
    """`ctx` lets callers supply a point-in-time context (used to seed historical decisions)."""
    v = latest_base(db, cohort.id)
    a = assumptions_from_dict(v.assumptions)
    partner = db.get(Partner, cohort.partner_id)
    if ctx is None:
        ctx = live_context(
            db,
            a,
            partner,
            policy,
            intake=cohort.intake_date,
            target=cohort.target_completion_date,
            exclude_cohort_id=cohort.id,
            portfolio_exposure=portfolio_exposure,
        )
    result = underwrite(a, policy, ctx)
    result["version_number"] = v.version_number
    result["priority_score"] = float(priority_score(compute(a), policy))
    if persist and not v.is_approved:
        for row in db.scalars(
            select(CohortBudgetVersion).where(
                CohortBudgetVersion.cohort_id == cohort.id,
                CohortBudgetVersion.version_number == v.version_number,
            )
        ):
            row.recommendation = {"decision": result["decision"], "reasons": result["reasons"]}
        cohort.priority_score = D(str(result["priority_score"]))
        if cohort.status == "draft":
            transition(db, cohort, "under_review")
    return result


def approve(
    db: Session,
    cohort: DeviceCohort,
    *,
    approver: str,
    rationale: str,
    policy: Policy,
    override: bool = False,
    portfolio_exposure: Decimal | None = None,
    ctx: UnderwritingContext | None = None,
) -> CohortBudgetVersion:
    if cohort.status != "under_review":
        raise DomainError(
            f"Only cohorts under review can be approved (status: {cohort.status}).", code="bad_status"
        )
    if not rationale or not rationale.strip():
        raise Invalid("A decision rationale is required for approval.")
    rec = run_underwriting(db, cohort, policy, portfolio_exposure=portfolio_exposure, persist=False, ctx=ctx)
    if rec["decision"] == "decline" and not override:
        raise DomainError(
            "Policy recommends DECLINE. Approve only with an explicit override and rationale.",
            code="policy_decline",
        )
    v = latest_base(db, cohort.id)
    if v.is_approved:
        raise DomainError("This budget version is already approved.", code="immutable_budget")
    now = utcnow()
    for row in db.scalars(
        select(CohortBudgetVersion).where(
            CohortBudgetVersion.cohort_id == cohort.id, CohortBudgetVersion.version_number == v.version_number
        )
    ):
        row.is_approved = True
        row.approved_by, row.approved_at, row.decision_rationale = approver, now, rationale
        row.recommendation = {"decision": rec["decision"], "reasons": rec["reasons"], "override": override}
    cohort.approved_budget_version_id = v.id
    cohort.decision_rationale = rationale
    transition(db, cohort, "approved")
    audit.log(
        db,
        "cohort",
        cohort.id,
        "approved",
        None,
        {
            "version": v.version_number,
            "approver": approver,
            "rationale": rationale,
            "recommendation": rec["decision"],
            "override": override,
        },
        user=approver,
    )
    return v


def decline(
    db: Session,
    cohort: DeviceCohort,
    *,
    approver: str,
    rationale: str,
    policy: Policy,
    portfolio_exposure: Decimal | None = None,
    ctx: UnderwritingContext | None = None,
) -> None:
    if cohort.status not in ("under_review", "draft"):
        raise DomainError(f"Cannot decline a cohort in status {cohort.status}.", code="bad_status")
    if not rationale or not rationale.strip():
        raise Invalid("A decision rationale is required.")
    rec = (
        run_underwriting(db, cohort, policy, portfolio_exposure=portfolio_exposure, persist=False, ctx=ctx)
        if cohort.status == "under_review"
        else None
    )
    if cohort.status == "draft":
        transition(db, cohort, "under_review")
    for row in db.scalars(
        select(CohortBudgetVersion).where(
            CohortBudgetVersion.cohort_id == cohort.id,
            CohortBudgetVersion.version_number == latest_base(db, cohort.id).version_number,
        )
    ):
        row.decision_rationale = rationale
        row.recommendation = {
            "decision": rec["decision"] if rec else "n/a",
            "reasons": rec["reasons"] if rec else [],
            "declined": True,
        }
    cohort.decision_rationale = rationale
    transition(db, cohort, "declined")
    audit.log(
        db,
        "cohort",
        cohort.id,
        "declined",
        None,
        {"approver": approver, "rationale": rationale},
        user=approver,
    )


def create_reforecast_version(
    db: Session, cohort: DeviceCohort, reforecast_result: dict
) -> CohortBudgetVersion:
    """Persist a reforecast as a NEW version; the approved plan is never touched."""
    base = latest_base(db, cohort.id)
    n = (
        db.scalar(
            select(func.max(CohortBudgetVersion.version_number)).where(
                CohortBudgetVersion.cohort_id == cohort.id
            )
        )
        or 0
    ) + 1
    e = reforecast_result["economics"]
    row = CohortBudgetVersion(
        cohort_id=cohort.id,
        version_number=n,
        scenario_type="reforecast",
        is_approved=False,
        expected_units_received=base.expected_units_received,
        expected_units_processed=base.expected_units_processed,
        expected_units_sold=int(round(e["units_sold"])),
        expected_acquisition_cost=q2(d(e["acquisition_cost"]) + d(e["partner_revenue_share"])),
        expected_inbound_logistics_cost=q2(d(e["inbound_logistics_cost"])),
        expected_robot_hours=q2(d(e["robot_hours"])),
        expected_robot_cost=q2(d(e["robot_cost"])),
        expected_manual_labor_cost=q2(d(e["manual_labor_cost"])),
        expected_repair_cost=q2(d(e["repair_cost"])),
        expected_outbound_fulfillment_cost=q2(d(e["outbound_fulfillment_cost"])),
        expected_marketplace_fees=q2(d(e["marketplace_fees"])),
        expected_revenue=q2(d(e["revenue"])),
        expected_contribution_margin=q2(d(e["contribution_margin"])),
        expected_cash_exposure=q2(d(e["cash_required"])),
        expected_collection_days=e["collection_days"],
        assumptions=base.assumptions,
        economics=e,
        created_by="system.reforecast",
    )
    db.add(row)
    audit.log(
        db,
        "cohort",
        cohort.id,
        "reforecast_created",
        None,
        {"version": n, "cm": float(row.expected_contribution_margin)},
    )
    db.flush()
    return row


def closeout_blockers(db: Session, cohort: DeviceCohort) -> list[dict]:
    """Everything that must be true before a cohort can be closed. Empty list == ready."""
    from ..models import Device
    from .devices import TERMINAL
    from .finance import cohort_actuals, collections_aging

    out: list[dict] = []
    crit = db.scalar(select(func.count()).select_from(Alert).where(
        Alert.cohort_id == cohort.id, Alert.severity == "critical", Alert.status.in_(("open", "escalated", "snoozed"))))  # fmt: skip
    if crit:
        out.append({"code": "critical_alerts_open", "message": f"{crit} unresolved critical alert(s)"})
    owed = [i for i in collections_aging(db, today())["items"] if i["cohort_id"] == cohort.id]
    if owed:
        total = sum((i["remaining"] for i in owed), d(0))
        out.append(
            {
                "code": "cash_outstanding",
                "message": f"{len(owed)} payout/invoice item(s) not yet collected (${float(total):,.2f} outstanding)",
            }
        )
    devs = list(db.scalars(select(Device).where(Device.cohort_id == cohort.id)))
    if devs:
        open_devs = [x for x in devs if x.current_status not in TERMINAL]
        if open_devs:
            out.append(
                {
                    "code": "devices_not_disposed",
                    "message": f"{len(open_devs)} device(s) are not sold, recycled or destroyed",
                }
            )
    else:
        inv = cohort_actuals(db, cohort.id, today())["inventory_units"]
        if inv > 0:
            out.append(
                {
                    "code": "unsold_inventory",
                    "message": f"{inv} device(s) remain unsold per operations counts",
                }
            )
    return out
