from tests.conftest import approved_cohort, assumptions, line, new_cohort


def test_end_to_end_underwrite_approve_assign(client, world):
    c = new_cohort(client, world)
    assert c["status"] == "draft"
    uw = client.post(f"/api/cohorts/{c['id']}/underwrite").json()
    assert uw["decision"] in ("accept", "accept_with_conditions") and uw["status"] == "under_review"
    assert set(uw["scenarios"]) == {"base", "downside", "upside"}
    r = client.post(f"/api/cohorts/{c['id']}/approve", json=dict(approver="cfo", rationale="ok"))
    assert r.status_code == 200 and r.json()["status"] == "approved"
    a = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][0], start_date="2026-06-16", end_date="2026-07-10"),
    )
    assert a.status_code == 201, a.text
    assert client.get(f"/api/cohorts/{c['id']}").json()["status"] == "scheduled"


def test_cannot_process_before_approval_and_capacity(client, world):
    c = new_cohort(client, world)
    r = client.post(f"/api/cohorts/{c['id']}/status", json=dict(status="in_processing"))
    assert r.status_code == 409 and r.json()["error"] == "bad_transition"
    c2 = approved_cohort(client, world, code="T-002")
    r = client.post(f"/api/cohorts/{c2['id']}/status", json=dict(status="in_processing"))
    assert r.status_code == 409  # approved -> in_processing is illegal (must be scheduled)


def test_overbooking_is_rejected(client, world):
    a = approved_cohort(client, world, code="T-A", units=400, target="2026-06-19")
    cell = world["cells"][0]
    # Cell A: 4 working days x 12h = 48h. ~280 scheduled hours needed -> reject
    r = client.post(
        f"/api/cohorts/{a['id']}/assignments",
        json=dict(robot_cell_id=cell, start_date="2026-06-16", end_date="2026-06-19", planned_hours=60),
    )
    assert r.status_code == 409 and r.json()["error"] == "overbooked"
    ok = client.post(
        f"/api/cohorts/{a['id']}/assignments",
        json=dict(robot_cell_id=cell, start_date="2026-06-16", end_date="2026-06-19", planned_hours=40),
    )
    assert ok.status_code == 201
    b = approved_cohort(client, world, code="T-B", units=50, target="2026-06-19")
    r = client.post(
        f"/api/cohorts/{b['id']}/assignments",
        json=dict(robot_cell_id=cell, start_date="2026-06-16", end_date="2026-06-19", planned_hours=20),
    )
    assert r.status_code == 409 and r.json()["error"] == "overbooked"  # only 8h left on Cell A


def test_assignment_rejected_on_blocked_cell_and_sla_breach(client, world):
    c = approved_cohort(client, world, code="T-C", units=50, target="2026-06-18")
    client.patch(f"/api/robot-cells/{world['cells'][1]}", json=dict(status="maintenance"))
    r = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][1], start_date="2026-06-16", end_date="2026-06-18"),
    )
    assert r.status_code == 409 and r.json()["error"] == "cell_blocked"
    r = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][0], start_date="2026-06-16", end_date="2026-06-30"),
    )
    assert r.status_code == 409 and r.json()["error"] == "sla_breach"


def test_approved_budget_is_immutable_and_reforecast_leaves_plan_intact(client, world):
    c = approved_cohort(client, world, code="T-D")
    before = client.get(f"/api/cohorts/{c['id']}/budget-versions").json()
    plan_cm = [v for v in before if v["scenario_type"] == "base" and v["is_approved"]][0][
        "expected_contribution_margin"
    ]
    r = client.post(
        f"/api/cohorts/{c['id']}/budget-versions", json=assumptions(lines=[line(units=100, resale_price=150)])
    )
    assert r.status_code == 201 and r.json()["scenario_type"] == "reforecast"
    after = client.get(f"/api/cohorts/{c['id']}/budget-versions").json()
    approved = [v for v in after if v["is_approved"] and v["scenario_type"] == "base"][0]
    assert approved["expected_contribution_margin"] == plan_cm
    assert any(v["scenario_type"] == "reforecast" and not v["is_approved"] for v in after)
    assert (
        client.post(f"/api/cohorts/{c['id']}/approve", json=dict(approver="x", rationale="again")).status_code
        == 409
    )
    assert client.patch(f"/api/cohorts/{c['id']}", json=dict(intake_date="2026-07-01")).status_code == 409


