from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import PERMS, Forbidden, current_role, require
from ..config import today
from ..db import get_db
from ..errors import DomainError, Invalid, NotFound
from ..models import (
    Alert,
    AuditLog,
    CohortAssignment,
    CohortBudgetVersion,
    CohortModelMix,
    DeviceCohort,
    ExceptionEvent,
    Partner,
    PartnerContract,
)
from ..services import capacity
from ..services import cohorts as svc
from ..services import memo as memo_svc
from ..services import scenario as scen_svc
from ..services.cohort_metrics import expected_exceptions_by_cohort
from ..services.economics import assumptions_from_dict, compute, d, to_plain
from ..services.finance import cash_conversion, cohort_actuals
from ..services.operations import daily_series, plan_to_date
from ..services.underwriting import priority_score, underwrite
from ..services.variance import reforecast, variance
from .util import cohort_or_404, pol, row

router = APIRouter(prefix="/api", tags=["cohorts"])


class StatusIn(BaseModel):
    status: str


def _assumptions_from(body, db: Session, policy):
    partner = db.get(Partner, body.partner_id)
    if not partner:
        raise NotFound("Partner")
    contract = db.get(PartnerContract, body.contract_id) if body.contract_id else None
    if body.contract_id and not contract:
        raise NotFound("Contract")
    a = svc.resolve_assumptions(body.assumptions.model_dump(), contract, partner, policy)
    return partner, contract, a


def _code(db: Session, partner_type: str) -> str:
    prefix = {"university": "EDU", "nonprofit": "EDU", "itad_provider": "ITAD", "recycler": "REC"}.get(
        partner_type, "ENT"
    )
    n = len(list(db.scalars(select(DeviceCohort.id)))) + 1
    return f"{prefix}-{n:03d}"


@router.post("/underwrite/preview", dependencies=[Depends(require("draft"))])
def preview(body: schemas.PreviewIn, db: Session = Depends(get_db)):
    policy = pol(db)
    partner, contract, a = _assumptions_from(body, db, policy)
    ctx = svc.live_context(
        db, a, partner, policy, intake=body.intake_date, target=body.target_completion_date
    )
    result = underwrite(a, policy, ctx)
    result["priority_score"] = float(priority_score(compute(a), policy))
    return result


@router.post("/cohorts", status_code=201, dependencies=[Depends(require("draft"))])
def create_cohort(body: schemas.CohortIn, db: Session = Depends(get_db)):
    policy = pol(db)
    partner, contract, a = _assumptions_from(body, db, policy)
    code = body.cohort_code or _code(db, partner.partner_type)
    if db.scalar(select(DeviceCohort).where(DeviceCohort.cohort_code == code)):
        raise DomainError(f"Cohort code {code} already exists.", code="duplicate")
    c = DeviceCohort(
        partner_id=partner.id,
        contract_id=body.contract_id,
        cohort_code=code,
        cohort_name=body.cohort_name,
        intake_date=body.intake_date,
        target_completion_date=body.target_completion_date,
        target_sale_date=body.target_sale_date
        or (
            body.target_completion_date + timedelta(days=policy.target_days_to_sale)
            if body.target_completion_date
            else None
        ),
        expected_device_count=a.units,
        ownership_model=a.ownership,
        status="draft",
    )
    db.add(c)
    db.flush()
    svc.add_budget_version(db, c, a)
    svc.audit.log(db, "cohort", c.id, "created", None, {"code": code})
    db.commit()
    return row(c)


@router.get("/cohorts")
def list_cohorts(status: str | None = None, db: Session = Depends(get_db)):
    from ..services.analytics import cohort_rows

    rows = cohort_rows(db, today(), pol(db))
    if status:
        rows = [r for r in rows if r["status"] == status]
    for r in rows:
        v = None
        c = db.get(DeviceCohort, r["id"])
        ver = (
            svc.latest_base(db, c.id)
            if db.scalar(select(CohortBudgetVersion.id).where(CohortBudgetVersion.cohort_id == c.id))
            else None
        )
        rec = ver.recommendation if ver else None
        r["recommendation"] = rec.get("decision") if rec else None
        r["base_cm_pct"] = (
            d(ver.expected_contribution_margin) / d(ver.expected_revenue)
            if ver and d(ver.expected_revenue)
            else None
        )
        r["base_cm"] = ver.expected_contribution_margin if ver else None
        r["cash_required"] = ver.expected_cash_exposure if ver else None
        r["cm_per_robot_hour_plan"] = (ver.economics or {}).get("cm_per_robot_hour") if ver else None
        r["target_completion_date"] = c.target_completion_date
        r["intake_date"] = c.intake_date
        _ = v
    return rows


