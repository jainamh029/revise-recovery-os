from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import schemas
from ..auth import require
from ..config import today
from ..db import get_db
from ..errors import NotFound
from ..filters import DashboardFilters, DrillParams, resolve
from ..models import Alert, DeviceCohort, Partner
from ..services import alerts as alert_svc
from ..services import drilldowns as drill
from ..services.analytics import cohort_rows, executive, model_burden
from ..services.partners import scorecard
from .util import pol

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/dashboard/executive")
def dashboard(filters: Annotated[DashboardFilters, Query()], db: Session = Depends(get_db)):
    """Executive dashboard. All filters are validated server-side and combined by intersection (see app/filters.py)."""
    policy = pol(db)
    scope = resolve(db, filters, today())  # raises a 422 with a clear message for unknown ids / bad ranges
    alert_svc.evaluate(db, today(), policy)
    db.commit()
    return executive(db, today(), policy, scope)


@router.get("/partners/{partner_id}/scorecard")
def partner_scorecard(partner_id: str, db: Session = Depends(get_db)):
    p = db.get(Partner, partner_id)
    if not p:
        raise NotFound("Partner")
    return scorecard(db, p, today())


@router.get("/analytics/partner-scorecards")
def all_scorecards(db: Session = Depends(get_db)):
    return [scorecard(db, p, today()) for p in db.scalars(select(Partner).order_by(Partner.name))]


@router.get("/analytics/cohort-profitability")
def cohort_profitability(db: Session = Depends(get_db)):
    rows = [r for r in cohort_rows(db, today(), pol(db)) if r["forecast_cm"] is not None]
    return sorted(rows, key=lambda r: -(r["forecast_cm_pct"] or 0))


@router.get("/analytics/model-burden")
def burden(db: Session = Depends(get_db)):
    return model_burden(db)


@router.get("/alerts")
def list_alerts(status: str | None = None, severity: str | None = None, db: Session = Depends(get_db)):
    alert_svc.evaluate(db, today(), pol(db))
    db.commit()
    code = {c.id: c.cohort_code for c in db.scalars(select(DeviceCohort))}
    q = select(Alert).order_by(Alert.created_at.desc())
    if status:
        q = q.where(Alert.status == status)
    else:
        q = q.where(Alert.status != "resolved")
    if severity:
        q = q.where(Alert.severity == severity)
    rank = {"critical": 0, "warning": 1, "info": 2}
    return sorted(
        (alert_svc.serialize(a, code) for a in db.scalars(q)), key=lambda a: rank.get(a["severity"], 3)
    )


@router.patch("/alerts/{alert_id}", dependencies=[Depends(require("act"))])
def alert_action(alert_id: str, body: schemas.AlertActionIn, db: Session = Depends(get_db)):
    a = alert_svc.act(
        db,
        alert_id,
        body.action,
        user=body.user,
        text=body.text,
        owner=body.owner,
        snooze_days=body.snooze_days,
        as_of=today(),
    )
    db.commit()
    code = {c.id: c.cohort_code for c in db.scalars(select(DeviceCohort))}
    return alert_svc.serialize(a, code)


DrillMetric = Literal["realized_cm", "forecast_cm", "active_cohorts", "cash_exposure", "overdue_collections", "robot_utilization", "exceptions",
                      "critical_alerts", "aged_inventory"]  # fmt: skip


@router.get("/drilldown/{metric}")
def drilldown(metric: DrillMetric, params: Annotated[DrillParams, Query()], db: Session = Depends(get_db)):
    """Rows + backend-computed totals + reconciliation metadata behind one dashboard card, under the SAME filters."""
    policy = pol(db)
    scope = resolve(db, params, today())
    alert_svc.evaluate(db, today(), policy)
    db.commit()
    return drill.build(db, metric, today(), policy, scope, params)


@router.get("/drilldown/{metric}/export.csv", response_class=PlainTextResponse)
def drilldown_csv(
    metric: DrillMetric, params: Annotated[DrillParams, Query()], db: Session = Depends(get_db)
):
    policy = pol(db)
    scope = resolve(db, params, today())
    alert_svc.evaluate(db, today(), policy)
    db.commit()
    body = drill.to_csv(drill.build(db, metric, today(), policy, scope, params), params.table)
    return PlainTextResponse(
        body, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{metric}.csv"'}
    )
