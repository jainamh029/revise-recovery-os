"""Test set 4 (capacity / scheduler) and Test set 5 (operations / variance)."""

import threading

import pytest

from tests.acceptance.helpers import schedule_and_receive
from tests.conftest import approved_cohort, new_cohort

WIN = dict(start_date="2026-06-16", end_date="2026-07-10")


def need_hours(client, cid):
    return client.get(f"/api/cohorts/{cid}/underwriting").json()["scenarios"]["base"]["scheduled_robot_hours"]


def assign(client, cid, cell, **kw):
    return client.post(f"/api/cohorts/{cid}/assignments", json=dict(robot_cell_id=cell, **{**WIN, **kw}))


def cohort(client, world, code, units, target="2026-07-10"):
    return approved_cohort(client, world, code=code, units=units, target=target)


@pytest.fixture()
def squeezed(client, world):
    """Cell A has ~120h pre-booked, so a 222h cohort fits in neither cell alone but fits across both."""
    blk = cohort(client, world, "BLK", 200)
    assert assign(client, blk["id"], world["cells"][0], planned_hours=120).status_code == 201
    big = cohort(client, world, "BIG", 400)
    return big, need_hours(client, big["id"])


def calendar_free(client, cell_id, start="2026-06-16", end="2026-07-10"):
    cal = client.get(f"/api/capacity/calendar?start={start}&end={end}").json()
    cell = next(c for c in cal["cells"] if c["id"] == cell_id)
    return sum(float(d["available"]) - float(d["assigned"]) for d in cell["days"])


def test_CAP_01_full_assignment_reduces_free_hours(client, world):
    c = cohort(client, world, "C1", 100)
    before = calendar_free(client, world["cells"][0])
    need = need_hours(client, c["id"])
    assert assign(client, c["id"], world["cells"][0]).status_code == 201
    assert abs((before - calendar_free(client, world["cells"][0])) - need) < 0.05


def test_CAP_02_overbooking_error_is_quantitative(client, world, squeezed):
    big, need = squeezed
    r = assign(client, big["id"], world["cells"][0])
    assert r.status_code == 409 and r.json()["error"] == "overbooked"
    d = r.json()["details"]
    assert abs(d["requested_hours"] - need) < 0.05 and abs(d["free_hours"] - (228 - 120)) < 0.6
    assert "short" in r.json()["message"]


def test_CAP_03_10_split_reconciles_hours_and_units_exactly(client, world, squeezed):
    big, need = squeezed
    free_a = calendar_free(client, world["cells"][0])
    r1 = assign(client, big["id"], world["cells"][0], planned_hours=round(free_a, 2))
    r2 = assign(client, big["id"], world["cells"][1])  # default = whatever is still unplaced
    assert r1.status_code == 201 and r2.status_code == 201, (r1.text, r2.text)
    hours = float(r1.json()["planned_hours"]) + float(r2.json()["planned_hours"])
    assert abs(hours - need) < 0.011
    assert r1.json()["planned_units"] + r2.json()["planned_units"] == 400  # integer units reconcile exactly
    again = assign(client, big["id"], world["cells"][1])
    assert (
        again.status_code == 409 and again.json()["error"] == "fully_assigned"
    )  # remaining-to-place is zero
    assert client.get(f"/api/cohorts/{big['id']}").json()["status"] == "scheduled"


def test_CAP_10_three_way_split_of_an_odd_unit_count(client, world):
    c = cohort(client, world, "ODD", 401)
    need = need_hours(client, c["id"])
    parts = [round(need / 3, 2), round(need / 3, 2)]
    units = 0
    for cell, h in zip((world["cells"][0], world["cells"][1]), parts, strict=True):
        r = assign(client, c["id"], cell, planned_hours=h)
        assert r.status_code == 201
        units += r.json()["planned_units"]
    r = assign(client, c["id"], world["cells"][0])
    assert r.status_code == 201
    assert units + r.json()["planned_units"] == 401


def test_CAP_04_sub_cent_remainder_is_fully_allocated_not_an_empty_assignment(client, world):
    c = cohort(client, world, "SUB", 100)
    need = need_hours(client, c["id"])
    assert assign(client, c["id"], world["cells"][0], planned_hours=round(need - 0.004, 4)).status_code == 201
    r = assign(client, c["id"], world["cells"][1])
    assert r.status_code == 409 and r.json()["error"] == "fully_assigned"
    assert len(client.get(f"/api/cohorts/{c['id']}").json()["assignments"]) == 1


