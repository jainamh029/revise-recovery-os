"""Dashboard drill-downs (DASH-08). Each builder returns the rows behind one dashboard card, computed from the SAME primitives,
windows and scope rules the dashboard uses, plus backend-computed totals and reconciliation metadata. The browser never computes a
total. Ratios (utilisation, exception rate, CM%) are always recomputed from summed numerators and denominators -- never averaged.

Nothing here changes a formula, threshold, gate or alert rule.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Alert,
    CapacityCalendar,
    CohortAssignment,
    CohortBudgetVersion,
    DailyOperation,
    Device,
    DeviceCohort,
    ExceptionEvent,
    Partner,  # fmt: skip
    ResaleListing,
    RobotCell,
)
from ..policy import Policy
from .alerts import serialize as serialize_alert
from .analytics import _alert_in_scope, collection_in_scope, executive, scope_windows
from .cohort_metrics import ACTIVE
from .economics import d
from .finance import cohort_actuals, collections_aging, inventory_aging
from .variance import reforecast

D = Decimal
ZERO = D(0)
METRICS = ("realized_cm", "forecast_cm", "active_cohorts", "cash_exposure", "overdue_collections", "robot_utilization", "exceptions",
           "critical_alerts", "aged_inventory")  # fmt: skip
TOL = D("0.005")


def _m(x) -> str:
    return f"${float(x):,.0f}" if x is not None else "n/a"


def _pct(x) -> str:
    return "n/a" if x is None else f"{float(x) * 100:.1f}%"


def _ratio(n, dn):
    return n / dn if dn and dn > 0 else None


def _col(key, label, typ="text", **kw):
    return {"key": key, "label": label, "type": typ, **kw}


def _fmt(x):
    return x.isoformat() if x else None


class Ctx:
    def __init__(self, db: Session, as_of: date, policy: Policy, scope):
        self.db, self.as_of, self.policy, self.scope = db, as_of, policy, scope
        self.ids = scope.cohort_ids
        all_c = list(db.scalars(select(DeviceCohort).order_by(DeviceCohort.cohort_code)))
        self.all = {c.id: c for c in all_c}
        self.cohorts = [c for c in all_c if c.id in self.ids]
        self.live = [c for c in self.cohorts if c.status in ACTIVE or c.status == "closed"]
        self.w = scope_windows(db, scope, as_of)
        self.partner = {p.id: p.name for p in db.scalars(select(Partner))}
        self.cell = {c.id: c for c in db.scalars(select(RobotCell))}

    def code(self, cid):
        c = self.all.get(cid)
        return c.cohort_code if c else None

    def partner_of(self, cid):
        c = self.all.get(cid)
        return self.partner.get(c.partner_id) if c else None

    def window_label(self, key):
        s, e = self.w[key]
        return f"{_fmt(s) or 'start of records'} → {_fmt(e)}"

    def dashboard(self) -> dict:
        return executive(self.db, self.as_of, self.policy, self.scope)


def _recon(ctx, *, formula, detail, dash_key_value, money=True, statement=None, verify=True, count=None):
    """Reconciliation metadata. `dash_key_value` is the dashboard figure (computed by the real dashboard code path)."""
    matches = None
    if verify and dash_key_value is not None:
        matches = abs(D(str(detail)) - D(str(dash_key_value))) <= TOL
    st = statement or ""
    if matches is False:
        st += "  WARNING: this does NOT match the dashboard card."
    return {
        "formula": formula,
        "detail_value": detail,
        "dashboard_value": dash_key_value,
        "matches": matches,
        "statement": st,
        "count": count,
        "tolerance": float(TOL),
    }


def _payload(ctx: Ctx, metric, title, basis, tables, recon, notes=None, window_keys=(), extra=None):
    today = ctx.as_of.isoformat()
    if basis == "current":
        basis_label = f"Current as of {today}"
    elif basis == "current_balance":
        basis_label = f"Current balance as of {today}"
    else:
        basis_label = "Date-filtered: " + "; ".join(
            f"{k.replace('_', ' ')} {ctx.window_label(k)}" for k in window_keys
        )
    return {
        "metric": metric, "title": title, "basis": basis, "basis_label": basis_label, "as_of": today,
        "windows": {k: {"start": _fmt(ctx.w[k][0]), "end": _fmt(ctx.w[k][1])} for k in window_keys},
        "filters": {"count": ctx.scope.active_count, "applied": ctx.scope.applied, "requested": ctx.scope.requested,
                    "cohorts_in_scope": len(ctx.cohorts)},
        "notes": notes or [], "tables": tables, "reconciliation": recon, "empty": all(len(t["rows"]) == 0 for t in tables), **(extra or {}),
    }  # fmt: skip


def _scope_notes(ctx: Ctx, margin_card: bool) -> list[str]:
    n = []
    if ctx.scope.channel and margin_card:
        n.append("Resale-channel filter: this is CHANNEL RECOVERY MARGIN -- matching sales only. Acquisition, robot-processing and other cohort costs "
                 "are not stored per resale channel, so they are NOT allocated to it, and service invoices are excluded.")  # fmt: skip
    if ctx.scope.cell_id and margin_card:
        n.append("Robot-cell filter: margin shows the COHORTS THAT USED this cell (whole-cohort figures). Revenue and acquisition costs are not "
                 "recorded per cell, so this is not cell-attributable margin.")  # fmt: skip
    return n


def _driver(ctx: Ctx, c: DeviceCohort) -> str:
    fc = reforecast(ctx.db, c, ctx.as_of)
    if not fc:
        return "No approved plan"
    dr = fc["drivers"]
    cands = [
        (
            dr["exception_mult"] - 1,
            f"Exception rate {_pct(dr['observed_exception_rate'])} vs {_pct(dr['plan_exception_rate'])} plan",
        ),
        (dr["repair_mult"] - 1, f"Repair cost per exception ×{dr['repair_mult']:.2f} vs plan"),
        (dr["process_mult"] - 1, f"Process time per device ×{dr['process_mult']:.2f} vs plan"),
        (1 - dr["price_mult"], f"Realised resale price ×{dr['price_mult']:.2f} vs plan"),
        ((0.9 - dr["uptime"]) / 0.9, f"Robot uptime {_pct(dr['uptime'])}"),
    ]
    score, text = max(cands, key=lambda x: x[0])
    return text if score > 0.10 else "Tracking plan"


def _alert_state(ctx: Ctx, cid: str, alerts) -> str:
    mine = [a for a in alerts if a.cohort_id == cid]
    if not mine:
        return "No open alerts"
    crit = len([a for a in mine if a.severity == "critical"])
    warn = len(mine) - crit
    top = next((a for a in mine if a.severity == "critical"), mine[0])
    return f"{crit} critical, {warn} warning · {top.title}"


def _open_alerts(db):
    return list(
        db.scalars(
            select(Alert).where(Alert.status.in_(("open", "escalated"))).order_by(Alert.created_at.desc())
        )
    )


# ================================================================== A. realized CM
def realized_cm(ctx: Ctx, p) -> dict:
    rs, re = ctx.w["realized_margin"]
    rows = []
    for c in ctx.live:
        a = (
            cohort_actuals(ctx.db, c.id, ctx.as_of)
            if (rs is None and not ctx.scope.channel)
            else cohort_actuals(ctx.db, c.id, re, start=rs, channel=ctx.scope.channel)
        )
        net, cm = a["net_recognized_revenue"], a["contribution_margin"]
        rows.append({
            "code": c.cohort_code, "name": c.cohort_name, "href": f"/cohorts/{c.id}", "partner": ctx.partner_of(c.id), "status": c.status,
            "gross_sale_proceeds": a["gross_sale_proceeds"], "marketplace_fees": a["marketplace_fees_total"], "refunds": a["sales"]["refunds"],
            "discounts": a["discounts"], "service_invoiced": a["invoices"]["billed"], "net_recognized_revenue": net,
            "direct_variable_cost": a["cost_total"], "contribution_margin": cm, "cm_pct": _ratio(cm, net),
            "driver": "Channel recovery margin (processing costs not attributable)" if ctx.scope.channel else _driver(ctx, c),
        })  # fmt: skip
    keys = (
        "gross_sale_proceeds",
        "marketplace_fees",
        "refunds",
        "discounts",
        "service_invoiced",
        "net_recognized_revenue",
        "direct_variable_cost",
        "contribution_margin",
    )
    tot = {k: sum((r[k] for r in rows), ZERO) for k in keys}
    tot["cm_pct"] = _ratio(
        tot["contribution_margin"], tot["net_recognized_revenue"]
    )  # total CM / total net revenue, NOT an average of row %
    cols = [
        _col("code", "Cohort", "link", href_key="href"), _col("name", "Name"), _col("partner", "Partner"), _col("status", "Status (current)", "chip"),
        _col("gross_sale_proceeds", "Gross sale proceeds", "money"), _col("marketplace_fees", "Marketplace fees", "money"), _col("refunds", "Refunds", "money"),
        _col("discounts", "Discounts", "money"), _col("service_invoiced", "Service invoices", "money"),
        _col("net_recognized_revenue", "Net recognized revenue", "money"), _col("direct_variable_cost", "Direct variable costs", "money"),
        _col("contribution_margin", "Realized CM", "money"), _col("cm_pct", "CM % of net revenue", "pct"), _col("driver", "Primary driver"),
    ]  # fmt: skip
    dash = ctx.dashboard()["actual_cm_to_date"] if p.verify else None
    st = f"{_m(tot['contribution_margin'])} realized contribution margin across {len(rows)} cohort{'s' if len(rows) != 1 else ''}, matching the filtered dashboard total."
    return _payload(
        ctx, "realized_cm", "Realized contribution margin", "dated", [{"id": "cohorts", "title": "Cohorts", "columns": cols, "rows": rows, "totals": tot}],
        _recon(ctx, formula="Σ contribution_margin = Σ net_recognized_revenue − Σ direct_variable_cost;  CM % = Σ CM ÷ Σ net_recognized_revenue",
               detail=tot["contribution_margin"], dash_key_value=dash, statement=st, verify=p.verify, count=len(rows)),
        _scope_notes(ctx, True) + ["Net recognized revenue = gross sale proceeds − marketplace fees − refunds − discounts (+ invoiced service fees). "
                                   "Merchant-paid shipping and return labels are in direct variable costs."],
        ("realized_margin",),
    )  # fmt: skip


# ================================================================== B. forecast CM
def forecast_cm(ctx: Ctx, p) -> dict:
    alerts = _open_alerts(ctx.db)
    rows = []
    for c in ctx.live:
        v = ctx.db.get(CohortBudgetVersion, c.approved_budget_version_id)
        fc = reforecast(ctx.db, c, ctx.as_of)
        plan = d(v.expected_contribution_margin)
        f = D(str(fc["economics"]["contribution_margin"])) if fc else ZERO
        plan_pct = _ratio(plan, d(v.expected_revenue))
        fpct = fc["economics"]["cm_pct"] if fc else None
        rec = (v.recommendation or {}).get("decision")
        rows.append({
            "code": c.cohort_code, "name": c.cohort_name, "href": f"/cohorts/{c.id}", "partner": ctx.partner_of(c.id), "status": c.status,
            "plan_cm": plan, "forecast_cm": f, "variance": f - plan, "plan_cm_pct": plan_pct, "forecast_cm_pct": fpct,
            "pct_variance_pts": None if plan_pct is None or fpct is None else D(str(fpct)) - plan_pct, "driver": _driver(ctx, c),
            "recommendation": rec, "alert_state": _alert_state(ctx, c.id, alerts),
        })  # fmt: skip
    tot = {
        "plan_cm": sum((r["plan_cm"] for r in rows), ZERO),
        "forecast_cm": sum((r["forecast_cm"] for r in rows), ZERO),
    }
    tot["variance"] = tot["forecast_cm"] - tot["plan_cm"]
    cols = [
        _col("code", "Cohort", "link", href_key="href"), _col("partner", "Partner"), _col("status", "Status", "chip"), _col("plan_cm", "Approved plan CM", "money"),
        _col("forecast_cm", "Current forecast CM", "money"), _col("variance", "Variance $", "money"), _col("plan_cm_pct", "Plan CM %", "pct"),
        _col("forecast_cm_pct", "Forecast CM %", "pct"), _col("pct_variance_pts", "CM % variance (pts)", "pct"), _col("driver", "Main driver"),
        _col("recommendation", "Recommendation at approval", "chip"), _col("alert_state", "Alert state"),
    ]  # fmt: skip
    dash = ctx.dashboard()["forecast_cm"] if p.verify else None
    st = f"{_m(tot['forecast_cm'])} forecast contribution margin across {len(rows)} cohort{'s' if len(rows) != 1 else ''}, matching the filtered dashboard total. Current as of {ctx.as_of}."
    return _payload(
        ctx, "forecast_cm", "Forecast contribution margin", "current", [{"id": "cohorts", "title": "Cohorts", "columns": cols, "rows": rows, "totals": tot}],
        _recon(ctx, formula="Σ forecast_cm over live cohorts in scope (reforecast of the approved model on observed behaviour, as of today)", detail=tot["forecast_cm"],
               dash_key_value=dash, statement=st, verify=p.verify, count=len(rows)),
        ["Forecast is a current-state reforecast. It is not reconstructed for past dates, so the date filter does not change it.", *_scope_notes(ctx, False)],
    )  # fmt: skip


# ================================================================== C. active cohorts
def active_cohorts(ctx: Ctx, p) -> dict:
    alerts = _open_alerts(ctx.db)
    rows = []
    for c in ctx.cohorts:
        if c.status not in ACTIVE:
            continue
        v = ctx.db.get(CohortBudgetVersion, c.approved_budget_version_id)
        fc = reforecast(ctx.db, c, ctx.as_of)
        mine = [a for a in alerts if a.cohort_id == c.id]
        rows.append({
            "code": c.cohort_code, "name": c.cohort_name, "href": f"/cohorts/{c.id}", "partner": ctx.partner_of(c.id), "status": c.status,
            "planned_units": c.expected_device_count, "actual_units": c.actual_device_count, "plan_cm": d(v.expected_contribution_margin),
            "forecast_cm": D(str(fc["economics"]["contribution_margin"])) if fc else None, "target_completion": _fmt(c.target_completion_date),
            "main_risk": (next((a for a in mine if a.severity == "critical"), mine[0]).title if mine else "No open alerts"),
        })  # fmt: skip
    tot = {"count": len(rows), "planned_units": sum(r["planned_units"] for r in rows), "actual_units": sum(r["actual_units"] for r in rows),
           "plan_cm": sum((r["plan_cm"] for r in rows), ZERO), "forecast_cm": sum((r["forecast_cm"] or ZERO for r in rows), ZERO)}  # fmt: skip
    cols = [
        _col("code", "Cohort", "link", href_key="href"), _col("name", "Name"), _col("partner", "Partner"), _col("status", "Status", "chip"),
        _col("planned_units", "Planned units", "int"), _col("actual_units", "Actual units received", "int"), _col("plan_cm", "Planned CM", "money"),
        _col("forecast_cm", "Forecast CM", "money"), _col("target_completion", "Target completion", "date"), _col("main_risk", "Main risk / alert"),
    ]  # fmt: skip
    dash = ctx.dashboard()["active_cohorts"] if p.verify else None
    st = f"{len(rows)} active cohort{'s' if len(rows) != 1 else ''} (current status as of {ctx.as_of}), matching the filtered dashboard card."
    return _payload(
        ctx, "active_cohorts", "Active cohorts", "current", [{"id": "cohorts", "title": "Cohorts in an active status", "columns": cols, "rows": rows, "totals": tot}],
        _recon(ctx, formula="count of cohorts in scope whose CURRENT status is in-intake … partially-sold", detail=len(rows), dash_key_value=dash, money=False,
               statement=st, verify=p.verify, count=len(rows)),
        ["Status is the cohort's current status; status history is not snapshotted, so it is not backdated to the selected range."],
    )  # fmt: skip


# ================================================================== D. cash exposure
def cash_exposure(ctx: Ctx, p) -> dict:
    sc = ctx.scope
    lines = []
    inv_total = ZERO
    if sc.channel:
        ia = inventory_aging(
            ctx.db,
            ctx.as_of,
            ctx.policy.target_days_to_sale,
            ctx.ids if sc.cohort_filtered else None,
            sc.channel,
        )
        per: dict[str, Decimal] = {}
        for r in ia["rows"]:
            per[r["cohort_id"]] = per.get(r["cohort_id"], ZERO) + r["value"]
        inv_rows = per
    else:
        inv_rows = {c.id: cohort_actuals(ctx.db, c.id, ctx.as_of)["inventory_value"] for c in ctx.live}
    for cid, val in inv_rows.items():
        inv_total += val
        lines.append({"section": "inventory", "cohort": ctx.code(cid), "partner": ctx.partner_of(cid), "reference": "Unsold inventory — unrecovered acquisition + processing cost",
                      "record_id": f"inventory:{cid}", "amount": val})  # fmt: skip
    for it in collections_aging(ctx.db, ctx.as_of)["items"]:
        if not collection_in_scope(it, sc):
            continue
        lines.append({"section": "payouts" if it["kind"] == "payout" else "invoices", "cohort": ctx.code(it["cohort_id"]), "partner": it["partner"] or ctx.partner_of(it["cohort_id"]),
                      "reference": it["ref"], "record_id": f"{it['kind']}:{it['id']}", "amount": it["remaining"]})  # fmt: skip
    by = {
        s: sum((ln["amount"] for ln in lines if ln["section"] == s), ZERO)
        for s in ("inventory", "payouts", "invoices")
    }
    total = sum(by.values(), ZERO)
    lines.sort(key=lambda r: (["inventory", "payouts", "invoices"].index(r["section"]), -float(r["amount"])))
    cols = [
        _col("section", "Section", "chip"),
        _col("cohort", "Cohort"),
        _col("partner", "Partner / customer"),
        _col("reference", "Reference"),
        _col("amount", "Current amount", "money"),
    ]
    tot = {"amount": total, "by_section": by}
    dash = ctx.dashboard()["cash_tied_up_inventory"] if p.verify else None
    st = (f"{_m(by['inventory'])} unrecovered inventory cash (matches the dashboard “Cash tied up in inventory” card) + {_m(by['payouts'])} uncollected marketplace payouts "
          f"+ {_m(by['invoices'])} outstanding service invoices = {_m(total)} total current cash exposure, as of {ctx.as_of}.")  # fmt: skip
    return _payload(
        ctx, "cash_exposure", "Cash tied up", "current", [{"id": "lines", "title": "Current cash exposure — every amount in exactly one section", "columns": cols, "rows": lines, "totals": tot}],
        _recon(ctx, formula="Card = Section 1 (inventory). Total exposure = inventory + uncollected payouts + outstanding service invoices; each source record appears once",
               detail=by["inventory"], dash_key_value=dash, statement=st, verify=p.verify, count=len(lines)),
        ["Section 1 is unsold inventory at cost; sections 2–3 are cash still owed to us. A sold device leaves inventory (section 1) and appears only as a payout receivable "
         "(section 2); service invoices are separate records — nothing is counted twice.",
         "Current balances only: there are no historical snapshots, so this is not reconstructed for past dates.",
         *([ "Resale-channel filter: inventory is that channel's listed-unsold units at cohort unit cost; invoices cannot be attributed to a channel and are excluded."] if sc.channel else [])],
        extra={"sections": by},
    )  # fmt: skip


# ================================================================== E. overdue collections
def overdue_collections(ctx: Ctx, p) -> dict:
    only_overdue = (p.status or "overdue") == "overdue"
    rows = []
    for it in collections_aging(ctx.db, ctx.as_of)["items"]:
        if not collection_in_scope(it, ctx.scope) or (only_overdue and it["days_overdue"] <= 0):
            continue
        od = it["days_overdue"]
        who = it["partner"] or it["channel"] or ctx.partner_of(it["cohort_id"])
        action = ("Not yet due — monitor" if od <= 0 else "Send payment reminder" if od <= 30 else
                  f"Send formal demand to {who}; confirm a payment date" if od <= 60 else f"Escalate to {who} finance; pause new cohorts until settled")  # fmt: skip
        rows.append({
            "partner": who, "cohort": ctx.code(it["cohort_id"]), "href": f"/cohorts/{it['cohort_id']}" if it["cohort_id"] else None, "kind": it["kind"],
            "reference": it["ref"], "issue_date": it["issued"], "due_date": it["due_date"], "days_overdue": od, "original_amount": it["amount"],
            "collected": it["amount"] - it["remaining"], "outstanding": it["remaining"], "next_action": action,
        })  # fmt: skip
    rows.sort(key=lambda r: -r["days_overdue"])
    tot = {k: sum((r[k] for r in rows), ZERO) for k in ("original_amount", "collected", "outstanding")}
    cols = [_col("partner", "Partner / customer"), _col("cohort", "Cohort", "link", href_key="href"), _col("kind", "Type", "chip"), _col("reference", "Invoice / payout ref"),
            _col("issue_date", "Issue date", "date"), _col("due_date", "Due date", "date"), _col("days_overdue", "Days overdue", "int"),
            _col("original_amount", "Original amount", "money"), _col("collected", "Collected", "money"), _col("outstanding", "Outstanding balance", "money"),
            _col("next_action", "Suggested next action")]  # fmt: skip
    dash = ctx.dashboard()["overdue_collections"] if (p.verify and only_overdue) else None
    st = f"{_m(tot['outstanding'])} {'overdue' if only_overdue else 'outstanding'} across {len(rows)} item{'s' if len(rows) != 1 else ''}, matching the filtered dashboard card. Current balance as of {ctx.as_of}."
    return _payload(
        ctx, "overdue_collections", "Overdue collections" if only_overdue else "Collections outstanding", "current_balance",
        [{"id": "items", "title": "Invoices and marketplace payouts", "columns": cols, "rows": rows, "totals": tot}],
        _recon(ctx, formula="Σ outstanding balance of items past their due date (balance = original amount − cash received to date)", detail=tot["outstanding"],
               dash_key_value=dash, statement=st, verify=p.verify, count=len(rows)),
        ["Scope rule: invoice date / sale date decide which items are included when a date filter is set; the outstanding balance itself is always the CURRENT balance.",
         "Invoices cannot be attributed to a resale channel, so a channel filter shows that channel's payouts only."],
        extra={"status": "overdue" if only_overdue else "all"},
    )  # fmt: skip


# ================================================================== F. robot utilisation
def robot_utilization(ctx: Ctx, p) -> dict:
    sc = ctx.scope
    s, e = ctx.w["operations"]
    ops = [o for o in ctx.db.scalars(select(DailyOperation)) if o.cohort_id in ctx.ids and (not sc.cell_id or o.robot_cell_id == sc.cell_id)
           and (s is None or o.operation_date >= s) and o.operation_date <= e]  # fmt: skip
    by_ops: dict[tuple, list] = {}
    for o in ops:
        by_ops.setdefault((o.robot_cell_id, o.operation_date), []).append(o)
    assigned: dict[tuple, Decimal] = {}
    for a in ctx.db.scalars(select(CohortAssignment).where(CohortAssignment.status != "cancelled")):
        if a.cohort_id not in ctx.ids or (sc.cell_id and a.robot_cell_id != sc.cell_id):
            continue
        for iso, h in (a.daily_plan or {}).items():
            k = (a.robot_cell_id, date.fromisoformat(iso))
            assigned[k] = assigned.get(k, ZERO) + d(h)
    rows = []
    # An empty scope (no cohort matches) has no activity to show; the dashboard reports 0% for it and so does this total.
    calendar = (
        []
        if not ctx.cohorts
        else ctx.db.scalars(select(CapacityCalendar).order_by(CapacityCalendar.calendar_date.desc()))
    )
    for cal in calendar:
        cell = ctx.cell[cal.robot_cell_id]
        if (
            cell.status != "available"
            or (sc.cell_id and cell.id != sc.cell_id)
            or (s is not None and cal.calendar_date < s)
            or cal.calendar_date > e
        ):
            continue
        mine = by_ops.get((cell.id, cal.calendar_date), [])
        avail = d(cal.scheduled_hours) - d(cal.maintenance_hours)
        prod = sum((d(o.productive_robot_hours) for o in mine), ZERO)
        rows.append({
            "cell": cell.cell_name, "date": cal.calendar_date.isoformat(), "scheduled_hours": d(cal.scheduled_hours), "maintenance_hours": d(cal.maintenance_hours),
            "downtime_hours": sum((d(o.downtime_hours) for o in mine), ZERO), "available_hours": avail, "assigned_hours": assigned.get((cell.id, cal.calendar_date), ZERO),
            "productive_hours": prod, "completed_devices": sum(o.devices_completed for o in mine), "utilization": _ratio(prod, avail),
        })  # fmt: skip
    keys = (
        "scheduled_hours",
        "maintenance_hours",
        "downtime_hours",
        "available_hours",
        "assigned_hours",
        "productive_hours",
        "completed_devices",
    )
    tot = {k: sum((r[k] for r in rows), ZERO) for k in keys}
    tot["utilization"] = _ratio(
        tot["productive_hours"], tot["available_hours"]
    )  # Σ productive ÷ Σ available -- never a mean of daily %
    cols = [_col("cell", "Robot cell"), _col("date", "Date", "date"), _col("scheduled_hours", "Scheduled h", "num"), _col("maintenance_hours", "Maintenance h", "num"),
            _col("downtime_hours", "Downtime h", "num"), _col("available_hours", "Available h", "num"), _col("assigned_hours", "Assigned h", "num"),
            _col("productive_hours", "Productive h", "num"), _col("completed_devices", "Completed devices", "int"), _col("utilization", "Utilization", "pct")]  # fmt: skip
    dash = ctx.dashboard()["utilization"] if p.verify else None
    weighted = tot["utilization"] or ZERO
    st = (f"{float(tot['productive_hours']):,.1f} productive hours ÷ {float(tot['available_hours']):,.1f} available hours = {_pct(tot['utilization'])} weighted utilization, "
          f"matching the filtered dashboard card.")  # fmt: skip
    return _payload(
        ctx, "robot_utilization", "Robot utilization", "dated", [{"id": "cell_days", "title": "Cell-days", "columns": cols, "rows": rows, "totals": tot}],
        _recon(ctx, formula="Utilization = Σ productive hours ÷ Σ available hours, available = scheduled − planned maintenance (unplanned downtime is NOT removed)",
               detail=weighted, dash_key_value=dash, money=False, statement=st, verify=p.verify, count=len(rows))
        | {"numerator": tot["productive_hours"], "denominator": tot["available_hours"]},
        ["Only cells currently in service ('available') are counted, as on the dashboard; a cell in maintenance/offline is excluded.",
         "With cohort filters, productive hours are those cohorts' hours, so utilization is their share of the cells' available hours.",
         "Available hours exclude only planned maintenance; downtime is shown separately and lowers utilization."],
        ("operations",),
    )  # fmt: skip


# ================================================================== G. exceptions
def exceptions(ctx: Ctx, p) -> dict:
    sc = ctx.scope
    s, e = ctx.w["exceptions"]
    live_ids = {c.id for c in ctx.live}
    agg: dict[tuple, dict] = {}
    for o in ctx.db.scalars(select(DailyOperation)):
        if (
            o.cohort_id not in live_ids
            or (sc.cell_id and o.robot_cell_id != sc.cell_id)
            or (s is not None and o.operation_date < s)
            or o.operation_date > e
        ):
            continue
        r = agg.setdefault(
            (o.operation_date, o.cohort_id, o.robot_cell_id),
            {"devices_entering": 0, "exceptions": 0, "under_repair": 0},
        )
        r["devices_entering"] += o.devices_started
        r["exceptions"] += o.manual_exception_count
        r["under_repair"] += o.devices_under_repair
    rows = [{"date": k[0].isoformat(), "cohort": ctx.code(k[1]), "href": f"/cohorts/{k[1]}", "cell": ctx.cell[k[2]].cell_name, **v, "rate": _ratio(D(v["exceptions"]), D(v["devices_entering"]))}
            for k, v in sorted(agg.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True)]  # fmt: skip
    tot = {
        "devices_entering": sum(r["devices_entering"] for r in rows),
        "exceptions": sum(r["exceptions"] for r in rows),
        "under_repair": sum(r["under_repair"] for r in rows),
    }
    tot["rate"] = _ratio(
        D(tot["exceptions"]), D(tot["devices_entering"])
    )  # Σ exceptions ÷ Σ devices, not a mean of row rates
    cols = [_col("date", "Date", "date"), _col("cohort", "Cohort", "link", href_key="href"), _col("cell", "Robot cell"),
            _col("devices_entering", "Devices entering processing", "int"), _col("exceptions", "Exception count", "int"), _col("under_repair", "Under repair", "int"),
            _col("rate", "Rate (row, for reference)", "pct")]  # fmt: skip
    serial = {dv.id: dv.serial_number for dv in ctx.db.scalars(select(Device))}
    ev_rows = []
    for ev in ctx.db.scalars(select(ExceptionEvent).order_by(ExceptionEvent.opened_at.desc())):
        od = ev.opened_at.date()
        if (
            ev.cohort_id not in live_ids
            or (sc.cell_id and ev.robot_cell_id != sc.cell_id)
            or (s is not None and od < s)
            or od > e
        ):
            continue
        ev_rows.append({"date": od.isoformat(), "cohort": ctx.code(ev.cohort_id), "href": f"/cohorts/{ev.cohort_id}", "serial": serial.get(ev.device_id or "", None),
                        "cell": ctx.cell[ev.robot_cell_id].cell_name if ev.robot_cell_id else None, "type": ev.exception_type, "severity": ev.severity, "status": ev.status,
                        "resolution": "Resolved" if ev.status == "resolved" else "Open", "estimated_cost": d(ev.estimated_resolution_cost), "actual_cost": d(ev.actual_resolution_cost) if ev.actual_resolution_cost is not None else None})  # fmt: skip
    ev_cols = [_col("date", "Opened", "date"), _col("cohort", "Cohort", "link", href_key="href"), _col("serial", "Device serial"), _col("cell", "Robot cell"),
               _col("type", "Exception type", "chip"), _col("severity", "Severity", "chip"), _col("status", "Status", "chip"), _col("resolution", "Resolution"),
               _col("estimated_cost", "Estimated cost", "money"), _col("actual_cost", "Actual cost", "money")]  # fmt: skip
    ev_tot = {"count": len(ev_rows), "open": len([r for r in ev_rows if r["resolution"] == "Open"]), "estimated_cost": sum((r["estimated_cost"] for r in ev_rows), ZERO),
              "actual_cost": sum((r["actual_cost"] or ZERO for r in ev_rows), ZERO)}  # fmt: skip
    dash_all = ctx.dashboard() if p.verify else None
    dash = dash_all["exception_rate"] if dash_all else None
    st = f"{tot['exceptions']:,} exceptions ÷ {tot['devices_entering']:,} devices entering processing = {_pct(tot['rate'])} weighted exception rate, matching the filtered dashboard card."
    rec = _recon(
        ctx,
        formula="Exception rate = Σ exceptions ÷ Σ devices entering processing (live cohorts in scope, operation date in window)",
        detail=tot["rate"] or ZERO,
        dash_key_value=dash,
        money=False,
        statement=st,
        verify=p.verify,
        count=len(rows),
    )
    rec |= {"numerator": tot["exceptions"], "denominator": tot["devices_entering"]}
    return _payload(
        ctx, "exceptions", "Exception rate", "dated",
        [{"id": "daily", "title": "Daily operations (reconciles to the card)", "columns": cols, "rows": rows, "totals": tot},
         {"id": "events", "title": "Logged exception events (detail log)", "columns": ev_cols, "rows": ev_rows, "totals": ev_tot}],
        rec,
        ["The rate is computed from daily operation records. The event log below gives type, severity, cost and resolution for logged events; it can contain fewer "
         "events than the daily exception counts (not every exception is individually logged), so only the daily table reconciles to the card."],
        ("exceptions",),
    )  # fmt: skip


# ================================================================== H. critical alerts
def critical_alerts(ctx: Ctx, p) -> dict:
    sev = p.severity or "critical"
    alerts = [
        a
        for a in _open_alerts(ctx.db)
        if _alert_in_scope(a, ctx.scope) and (sev == "all" or a.severity == sev)
    ]
    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: (order.get(a.severity, 3), a.created_at), reverse=False)
    code = {cid: c.cohort_code for cid, c in ctx.all.items()}
    devs = {
        dv.id: dv.serial_number
        for dv in ctx.db.scalars(select(Device).where(Device.sanitization_status == "FAILED"))
    }
    rows = []
    for a in alerts:
        base = serialize_alert(a, code)
        entity = code.get(a.cohort_id or "") or (
            ctx.cell[a.entity_id].cell_name
            if a.entity_type == "robot_cell" and a.entity_id in ctx.cell
            else a.entity_type
        )
        if a.entity_type == "device":
            entity = f"{code.get(a.cohort_id or '')} · device {devs.get(a.entity_id, a.entity_id[:8])}"
        rows.append({**base, "entity": entity, "partner": ctx.partner_of(a.cohort_id) if a.cohort_id else None,
                     "href": f"/cohorts/{a.cohort_id}" if a.cohort_id else None})  # fmt: skip
    cols = [_col("alert_type", "Alert type", "chip"), _col("severity", "Severity", "chip"), _col("entity", "Entity", "link", href_key="href"), _col("partner", "Partner"),
            _col("description", "Description"), _col("recommended_action", "Recommended action"), _col("created_at", "Opened", "date"), _col("assigned_to", "Owner"),
            _col("status", "Status", "chip")]  # fmt: skip
    dash_all = ctx.dashboard() if p.verify else None
    dash = (
        None
        if not dash_all
        else (
            sum(dash_all["alert_counts"].values()) if sev == "all" else dash_all["alert_counts"].get(sev, 0)
        )
    )
    st = f"{len(rows)} unresolved {sev if sev != 'all' else ''} alert{'s' if len(rows) != 1 else ''}, matching the filtered dashboard card. Current as of {ctx.as_of}."
    note = (
        "Portfolio-level alerts (e.g. working capital) appear only when no entity filter is active; with a cohort/cell filter only alerts attached to in-scope "
        "cohorts (or the selected cell) are shown."
        if ctx.scope.cohort_filtered
        else "No entity filter is active, so portfolio-level alerts are included."
    )
    return _payload(
        ctx, "critical_alerts", f"{sev.capitalize()} alerts" if sev != "all" else "Open alerts", "current",
        [{"id": "alerts", "title": "Unresolved alerts (open or escalated)", "columns": cols, "rows": rows, "totals": {"count": len(rows)}}],
        _recon(ctx, formula="count of open + escalated alerts of the chosen severity that are attached to in-scope entities", detail=len(rows), dash_key_value=dash, money=False,
               statement=st.replace("  ", " "), verify=p.verify, count=len(rows)),
        ["Alerts are current state and are not filtered by the date range.", note], extra={"severity": sev},
    )  # fmt: skip


# ================================================================== I. aged inventory
def aged_inventory(ctx: Ctx, p) -> dict:
    sc = ctx.scope
    target = ctx.policy.target_days_to_sale
    age_gt = p.age_gt_days if p.age_gt_days is not None else target
    ia = inventory_aging(ctx.db, ctx.as_of, target, ctx.ids if sc.cohort_filtered else None, sc.channel)
    listings = {ls.id: ls for ls in ctx.db.scalars(select(ResaleListing))}
    by_listing: dict[str, list] = {}
    for dv in ctx.db.scalars(
        select(Device).where(Device.resale_listing_id.is_not(None), Device.current_status == "LISTED")
    ):
        by_listing.setdefault(dv.resale_listing_id, []).append(dv)
    rows = []
    for r in ia["rows"]:
        if r["age_days"] <= age_gt:
            continue
        ls = listings[r["listing_id"]]
        c = ctx.all[r["cohort_id"]]
        v = (
            ctx.db.get(CohortBudgetVersion, c.approved_budget_version_id)
            if c.approved_budget_version_id
            else None
        )
        a = (v.assumptions or {}) if v else {}
        factor = (
            D(1)
            - D(str(a.get("marketplace_fee_rate", 0)))
            - D(str(a.get("return_rate", 0)))
            - D(str(a.get("discount_rate", 0)))
        )
        est = d(ls.listing_price) * r["unsold_units"] * factor
        devs = by_listing.get(ls.id, [])
        ad = r["age_days"]
        rows.append({
            "cohort": c.cohort_code, "href": f"/cohorts/{c.id}", "partner": ctx.partner_of(c.id), "channel": r["channel"], "days_listed": ad, "unsold_units": r["unsold_units"],
            "serials": ", ".join(x.serial_number for x in devs[:3]) + (f" (+{len(devs) - 3} more)" if len(devs) > 3 else "") if devs else None,
            "device_status": f"{len(devs)} device record(s) LISTED" if devs else "No serial-level record", "list_price": d(ls.listing_price), "cost_basis": r["value"],
            "estimated_recovery": est, "action": ("Mark down 15–20% or sell as a bulk lot" if ad > 90 else "Relist on a second channel or bundle" if ad > 60 else "Review price against comparable listings"),
        })  # fmt: skip
    rows.sort(key=lambda r: -r["days_listed"])
    tot = {"listings": len(rows), "unsold_units": sum(r["unsold_units"] for r in rows), "cost_basis": sum((r["cost_basis"] for r in rows), ZERO),
           "estimated_recovery": sum((r["estimated_recovery"] for r in rows), ZERO)}  # fmt: skip
    cols = [_col("cohort", "Cohort", "link", href_key="href"), _col("partner", "Partner"), _col("channel", "Listing channel"), _col("days_listed", "Days listed", "int"),
            _col("unsold_units", "Unsold units", "int"), _col("serials", "Device serials (sample)"), _col("device_status", "Current device status"),
            _col("list_price", "List price / unit", "money"), _col("cost_basis", "Cost basis", "money"), _col("estimated_recovery", "Est. net recovery", "money"),
            _col("action", "Recommended action")]  # fmt: skip
    dash = ctx.dashboard()["aged_inventory_value"] if (p.verify and age_gt == target) else None
    st = (f"{_m(tot['cost_basis'])} aged inventory at cost across {len(rows)} listing{'s' if len(rows) != 1 else ''} ({tot['unsold_units']} units older than {age_gt} days)"
          f"{', matching the filtered dashboard card' if age_gt == target else ''}. Current as of {ctx.as_of}.")  # fmt: skip
    return _payload(
        ctx, "aged_inventory", "Aged inventory", "current", [{"id": "listings", "title": f"Listings older than {age_gt} days with unsold units", "columns": cols, "rows": rows, "totals": tot}],
        _recon(ctx, formula=f"Σ cost basis (cohort unit cost × unsold units) of listings older than {age_gt} days", detail=tot["cost_basis"], dash_key_value=dash, statement=st,
               verify=p.verify, count=len(rows)),
        ["Current unsold inventory at cost. There are no historical inventory snapshots, so this is never presented as a past-date value.",
         "Estimated recovery = unsold units × list price × (1 − fee − return − discount rates from the approved plan): an estimate, not a forecast of sale.",
         "Serials shown where the serial-level ledger has devices LISTED on the listing."],
        extra={"age_gt_days": age_gt},
    )  # fmt: skip


BUILDERS = {"realized_cm": realized_cm, "forecast_cm": forecast_cm, "active_cohorts": active_cohorts, "cash_exposure": cash_exposure,
            "overdue_collections": overdue_collections, "robot_utilization": robot_utilization, "exceptions": exceptions,
            "critical_alerts": critical_alerts, "aged_inventory": aged_inventory}  # fmt: skip


def build(db: Session, metric: str, as_of: date, policy: Policy, scope, params) -> dict:
    return BUILDERS[metric](Ctx(db, as_of, policy, scope), params)


def to_csv(payload: dict, table_id: str | None = None) -> str:
    t = next((x for x in payload["tables"] if x["id"] == table_id), payload["tables"][0])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"# {payload['title']} | {payload['basis_label']} | synthetic demo data"])
    w.writerow([c["label"] for c in t["columns"]])

    def cell(c, v):
        if v is None:
            return ""
        if c["type"] == "money":
            return f"{float(v):.2f}"
        if c["type"] in ("pct",):
            return f"{float(v):.6f}"
        if c["type"] == "num":
            return f"{float(v):.2f}"
        return str(v)

    for r in t["rows"]:
        w.writerow([cell(c, r.get(c["key"])) for c in t["columns"]])
    tot = t.get("totals") or {}
    w.writerow(
        ["TOTAL"]
        + [
            cell(c, tot.get(c["key"])) if c["key"] in tot and not isinstance(tot.get(c["key"]), dict) else ""
            for c in t["columns"][1:]
        ]
    )
    return buf.getvalue()