def test_decline_recommendation_needs_override(client, world):
    c = new_cohort(client, world, code="T-E", acquisition_cost_per_unit=500)
    assert client.post(f"/api/cohorts/{c['id']}/underwrite").json()["decision"] == "decline"
    r = client.post(f"/api/cohorts/{c['id']}/approve", json=dict(approver="ceo", rationale="strategic"))
    assert r.status_code == 409 and r.json()["error"] == "policy_decline"
    assert (
        client.post(f"/api/cohorts/{c['id']}/decline", json=dict(approver="ceo", rationale="policy")).json()[
            "status"
        ]
        == "declined"
    )
    audit = client.get(f"/api/cohorts/{c['id']}/audit").json()
    assert any(a["action"] == "declined" for a in audit)


def test_validation_rules(client, world):
    bad = assumptions(lines=[line(exception_rate=25)])  # 25 instead of 0.25
    r = client.post(
        "/api/cohorts", json=dict(partner_id=world["partner"]["id"], cohort_name="x", assumptions=bad)
    )
    assert r.status_code == 422
    r = client.post(
        "/api/contracts",
        json=dict(
            partner_id=world["partner"]["id"],
            contract_name="c",
            contract_type="supply_purchase",
            inventory_ownership_model="unknown",
            start_date="2026-06-10",
            end_date="2026-06-01",
        ),
    )
    assert r.status_code == 422
    r = client.post(
        "/api/cost-entries", json=dict(cost_date="2026-06-15", cost_category="other_direct_cost", amount=10)
    )
    assert r.status_code == 409 and r.json()["error"] == "unallocated_cost"


def _scheduled(client, world, code="T-F", units=100, cell=0):
    c = approved_cohort(client, world, code=code, units=units)
    r = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][cell], start_date="2026-06-10", end_date="2026-06-30"),
    )
    assert r.status_code == 201, r.text
    return c


def daily(client, world, cohort, day, **kw):
    body = dict(
        cohort_id=cohort["id"],
        robot_cell_id=world["cells"][0],
        operation_date=day,
        devices_received=0,
        devices_started=20,
        devices_completed=19,
        first_pass_completions=16,
        manual_exception_count=2,
        quality_approved_count=19,
        productive_robot_hours=10,
        downtime_hours=1,
        technician_hours=1.2,
        repair_parts_cost=110,
    )
    body.update(kw)
    return client.post("/api/operations/daily", json=body)


def test_daily_operations_metrics_and_cost_posting(client, world):
    c = _scheduled(client, world)
    r = daily(client, world, c, "2026-06-11", devices_received=100)
    assert r.status_code == 201, r.text
    daily(client, world, c, "2026-06-12")
    act = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    assert act["units"]["started"] == 40 and act["units"]["received"] == 100
    cats = act["costs_by_category"]
    assert cats["device_acquisition"] == 6000 and cats["inbound_logistics"] == 300  # posted on receipt
    assert cats["robot_operations"] == 2 * 11 * 42 and cats["repair_parts"] == 220
    var = {x["key"]: x for x in client.get(f"/api/cohorts/{c['id']}/variance").json()["rows"]}
    assert abs(var["exception_rate"]["actual"] - 0.10) < 1e-9  # 4 / 40
    assert abs(var["fpy"]["actual"] - 32 / 38) < 1e-9  # first-pass / completed
    assert abs(var["utilization"]["actual"] - 20 / 24) < 1e-9  # productive / scheduled (2 x 12h)
    # re-posting the same day replaces (idempotent), not duplicates, cost entries
    daily(client, world, c, "2026-06-12", repair_parts_cost=110)
    assert client.get(f"/api/cohorts/{c['id']}/actuals").json()["costs_by_category"]["repair_parts"] == 220
    assert daily(client, world, c, "2026-06-13", devices_started=-1).status_code == 422
    assert (
        daily(client, world, c, "2026-06-13", first_pass_completions=30, devices_completed=19).status_code
        == 422
    )


def test_exception_spike_triggers_margin_floor_breach_alert(client, world):
    c = _scheduled(client, world, units=100)
    daily(client, world, c, "2026-06-11", devices_received=100)
    # 60 devices through with 55% exceptions and very expensive repairs -> forecast margin collapses
    r = daily(
        client,
        world,
        c,
        "2026-06-12",
        devices_started=60,
        devices_completed=50,
        first_pass_completions=25,
        manual_exception_count=33,
        quality_approved_count=50,
        productive_robot_hours=12,
        repair_parts_cost=33 * 400,
        technician_hours=30,
    )
    assert r.status_code == 201, r.text
    alerts = client.get("/api/alerts").json()
    types = {a["alert_type"]: a for a in alerts}
    assert "CRITICAL_MARGIN_INTERVENTION" in types or "STOP_LOSS_MAKING" in types
    assert "EXCEPTION_RATE_ABOVE_PLAN" in types
    crit = [a for a in alerts if a["severity"] == "critical"][0]
    # a cohort with an unresolved critical alert cannot be closed
    assert crit["cohort_id"] == c["id"]
    fc = client.get(f"/api/cohorts/{c['id']}/variance").json()["reforecast"]
    assert fc["below_floor"] and fc["delta_cm"] < 0
    # the approved plan is untouched by actuals
    plan = [
        v
        for v in client.get(f"/api/cohorts/{c['id']}/budget-versions").json()
        if v["is_approved"] and v["scenario_type"] == "base"
    ][0]
    assert plan["expected_contribution_margin"] > 0


