"""Alert rules engine (PRD section 8 / TRD section 8). Deterministic, idempotent, self-resolving."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import DomainError, NotFound
from ..models import Alert, Device, DeviceCohort, ExceptionEvent, Invoice, RobotCell, SalesTransaction, utcnow
from ..policy import Policy
from . import audit
from .capacity import calendar_rows
from .cohort_metrics import ACTIVE, portfolio_cash_exposure
from .economics import d, safe_div
from .finance import collections_aging, inventory_aging
from .operations import cell_utilization, daily_series, plan_to_date
from .variance import reforecast, variance

D = Decimal
AUTO_OPEN = ("open", "escalated", "snoozed")


def _c(type_, sev, etype, eid, cohort_id, title, desc, action):
    return dict(
        alert_type=type_,
        severity=sev,
        entity_type=etype,
        entity_id=eid,
        cohort_id=cohort_id,
        title=title,
        description=desc,
        recommended_action=action,
    )


def _fp(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _pct(x) -> str:
    return f"{float(x) * 100:.0f}%"


def candidates(db: Session, as_of: date, policy: Policy) -> list[dict]:
    out: list[dict] = []

    for cell in db.scalars(select(RobotCell)):
        days = cell_utilization(db, cell.id, as_of - timedelta(days=21), as_of)["days"][-5:]
        if len(days) == 5 and all(x["utilization"] < policy.utilization_warning for x in days):
            avg = sum(x["utilization"] for x in days) / 5
            reasons = sorted({r for x in days for r in x["reasons"]})
            out.append(
                _c(
                    "LOW_UTILIZATION",
                    "warning",
                    "robot_cell",
                    cell.id,
                    None,
                    f"{cell.cell_name}: utilization {_pct(avg)} for 5 operating days",
                    f"Productive utilization below {_pct(policy.utilization_warning)} for five consecutive operating days."
                    + (f" Downtime reasons: {', '.join(reasons)}." if reasons else ""),
                    "Review downtime causes, schedule maintenance, or shift cohorts to a healthier cell.",
                )
            )

    for c in db.scalars(select(DeviceCohort).where(DeviceCohort.status.in_(ACTIVE))):
        code = c.cohort_code
        series = daily_series(db, c.id, as_of)
        started = series[-1]["cum_started"] if series else 0
        p2d = plan_to_date(db, c.id, as_of)
        if p2d["units"] > 0 and started > 0 and started < p2d["units"] * (1 - policy.throughput_miss_warning):
            out.append(
                _c(
                    "THROUGHPUT_BELOW_PLAN",
                    "warning",
                    "cohort",
                    c.id,
                    c.id,
                    f"{code}: throughput {_pct(started / float(p2d['units']))} of plan",
                    f"{started} devices started vs {float(p2d['units']):.0f} planned to date (>{_pct(policy.throughput_miss_warning)} miss).",
                    "Check cell uptime and intake flow; add hours or re-sequence cohorts.",
                )
            )
        fc = reforecast(db, c, as_of)
        if fc:
            drv = fc["drivers"]
            if (
                started >= 30
                and drv["observed_exception_rate"] is not None
                and drv["plan_exception_rate"] > 0
                and D(str(drv["observed_exception_rate"]))
                > D(str(drv["plan_exception_rate"])) * policy.exception_alert_multiple
            ):
                out.append(
                    _c(
                        "EXCEPTION_RATE_ABOVE_PLAN",
                        "warning",
                        "cohort",
                        c.id,
                        c.id,
                        f"{code}: exceptions tracking {_pct(drv['observed_exception_rate'])} vs {_pct(drv['plan_exception_rate'])} plan",
                        f"Observed exception rate exceeds plan by more than {int((policy.exception_alert_multiple - 1) * 100)}%. "
                        f"Reforecast CM is {_fp(fc['economics']['cm_pct'])} of net recognized revenue vs {_fp(fc['plan_cm_pct'])} planned.",
                        "Inspect the top exception types, quarantine the worst models, and invoke the partner quality clause.",
                    )
                )
            if fc["negative"]:
                out.append(
                    _c(
                        "STOP_LOSS_MAKING",
                        "critical",
                        "cohort",
                        c.id,
                        c.id,
                        f"{code}: forecast margin is LOSS-MAKING -- stop",
                        f"Reforecast contribution margin ${fc['economics']['contribution_margin']:,.0f} (plan ${fc['plan_cm']:,.0f}).",
                        "Stop intake for this cohort and reroute remaining devices to recycling or renegotiate terms.",
                    )
                )
            elif fc["below_floor"]:
                out.append(
                    _c(
                        "CRITICAL_MARGIN_INTERVENTION",
                        "critical",
                        "cohort",
                        c.id,
                        c.id,
                        f"{code}: forecast margin {_fp(fc['economics']['cm_pct'])} of net revenue, below {fc['margin_floor_pct'] * 100:.0f}% floor",
                        f"Reforecast CM ${fc['economics']['contribution_margin']:,.0f} vs approved plan ${fc['plan_cm']:,.0f} "
                        f"({fc['delta_cm']:+,.0f}).",
                        "Reprice remaining supply, reroute high-exception models, or pause intake.",
                    )
                )
        v = variance(db, c, as_of, policy)
        for r in v["rows"]:
            if (
                r["key"] in ("repair_cost", "labor_cost")
                and r["variance_pct"] is not None
                and r["variance_pct"] > policy.cost_overrun_warning
                and r["plan"]
                and r["plan"] > 500
            ):
                out.append(
                    _c(
                        "COST_OVERRUN",
                        "warning",
                        "cohort",
                        c.id,
                        c.id,
                        f"{code}: {r['label'].lower()} {_pct(r['variance_pct'])} over budget",
                        f"Actual ${float(r['actual']):,.0f} vs progress-adjusted budget ${float(r['plan']):,.0f}.",
                        "Review rework drivers; confirm parts pricing and technician routing.",
                    )
                )
                break

    inv = inventory_aging(db, as_of, policy.target_days_to_sale)
    for row in inv["rows"]:
        if row["age_days"] > policy.target_days_to_sale:
            c = db.get(DeviceCohort, row["cohort_id"])
            out.append(
                _c(
                    "AGED_INVENTORY",
                    "warning",
                    "cohort",
                    row["cohort_id"],
                    row["cohort_id"],
                    f"{c.cohort_code}: {row['unsold_units']} unsold units aged {row['age_days']}d",
                    f"${float(row['value']):,.0f} at cost listed on {row['channel']} beyond the {policy.target_days_to_sale}-day target.",
                    "Markdown, bundle to a bulk buyer, or relist on a second channel.",
                )
            )

    aging = collections_aging(db, as_of)
    pay_by_cohort: dict[str, list] = {}
    for it in aging["items"]:
        if it["days_overdue"] <= 0:
            continue
        if it["kind"] == "invoice":
            c = db.get(DeviceCohort, it["cohort_id"]) if it["cohort_id"] else None
            out.append(
                _c(
                    "OVERDUE_COLLECTION",
                    "warning",
                    "invoice",
                    it["id"],
                    it["cohort_id"],
                    f"Invoice {it['ref']} overdue {it['days_overdue']}d (${float(it['remaining']):,.0f})",
                    f"{it['partner']} owes ${float(it['remaining']):,.0f}; due {it['due_date']}"
                    + (f" ({c.cohort_code})." if c else "."),
                    "Escalate to partner finance; consider pausing new cohorts until settled.",
                )
            )
        else:
            pay_by_cohort.setdefault(it["cohort_id"], []).append(it)
    for cid, items in pay_by_cohort.items():
        c = db.get(DeviceCohort, cid)
        tot = sum((i["remaining"] for i in items), D(0))
        worst = max(i["days_overdue"] for i in items)
        out.append(
            _c(
                "OVERDUE_COLLECTION",
                "warning",
                "cohort",
                cid,
                cid,
                f"{c.cohort_code}: ${float(tot):,.0f} of resale payouts overdue (up to {worst}d)",
                f"{len(items)} sale payout(s) past due.",
                "Chase the marketplace / bulk buyer; shorten payout terms on the next cohort.",
            )
        )

    covered = {
        x for x in db.scalars(select(Device.cohort_id).where(Device.sanitization_status == "FAILED", Device.current_status.in_(("QUARANTINED", "SANITIZATION_FAILED"))))
    }  # cohorts already alerted at device level (same root cause, avoid a duplicate critical)  # fmt: skip
    seen: set[str] = set()
    for ev in db.scalars(
        select(ExceptionEvent).where(
            ExceptionEvent.exception_type == "data_wipe_failure", ExceptionEvent.status == "open"
        )
    ):
        if ev.cohort_id in seen or ev.cohort_id in covered:
            continue
        seen.add(ev.cohort_id)
        c = db.get(DeviceCohort, ev.cohort_id)
        out.append(
            _c("DATA_WIPE_EXCEPTION", "critical", "cohort", ev.cohort_id, ev.cohort_id, f"{c.cohort_code}: open data-wipe failure",
               "A device failed secure-wipe verification and must be quarantined before any resale.", "Quarantine device(s); re-run wipe; do not list.")
        )  # fmt: skip

    for dev in db.scalars(
        select(Device).where(
            Device.sanitization_status == "FAILED",
            Device.current_status.in_(("QUARANTINED", "SANITIZATION_FAILED")),
        )
    ):
        c = db.get(DeviceCohort, dev.cohort_id)
        out.append(
            _c("SANITIZATION_FAILURE", "critical", "device", dev.id, dev.cohort_id, f"{c.cohort_code}: device {dev.serial_number} failed data sanitization",
               "Quarantined. Blocked from robot processing, resale and shipment until re-sanitized with a passing certificate.",
               "Keep quarantined; re-run sanitization or destroy with a reason code."))  # fmt: skip

    for cell in db.scalars(select(RobotCell)):
        risky = [r for r in calendar_rows(db, as_of + timedelta(days=1), as_of + timedelta(days=60), [cell.id])
                 if d(r.assigned_hours) > d(r.available_hours) + D("0.005")]  # fmt: skip
        if risky:
            short = sum((d(r.assigned_hours) - d(r.available_hours) for r in risky), D(0))
            out.append(_c("CAPACITY_AT_RISK", "warning", "robot_cell", cell.id, None, f"{cell.cell_name}: {float(short):.1f}h assigned beyond available hours",
                          f"Downtime or maintenance reduced capacity on {len(risky)} day(s) after work was scheduled; first affected day {risky[0].calendar_date}.",
                          "Re-sequence work to another cell or extend the schedule."))  # fmt: skip

    exp = portfolio_cash_exposure(db)
    if exp > policy.working_capital_limit * D("0.8"):
        sev = "critical" if exp > policy.working_capital_limit else "warning"
        out.append(
            _c(
                "WORKING_CAPITAL",
                sev,
                "portfolio",
                "portfolio",
                None,
                f"Cash exposure ${float(exp):,.0f} is {float(exp / policy.working_capital_limit) * 100:.0f}% of the working-capital limit",
                f"Limit ${float(policy.working_capital_limit):,.0f}. New cohorts will be flagged Review Required.",
                "Accelerate collections and sell-through before accepting new supply.",
            )
        )
    return out


def evaluate(db: Session, as_of: date, policy: Policy) -> list[Alert]:
    db.flush()  # sessions run with autoflush off; rules must see this request's pending changes
    cands = {(c["alert_type"], c["entity_type"], c["entity_id"]): c for c in candidates(db, as_of, policy)}
    existing = list(db.scalars(select(Alert)))
    by_key: dict[tuple, list[Alert]] = {}
    for a in existing:
        by_key.setdefault((a.alert_type, a.entity_type, a.entity_id), []).append(a)

    for key, c in cands.items():
        rows = by_key.get(key, [])
        live = next((r for r in rows if r.status in AUTO_OPEN), None)
        if live:
            live.title, live.description, live.recommended_action, live.severity = (
                c["title"],
                c["description"],
                c["recommended_action"],
                c["severity"],
            )
            if live.status == "snoozed" and live.snoozed_until and live.snoozed_until <= as_of:
                live.status = "open"
            continue
        recent = [
            r
            for r in rows
            if r.status == "resolved"
            and r.resolved_at
            and (datetime.combine(as_of, datetime.min.time()) - r.resolved_at) < timedelta(days=7)
        ]
        if recent:
            continue
        db.add(Alert(comments=[], **c))
    for key, rows in by_key.items():
        if key not in cands:
            for r in rows:
                if r.status in AUTO_OPEN:
                    r.status, r.resolved_at = "resolved", utcnow()
                    r.comments = (r.comments or []) + [
                        {
                            "by": "system",
                            "at": utcnow().isoformat(),
                            "text": "Auto-resolved: condition cleared.",
                        }
                    ]
    db.flush()
    return list(db.scalars(select(Alert)))


def act(
    db: Session,
    alert_id: str,
    action: str,
    *,
    user: str = "demo.user",
    text: str | None = None,
    owner: str | None = None,
    snooze_days: int = 3,
    as_of: date | None = None,
) -> Alert:
    a = db.get(Alert, alert_id)
    if not a:
        raise NotFound("Alert")
    before = {"status": a.status, "assigned_to": a.assigned_to, "severity": a.severity}
    note = lambda t: (a.comments or []) + [{"by": user, "at": utcnow().isoformat(), "text": t}]  # noqa: E731
    if action == "assign":
        a.assigned_to = owner
        a.comments = note(f"Assigned to {owner}.")
    elif action == "comment":
        if not text:
            raise DomainError("Comment text required.", code="invalid")
        a.comments = note(text)
    elif action == "resolve":
        a.status, a.resolved_at = "resolved", utcnow()
        a.comments = note(text or "Resolved.")
    elif action == "snooze":
        a.status, a.snoozed_until = "snoozed", (as_of or date.today()) + timedelta(days=snooze_days)
        a.comments = note(f"Snoozed for {snooze_days} days.")
    elif action == "escalate":
        a.status = "escalated"
        if a.severity == "warning":
            a.severity = "critical"
        a.comments = note(text or "Escalated to leadership.")
    elif action == "reopen":
        a.status, a.resolved_at = "open", None
    else:
        raise DomainError(f"Unknown alert action '{action}'.", code="invalid")
    audit.log(
        db,
        "alert",
        a.id,
        f"alert_{action}",
        before,
        {"status": a.status, "assigned_to": a.assigned_to, "severity": a.severity, "text": text},
        user=user,
    )
    return a


def serialize(a: Alert, code_by_id: dict[str, str]) -> dict:
    return {
        "id": a.id,
        "alert_type": a.alert_type,
        "severity": a.severity,
        "entity_type": a.entity_type,
        "entity_id": a.entity_id,
        "cohort_id": a.cohort_id,
        "cohort_code": code_by_id.get(a.cohort_id or ""),
        "title": a.title,
        "description": a.description,
        "recommended_action": a.recommended_action,
        "status": a.status,
        "assigned_to": a.assigned_to,
        "snoozed_until": a.snoozed_until.isoformat() if a.snoozed_until else None,
        "comments": a.comments or [],
        "created_at": a.created_at.isoformat(),
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
    }


_ = (d, safe_div, Invoice, SalesTransaction)
