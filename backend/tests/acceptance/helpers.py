from tests.conftest import approved_cohort, assumptions, line  # noqa: F401


def controlled(**over):
    """The hand-calculable cohort from the acceptance plan (Test set 2)."""
    ln = line(units=100, process_minutes=24, exception_rate=0.2, first_pass_yield=0.8, repair_cost=50, resale_price=300, sell_through=0.75,
              model_name="Controlled laptop")  # fmt: skip
    a = assumptions(lines=[ln], acquisition_cost_per_unit=50, inbound_logistics_per_unit=5, outbound_shipping_per_unit=20,
                    marketplace_fee_rate=0.10, return_rate=0, repair_recovery_rate=0, planned_uptime=1, robot_cost_per_hour=50,
                    labor_rate_per_hour=60, manual_minutes_per_exception=60)  # fmt: skip
    a.update(over)
    return a


def preview(client, world, **over):
    r = client.post(
        "/api/underwrite/preview",
        json=dict(
            partner_id=world["partner"]["id"],
            contract_id=world["contract"]["id"],
            assumptions=controlled(**over),
        ),
    )
    assert r.status_code == 200, r.text
    return r.json()


def advance_cohort(client, cid, *statuses):
    for s in statuses:
        r = client.post(f"/api/cohorts/{cid}/status", json=dict(status=s))
        assert r.status_code == 200, (s, r.text)


def schedule_and_receive(client, world, code="T-DEV", units=10, day="2026-06-11"):
    """Approved + scheduled cohort with `units` devices received in operations (so device counts can reconcile)."""
    c = approved_cohort(client, world, code=code, units=units)
    r = client.post(
        f"/api/cohorts/{c['id']}/assignments",
        json=dict(robot_cell_id=world["cells"][0], start_date="2026-06-10", end_date="2026-06-30"),
    )
    assert r.status_code == 201, r.text
    r = client.post("/api/operations/daily", json=dict(cohort_id=c["id"], robot_cell_id=world["cells"][0], operation_date=day, devices_received=units,
                                                        devices_started=units, devices_completed=units, first_pass_completions=units,
                                                        quality_approved_count=units, productive_robot_hours=units * 0.5))  # fmt: skip
    assert r.status_code == 201, r.text
    return c


def register(client, cohort_id, n=1, prefix="SN"):
    ids = []
    for i in range(n):
        r = client.post(
            "/api/devices",
            json=dict(
                cohort_id=cohort_id,
                serial_number=f"{prefix}{i:04d}",
                manufacturer="Dell",
                model="Latitude 5490",
            ),
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    return ids


def to_ready(client, dev_id, cert="CERT-1"):
    for to in ("TRIAGED", "AWAITING_SANITIZATION"):
        assert client.post(f"/api/devices/{dev_id}/advance", json=dict(to=to)).status_code == 200
    assert (
        client.post(
            f"/api/devices/{dev_id}/sanitization", json=dict(passed=True, certificate_id=cert)
        ).status_code
        == 200
    )
    for to in ("IN_ROBOT_PROCESSING", "QUALITY_CONTROL"):
        assert client.post(f"/api/devices/{dev_id}/advance", json=dict(to=to)).status_code == 200
    assert client.post(f"/api/devices/{dev_id}/qc", json=dict(passed=True)).status_code == 200
    assert (
        client.post(f"/api/devices/{dev_id}/disposition", json=dict(disposition="RESALE")).status_code == 200
    )