def test_alert_actions(client, world):
    c = _scheduled(client, world, units=100)
    daily(client, world, c, "2026-06-11", devices_received=100)
    daily(
        client,
        world,
        c,
        "2026-06-12",
        devices_started=60,
        devices_completed=50,
        first_pass_completions=25,
        manual_exception_count=33,
        quality_approved_count=50,
        productive_robot_hours=12,
        repair_parts_cost=33 * 400,
        technician_hours=30,
    )
    a = client.get("/api/alerts").json()[0]
    assert (
        client.patch(f"/api/alerts/{a['id']}", json=dict(action="assign", owner="ops.lead")).json()[
            "assigned_to"
        ]
        == "ops.lead"
    )
    assert (
        client.patch(f"/api/alerts/{a['id']}", json=dict(action="comment", text="looking")).json()[
            "comments"
        ][-1]["text"]
        == "looking"
    )
    assert (
        client.patch(f"/api/alerts/{a['id']}", json=dict(action="escalate")).json()["status"] == "escalated"
    )
    assert (
        client.patch(f"/api/alerts/{a['id']}", json=dict(action="snooze", snooze_days=2)).json()["status"]
        == "snoozed"
    )
    assert (
        client.patch(f"/api/alerts/{a['id']}", json=dict(action="resolve", text="fixed")).json()["status"]
        == "resolved"
    )


def _processed(client, world, code="T-G"):
    c = _scheduled(client, world, code=code, units=100)
    daily(
        client,
        world,
        c,
        "2026-06-11",
        devices_received=100,
        devices_started=50,
        devices_completed=50,
        first_pass_completions=46,
        manual_exception_count=4,
        quality_approved_count=50,
        productive_robot_hours=10,
    )
    client.post(f"/api/cohorts/{c['id']}/status", json=dict(status="quality_control"))
    return c


def test_resale_net_recovery_returns_and_cash(client, world):
    c = _processed(client, world)
    ls = client.post(
        "/api/resale-listings",
        json=dict(
            cohort_id=c["id"],
            listing_channel="eBay",
            listing_date="2026-06-12",
            listing_price=300,
            device_count=50,
        ),
    ).json()
    assert client.get(f"/api/cohorts/{c['id']}").json()["status"] == "listed_for_sale"
    s = client.post(
        "/api/sales-transactions",
        json=dict(
            cohort_id=c["id"],
            resale_listing_id=ls["id"],
            sale_date="2026-06-13",
            units_sold=10,
            gross_sale_amount=3000,
            marketplace_fee=360,
            shipping_cost=130,
        ),
    ).json()
    assert s["net_sale_amount"] == 2510 and s["payment_status"] == "pending"
    # net can never exceed gross / be negative
    assert (
        client.post(
            "/api/sales-transactions",
            json=dict(cohort_id=c["id"], sale_date="2026-06-13", gross_sale_amount=100, marketplace_fee=200),
        ).status_code
        == 422
    )
    act = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    assert act["cash_in"] == 0, "resale revenue is not cash until a payout is recorded"
    exposure_before = act["cash_exposure"]
    cm_before = act["contribution_margin"]
    # payout -> cash exposure falls by exactly the receipt
    r = client.post(
        "/api/cash-receipts", json=dict(sales_transaction_id=s["id"], receipt_date="2026-06-15", amount=2510)
    )
    assert r.status_code == 201
    act = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    assert act["cash_in"] == 2510 and abs(act["cash_exposure"] - (exposure_before - 2510)) < 1e-6
    assert (
        client.post(
            "/api/cash-receipts", json=dict(sales_transaction_id=s["id"], receipt_date="2026-06-15", amount=1)
        ).status_code
        == 409
    )  # overpayment
    # return reverses net recovery and margin immediately
    ret = client.post(
        f"/api/sales-transactions/{s['id']}/return", json=dict(units=2, refund_amount=600, return_cost=26)
    )
    assert ret.status_code == 200 and ret.json()["net_sale_amount"] == 2510 - 626
    act2 = client.get(f"/api/cohorts/{c['id']}/actuals").json()
    assert abs(act2["contribution_margin"] - (cm_before - 626)) < 1e-6
    # clawback adjustment recorded so cash stays consistent
    assert act2["cash_in"] == 1884