def test_CAP_05_06_exact_capacity_accepted_one_hundredth_over_rejected(client, world):
    a = cohort(client, world, "EXACT", 100)
    b = cohort(client, world, "OVER", 100)
    two_days = dict(start_date="2026-06-16", end_date="2026-06-17")  # 2 x 12h = 24h on Cell B
    assert (
        assign(client, a["id"], world["cells"][1], planned_hours=24, **two_days).status_code == 201
    )  # CAP-05
    assert abs(calendar_free(client, world["cells"][1], "2026-06-16", "2026-06-17")) < 0.011
    r = assign(client, b["id"], world["cells"][0], planned_hours=24.01, **two_days)  # CAP-06
    assert r.status_code == 409 and r.json()["error"] == "overbooked"
    assert (
        calendar_free(client, world["cells"][0], "2026-06-16", "2026-06-17") == 24
    )  # nothing leaked into the calendar


def test_CAP_07_blocked_cell_rejects_assignments(client, world):
    c = cohort(client, world, "MNT", 100)
    for status in ("maintenance", "offline", "blocked"):
        client.patch(f"/api/robot-cells/{world['cells'][0]}", json=dict(status=status))
        r = assign(client, c["id"], world["cells"][0])
        assert r.status_code == 409 and r.json()["error"] == "cell_blocked"


def test_CAP_08_planned_downtime_after_assignment_shows_schedule_risk(client, world):
    c = cohort(client, world, "DWN", 100)
    assert assign(client, c["id"], world["cells"][0]).status_code == 201
    free_before = calendar_free(client, world["cells"][0])
    assert not [a for a in client.get("/api/alerts").json() if a["alert_type"] == "CAPACITY_AT_RISK"]
    # a full-day maintenance block lands on a day that already carries work
    day = next(
        d
        for d in client.get("/api/capacity/calendar?start=2026-06-16&end=2026-07-10").json()["cells"][0][
            "days"
        ]
        if float(d["assigned"]) > 0
    )["date"]
    r = client.post(
        "/api/capacity/calendar",
        json=dict(
            robot_cell_id=world["cells"][0],
            start_date=day,
            end_date=day,
            scheduled_hours=12,
            maintenance_hours=12,
        ),
    )
    assert r.status_code == 201
    assert calendar_free(client, world["cells"][0]) < free_before
    risk = [a for a in client.get("/api/alerts").json() if a["alert_type"] == "CAPACITY_AT_RISK"]
    assert len(risk) == 1 and risk[0]["severity"] == "warning"


def test_CAP_09_sla_infeasible_cohort_is_flagged_review_and_cannot_be_scheduled(client, world):
    c = new_cohort(client, world, code="SLA", units=400, intake="2026-06-16", target="2026-06-17")
    uw = client.post(f"/api/cohorts/{c['id']}/underwrite").json()
    assert uw["decision"] == "review" and any(
        ch["key"] == "robot_capacity" and ch["status"] == "fail" for ch in uw["checks"]
    )
    client.post(
        f"/api/cohorts/{c['id']}/approve", json=dict(approver="cfo", rationale="accept despite review")
    )
    r = assign(client, c["id"], world["cells"][0], end_date="2026-06-17")
    assert r.status_code == 409 and r.json()["error"] in ("sla_breach", "overbooked")


def test_CAP_11_cancel_restores_capacity_exactly_once(client, world):
    c = cohort(client, world, "CXL", 100)
    before = calendar_free(client, world["cells"][0])
    a = assign(client, c["id"], world["cells"][0]).json()
    assert calendar_free(client, world["cells"][0]) < before
    assert client.delete(f"/api/assignments/{a['id']}").status_code == 200
    assert abs(calendar_free(client, world["cells"][0]) - before) < 0.011
    assert (
        client.delete(f"/api/assignments/{a['id']}").status_code == 409
    )  # second cancel must not restore again
    assert abs(calendar_free(client, world["cells"][0]) - before) < 0.011
    assert (
        assign(client, c["id"], world["cells"][0]).status_code == 201
    )  # remaining-to-place recalculated to full