@router.get("/cohorts/{cohort_id}")
def get_cohort(cohort_id: str, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    vers = svc.versions(db, c.id)
    grouped: dict[int, dict] = {}
    for v in vers:
        g = grouped.setdefault(
            v.version_number,
            {
                "version_number": v.version_number,
                "is_approved": v.is_approved,
                "scenarios": {},
                "approved_by": v.approved_by,
                "approved_at": v.approved_at,
                "decision_rationale": v.decision_rationale,
                "recommendation": None,
                "created_at": v.created_at,
            },
        )
        g["scenarios"][v.scenario_type] = row(v, exclude=("assumptions",))
        if v.scenario_type in ("base", "reforecast") and v.recommendation:
            g["recommendation"] = v.recommendation
    partner, contract = (
        db.get(Partner, c.partner_id),
        (db.get(PartnerContract, c.contract_id) if c.contract_id else None),
    )
    assignments = [
        {**row(a, exclude=("daily_plan",)), "cell_name": a.robot_cell.cell_name}
        for a in db.scalars(
            select(CohortAssignment).where(
                CohortAssignment.cohort_id == c.id, CohortAssignment.status != "cancelled"
            )
        )
    ]
    mix = [
        {**row(m), "model": f"{m.profile.manufacturer} {m.profile.model_name}"}
        for m in db.scalars(select(CohortModelMix).where(CohortModelMix.cohort_id == c.id))
    ]
    base = svc.latest_base(db, c.id) if vers else None
    return {
        **row(c),
        "partner": row(partner),
        "contract": row(contract) if contract else None,
        "versions": list(grouped.values()),
        "assignments": assignments,
        "model_mix": mix,
        "assumptions": base.assumptions if base else None,
        "approved_version_id": c.approved_budget_version_id,
    }


@router.patch("/cohorts/{cohort_id}", dependencies=[Depends(require("draft"))])
def patch_cohort(cohort_id: str, body: schemas.CohortPatch, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    if c.approved_budget_version_id and body.model_dump(exclude_unset=True).keys() & {
        "intake_date",
        "target_completion_date",
    }:
        raise DomainError(
            "Dates of an approved cohort are part of the approved plan; create a reforecast instead.",
            code="immutable_budget",
        )
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(c, k, v)
    db.commit()
    return row(c)


@router.get("/cohorts/{cohort_id}/budget-versions")
def list_versions(cohort_id: str, db: Session = Depends(get_db)):
    cohort_or_404(db, cohort_id)
    return [row(v) for v in svc.versions(db, cohort_id)]


@router.post(
    "/cohorts/{cohort_id}/budget-versions", status_code=201, dependencies=[Depends(require("draft"))]
)
def add_version(cohort_id: str, body: schemas.AssumptionsIn, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    policy = pol(db)
    partner = db.get(Partner, c.partner_id)
    contract = db.get(PartnerContract, c.contract_id) if c.contract_id else None
    a = svc.resolve_assumptions(body.model_dump(), contract, partner, policy)
    if c.approved_budget_version_id:
        # Material change after approval => reforecast version; approved plan stays untouched.
        n = (max(v.version_number for v in svc.versions(db, c.id))) + 1
        r = svc._version_row(c.id, n, "reforecast", compute(a), a, "demo.user")
        db.add(r)
        svc.audit.log(db, "cohort", c.id, "reforecast_created", None, {"version": n})
        db.commit()
        return {"version_number": n, "scenario_type": "reforecast"}
    n = svc.add_budget_version(db, c, a)
    db.commit()
    return {"version_number": n, "scenario_type": "base"}


@router.post("/cohorts/{cohort_id}/underwrite", dependencies=[Depends(require("draft"))])
def do_underwrite(cohort_id: str, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    result = svc.run_underwriting(db, c, pol(db), persist=True)
    db.commit()
    return {**result, "status": c.status}


@router.post("/cohorts/{cohort_id}/approve", dependencies=[Depends(require("approve"))])
def approve(cohort_id: str, body: schemas.DecisionIn, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    v = svc.approve(
        db, c, approver=body.approver, rationale=body.rationale, policy=pol(db), override=body.override
    )
    db.commit()
    return {
        "status": c.status,
        "approved_version": v.version_number,
        "approved_budget_version_id": c.approved_budget_version_id,
    }


@router.post("/cohorts/{cohort_id}/decline", dependencies=[Depends(require("approve"))])
def decline(cohort_id: str, body: schemas.DecisionIn, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    svc.decline(db, c, approver=body.approver, rationale=body.rationale, policy=pol(db))
    db.commit()
    return {"status": c.status}


@router.post("/cohorts/{cohort_id}/status")
def set_status(
    cohort_id: str, body: StatusIn, db: Session = Depends(get_db), role: str = Depends(current_role)
):
    needed = "close" if body.status == "closed" else "ops_write"
    if role not in PERMS[needed]:
        raise Forbidden(
            f"Role '{role}' is not permitted to set status '{body.status}' ({needed}).", code="forbidden"
        )
    c = cohort_or_404(db, cohort_id)
    svc.transition(db, c, body.status)
    db.commit()
    return {"status": c.status}


@router.get("/cohorts/{cohort_id}/closeout-check")
def closeout_check(cohort_id: str, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    blockers = svc.closeout_blockers(db, c)
    return {
        "ready": not blockers and c.status in ("listed_for_sale", "partially_sold"),
        "status": c.status,
        "blockers": blockers,
    }


@router.post("/cohorts/{cohort_id}/scenario")
def scenario(cohort_id: str, body: schemas.ScenarioIn, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    a = assumptions_from_dict(svc.latest_base(db, c.id).assumptions)
    return scen_svc.run(a, body.model_dump(exclude_none=True))


@router.post("/cohorts/{cohort_id}/reforecast", status_code=201, dependencies=[Depends(require("draft"))])
def make_reforecast(cohort_id: str, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    fc = reforecast(db, c, today())
    if not fc:
        raise DomainError("No approved budget to reforecast from.", code="no_budget")
    v = svc.create_reforecast_version(db, c, fc)
    db.commit()
    return {
        "version_number": v.version_number,
        "expected_contribution_margin": v.expected_contribution_margin,
    }


@router.post(
    "/cohorts/{cohort_id}/assignments", status_code=201, dependencies=[Depends(require("ops_write"))]
)
def assign(cohort_id: str, body: schemas.AssignmentIn, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    cell = capacity.cell_or_404(db, body.robot_cell_id)
    v = db.get(CohortBudgetVersion, c.approved_budget_version_id) if c.approved_budget_version_id else None
    if not v:
        raise DomainError("Cohort must be approved before capacity can be assigned.", code="not_approved")
    total_hours = d((v.economics or {}).get("scheduled_robot_hours", v.expected_robot_hours))
    already = sum(
        (
            d(x.planned_hours)
            for x in db.scalars(
                select(CohortAssignment).where(
                    CohortAssignment.cohort_id == c.id, CohortAssignment.status != "cancelled"
                )
            )
        ),
        d(0),
    )
    # Default to whatever is still unplaced, so a cohort can be split across cells in several calls.
    hours = body.planned_hours or max(total_hours - already, d(0))
    if hours < d("0.05"):
        raise DomainError("All of this cohort's robot hours are already assigned.", code="fully_assigned")
    units_placed = sum(
        x.planned_units
        for x in db.scalars(
            select(CohortAssignment).where(
                CohortAssignment.cohort_id == c.id, CohortAssignment.status != "cancelled"
            )
        )
    )
    remaining_units = max(v.expected_units_processed - units_placed, 0)
    if body.planned_units:
        units = body.planned_units
    elif hours >= total_hours - already - d("0.05"):
        units = remaining_units  # last placement takes the integer remainder so units reconcile exactly
    else:
        units = max(
            min(int(round(v.expected_units_processed * float(hours / total_hours))), remaining_units), 1
        )
    if units > remaining_units:
        raise DomainError(
            f"Only {remaining_units} unit(s) of this cohort remain to be placed.", code="units_exceeded"
        )
    a = capacity.schedule_assignment(db, c, cell, body.start_date, body.end_date, hours, units)
    db.commit()
    return {**row(a), "cell_name": cell.cell_name}


@router.delete("/assignments/{assignment_id}", dependencies=[Depends(require("ops_write"))])
def delete_assignment(assignment_id: str, db: Session = Depends(get_db)):
    a = db.get(CohortAssignment, assignment_id)
    if not a:
        raise NotFound("Assignment")
    capacity.cancel_assignment(db, a)
    db.commit()
    return {"status": "cancelled"}


@router.get("/cohorts/{cohort_id}/actuals")
def actuals(cohort_id: str, db: Session = Depends(get_db)):
    cohort_or_404(db, cohort_id)
    return cohort_actuals(db, cohort_id, today())


@router.get("/cohorts/{cohort_id}/variance")
def get_variance(cohort_id: str, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    return variance(db, c, today(), pol(db))


@router.get("/cohorts/{cohort_id}/performance")
def performance(cohort_id: str, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    as_of, policy = today(), pol(db)
    var = variance(db, c, as_of, policy)
    p2d = plan_to_date(db, c.id, as_of)
    series = daily_series(db, c.id, as_of)
    evs = list(db.scalars(select(ExceptionEvent).where(ExceptionEvent.cohort_id == c.id)))
    by_type: dict[str, int] = {}
    for e in evs:
        by_type[e.exception_type] = by_type.get(e.exception_type, 0) + 1
    alerts = [
        {"id": a.id, "severity": a.severity, "title": a.title, "status": a.status, "alert_type": a.alert_type}
        for a in db.scalars(
            select(Alert).where(Alert.cohort_id == c.id, Alert.status.in_(("open", "escalated", "snoozed")))
        )
    ]
    return {
        "variance": var,
        "actuals": cohort_actuals(db, c.id, as_of),
        "plan_to_date": p2d,
        "series": series,
        "exceptions_by_type": [
            {"type": k, "count": v} for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])
        ],
        "cash_conversion": cash_conversion(db, c.id, as_of),
        "alerts": alerts,
    }


@router.get("/cohorts/{cohort_id}/underwriting")
def live_underwriting(cohort_id: str, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    return svc.run_underwriting(db, c, pol(db), persist=False)


@router.get("/cohorts/{cohort_id}/audit")
def audit_trail(cohort_id: str, db: Session = Depends(get_db)):
    cohort_or_404(db, cohort_id)
    return [
        row(a)
        for a in db.scalars(
            select(AuditLog).where(AuditLog.entity_id == cohort_id).order_by(AuditLog.created_at)
        )
    ]


@router.get("/cohorts/{cohort_id}/memo", response_class=PlainTextResponse)
def memo(cohort_id: str, db: Session = Depends(get_db)):
    c = cohort_or_404(db, cohort_id)
    policy = pol(db)
    uw = svc.run_underwriting(db, c, policy, persist=False)
    base = svc.latest_base(db, c.id)
    approved = None
    if c.approved_budget_version_id:
        b = db.get(CohortBudgetVersion, c.approved_budget_version_id)
        approved = {
            "approved_by": b.approved_by,
            "approved_at": b.approved_at,
            "version": b.version_number,
            "rationale": b.decision_rationale,
        }
        # Show the recommendation as it stood when approved: re-run is informational only.
    _ = base
    return memo_svc.build(
        c,
        db.get(Partner, c.partner_id),
        db.get(PartnerContract, c.contract_id) if c.contract_id else None,
        uw,
        policy,
        approved,
    )


_ = (date, Decimal, Invalid, to_plain, expected_exceptions_by_cohort)