def test_invoice_overpayment_prevented_and_overdue_alert(client, world):
    c = _processed(client, world, code="T-H")
    inv = client.post(
        "/api/invoices",
        json=dict(
            cohort_id=c["id"],
            partner_id=world["partner"]["id"],
            invoice_number="INV-1",
            invoice_date="2026-04-01",
            due_date="2026-05-01",
            amount=1000,
        ),
    ).json()
    assert any(a["alert_type"] == "OVERDUE_COLLECTION" for a in client.get("/api/alerts").json())
    assert (
        client.post(
            "/api/cash-receipts", json=dict(invoice_id=inv["id"], receipt_date="2026-06-10", amount=1200)
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/cash-receipts", json=dict(invoice_id=inv["id"], receipt_date="2026-06-10", amount=400)
        ).status_code
        == 201
    )
    aging = client.get("/api/collections/aging").json()
    assert aging["total_overdue"] == 600
    assert (
        client.post(
            "/api/cash-receipts", json=dict(invoice_id=inv["id"], receipt_date="2026-06-11", amount=600)
        ).status_code
        == 201
    )
    assert client.get("/api/collections/aging").json()["total_overdue"] == 0
    assert not any(
        a["alert_type"] == "OVERDUE_COLLECTION" for a in client.get("/api/alerts").json()
    )  # self-resolves
    assert (
        client.post(
            "/api/invoices",
            json=dict(
                partner_id=world["partner"]["id"],
                invoice_number="INV-2",
                invoice_date="2026-06-02",
                due_date="2026-06-01",
                amount=10,
            ),
        ).status_code
        == 422
    )


def test_cannot_close_with_critical_alert(client, world):
    c = _scheduled(client, world, units=100, code="T-I")
    daily(client, world, c, "2026-06-11", devices_received=100)
    client.post(
        "/api/exceptions",
        json=dict(cohort_id=c["id"], exception_type="data_wipe_failure", severity="critical"),
    )
    alerts = client.get("/api/alerts").json()
    assert any(a["alert_type"] == "DATA_WIPE_EXCEPTION" and a["severity"] == "critical" for a in alerts)
    import app.main as m

    # force status to listed to test the close guard
    from app.db import get_db as _g  # noqa: F401
    from app.models import DeviceCohort

    sess = next(m.app.dependency_overrides[_g]())
    row = sess.get(DeviceCohort, c["id"])
    row.status = "partially_sold"
    sess.commit()
    r = client.post(f"/api/cohorts/{c['id']}/status", json=dict(status="closed"))
    assert r.status_code == 409 and r.json()["error"] == "critical_alerts_open"


def test_scenario_endpoint_and_memo(client, world):
    c = new_cohort(client, world, code="T-J")
    s = client.post(f"/api/cohorts/{c['id']}/scenario", json=dict(price_mult=0.8)).json()
    assert s["delta_cm"] < 0 and len(s["tornado"]) == 7
    memo = client.get(f"/api/cohorts/{c['id']}/memo").text
    assert "Underwriting memo" in memo and "Illustrative demo data only" in memo


def test_cohort_can_be_split_across_cells_and_cannot_be_over_assigned(client, world):
    c = approved_cohort(client, world, code="T-SPLIT", units=400, target="2026-07-10")
    a, b = world["cells"]
    need = client.get(f"/api/cohorts/{c['id']}/underwriting").json()["scenarios"]["base"][
        "scheduled_robot_hours"
    ]
    half = round(need / 2, 2)
    r1 = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=a, start_date="2026-06-16", end_date="2026-07-10", planned_hours=half),
    )
    assert r1.status_code == 201, r1.text
    # omitted hours => place only what is still unplaced (and units scale with hours)
    r2 = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=b, start_date="2026-06-16", end_date="2026-07-10"),
    )
    assert r2.status_code == 201, r2.text
    assert abs(float(r1.json()["planned_hours"]) + float(r2.json()["planned_hours"]) - need) < 0.02
    assert r1.json()["planned_units"] + r2.json()["planned_units"] in (399, 400, 401)
    r3 = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=a, start_date="2026-06-16", end_date="2026-07-10"),
    )
    assert r3.status_code == 409 and r3.json()["error"] == "fully_assigned"