def test_CAP_12_concurrent_requests_never_double_book(client, world):
    """Invariant: assigned <= available on every day, however the two requests interleave.
    SQLite serialises writers (so one request may fail with a lock error); PostgreSQL relies on SELECT ... FOR UPDATE."""
    a, b = cohort(client, world, "RACE1", 100), cohort(client, world, "RACE2", 100)
    two_days = dict(start_date="2026-06-16", end_date="2026-06-17")
    codes = []

    def go(cid):
        try:
            codes.append(
                assign(client, cid, world["cells"][0], planned_hours=16, **two_days).status_code
            )  # 2 x 16h > 24h free
        except Exception:  # noqa: BLE001  (SQLite "database is locked")
            codes.append(500)

    ts = [threading.Thread(target=go, args=(c["id"],)) for c in (a, b)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert codes.count(201) <= 1, codes
    cal = client.get("/api/capacity/calendar?start=2026-06-16&end=2026-06-17").json()
    for d in next(c for c in cal["cells"] if c["id"] == world["cells"][0])["days"]:
        assert float(d["assigned"]) <= float(d["available"]) + 0.005


# ---------------------------------------------------------------------------------------------- Test set 5
def post_day(client, cid, cell, day, **kw):
    body = dict(cohort_id=cid, robot_cell_id=cell, operation_date=day, devices_received=0, devices_started=20, devices_completed=20, first_pass_completions=16,
                manual_exception_count=2, quality_approved_count=20, productive_robot_hours=8, downtime_hours=0, technician_hours=1, repair_parts_cost=0)  # fmt: skip
    body.update(kw)
    r = client.post("/api/operations/daily", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def variance(client, cid):
    return {x["key"]: x for x in client.get(f"/api/cohorts/{cid}/variance").json()["rows"]}


def test_OPS_01_to_05_rates_are_exactly_the_documented_ratios(client, world):
    c = schedule_and_receive(
        client, world, "OPS1", units=100, day="2026-06-10"
    )  # that day: 100 started/completed/first-pass
    client.post(
        "/api/capacity/calendar",
        json=dict(
            robot_cell_id=world["cells"][1], start_date="2026-06-12", end_date="2026-06-12", scheduled_hours=8
        ),
    )
    post_day(client, c["id"], world["cells"][1], "2026-06-12", devices_started=100, devices_completed=100, first_pass_completions=80,
             manual_exception_count=28, productive_robot_hours=6, downtime_hours=2)  # fmt: skip
    v = variance(client, c["id"])
    # cumulative: started 200, completed 200, first pass 180, exceptions 28
    assert abs(v["fpy"]["actual"] - 0.90) < 1e-9 and abs(v["exception_rate"]["actual"] - 28 / 200) < 1e-9
    solo = schedule_and_receive(client, world, "OPS2", units=10, day="2026-06-11")
    cid = solo["id"]
    # isolate a clean 100/80/28 day on a fresh cohort (OPS-04/05) and an 8h day with 6 productive hours (OPS-03)
    c3 = approved_cohort(client, world, code="OPS3", units=100)
    client.post(
        f"/api/cohorts/{c3['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][0], start_date="2026-06-09", end_date="2026-07-10"),
    )
    client.post(
        "/api/capacity/calendar",
        json=dict(
            robot_cell_id=world["cells"][0], start_date="2026-06-09", end_date="2026-06-09", scheduled_hours=8
        ),
    )
    post_day(client, c3["id"], world["cells"][0], "2026-06-09", devices_received=100, devices_started=100, devices_completed=100, first_pass_completions=80,
             manual_exception_count=28, productive_robot_hours=6)  # fmt: skip
    v3 = variance(client, c3["id"])
    assert v3["utilization"]["actual"] == 0.75  # OPS-03: 6 productive of 8 available
    assert v3["fpy"]["actual"] == 0.8  # OPS-04: 80 of 100
    assert v3["exception_rate"]["actual"] == 0.28  # OPS-05: 28 of 100
    assert v3["throughput"]["plan"] is not None and v3["throughput"]["variance_pct"] is not None  # OPS-02
    assert cid


def test_OPS_06_to_10_cost_overrun_drives_one_critical_alert_that_does_not_duplicate(client, world):
    c = schedule_and_receive(client, world, "OPS6", units=100, day="2026-06-10")
    plan = client.get(f"/api/cohorts/{c['id']}/variance").json()["reforecast"]["plan_cm"]
    before = client.get(f"/api/cohorts/{c['id']}/variance").json()["reforecast"]["economics"][
        "contribution_margin"
    ]
    for _ in range(2):  # OPS-09: same actuals submitted twice
        post_day(client, c["id"], world["cells"][1], "2026-06-12", devices_started=60, devices_completed=50, first_pass_completions=20,
                 manual_exception_count=35, quality_approved_count=50, productive_robot_hours=10, repair_parts_cost=30000, technician_hours=30)  # fmt: skip
    fc = client.get(f"/api/cohorts/{c['id']}/variance").json()["reforecast"]
    assert (
        fc["economics"]["contribution_margin"] < before and fc["delta_cm"] < 0 and fc["plan_cm"] == plan
    )  # OPS-06
    assert variance(client, c["id"])["repair_cost"]["status"] == "off_track"
    for _ in range(3):  # refreshing never spawns duplicates
        alerts = client.get("/api/alerts").json()
    crit = [
        a
        for a in alerts
        if a["cohort_id"] == c["id"]
        and a["alert_type"] in ("CRITICAL_MARGIN_INTERVENTION", "STOP_LOSS_MAKING")
    ]
    assert len(crit) == 1 and crit[0]["severity"] == "critical"  # OPS-08
    done = client.patch(
        f"/api/alerts/{crit[0]['id']}",
        json=dict(action="resolve", text="Root cause: bad battery lot; partner credited", user="ops.lead"),
    )
    assert done.json()["status"] == "resolved" and done.json()["comments"][-1]["text"].startswith(
        "Root cause"
    )  # OPS-10
    assert client.get("/api/alerts?status=resolved").json()


def test_OPS_10_alert_actions_are_audit_logged(client, world, session_factory):
    c = schedule_and_receive(client, world, "OPS10", units=100, day="2026-06-10")
    post_day(client, c["id"], world["cells"][1], "2026-06-12", devices_started=60, devices_completed=50, first_pass_completions=20,
             manual_exception_count=35, quality_approved_count=50, productive_robot_hours=10, repair_parts_cost=30000)  # fmt: skip
    a = next(
        x
        for x in client.get("/api/alerts").json()
        if x["cohort_id"] == c["id"] and x["severity"] == "critical"
    )
    client.patch(f"/api/alerts/{a['id']}", json=dict(action="resolve", text="handled", user="maya"))
    from sqlalchemy import select

    from app.models import AuditLog

    with session_factory() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.entity_id == a["id"])).one()
    assert (
        entry.user_id == "maya"
        and entry.old_value["status"] == "open"
        and entry.new_value["status"] == "resolved"
    )


def test_OPS_11_low_utilization_fires_on_the_fifth_consecutive_day_only(client, world):
    c = schedule_and_receive(client, world, "OPS11", units=100, day="2026-06-03")
    days = ["2026-06-09", "2026-06-10", "2026-06-11", "2026-06-12", "2026-06-15"]
    for i, day in enumerate(days, start=1):
        post_day(client, c["id"], world["cells"][1], day, devices_started=10, devices_completed=10, first_pass_completions=9, manual_exception_count=1,
                 quality_approved_count=10, productive_robot_hours=5)  # 5/12 = 42% < 65%  # fmt: skip
        low = [a for a in client.get("/api/alerts").json() if a["alert_type"] == "LOW_UTILIZATION"]
        assert (len(low) == 1) == (i == 5), (i, low)


def test_OPS_12_correction_recalculates_and_keeps_both_versions(client, world, session_factory):
    c = schedule_and_receive(client, world, "OPS12", units=100, day="2026-06-10")
    post_day(
        client,
        c["id"],
        world["cells"][1],
        "2026-06-12",
        devices_started=40,
        devices_completed=40,
        first_pass_completions=40,
        manual_exception_count=4,
    )
    assert abs(variance(client, c["id"])["exception_rate"]["actual"] - (4 / 140)) < 1e-9
    post_day(
        client,
        c["id"],
        world["cells"][1],
        "2026-06-12",
        devices_started=40,
        devices_completed=40,
        first_pass_completions=40,
        manual_exception_count=14,
    )
    assert abs(variance(client, c["id"])["exception_rate"]["actual"] - (14 / 140)) < 1e-9  # recalculated
    assert (
        len(client.get(f"/api/operations/daily?cohort_id={c['id']}").json()) == 2
    )  # corrected in place, not duplicated
    from sqlalchemy import select

    from app.models import AuditLog

    with session_factory() as s:
        e = s.scalars(select(AuditLog).where(AuditLog.action == "corrected")).one()
    assert (
        e.old_value["manual_exception_count"] == "4" and e.new_value["manual_exception_count"] == "14"
    )  # both versions retained


def test_ops_rejects_impossible_daily_records(client, world):
    c = schedule_and_receive(client, world, "OPSX", units=10)
    assert client.post("/api/operations/daily", json=dict(cohort_id=c["id"], robot_cell_id=world["cells"][0], operation_date="2026-06-12", devices_started=5,
                                                          devices_completed=5, first_pass_completions=6)).status_code == 422  # fmt: skip
