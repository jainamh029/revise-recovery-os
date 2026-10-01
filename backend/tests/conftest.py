import os
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import get_db
from app.main import app
from app.models import Base, RobotCell

FIXED_TODAY = "2026-06-15"  # a Monday


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(settings, "demo_today", FIXED_TODAY)


@pytest.fixture()
def session_factory(tmp_path):
    """SQLite file per test by default; set TEST_DATABASE_URL to run the identical suite on PostgreSQL."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        eng = create_engine(url)
        Base.metadata.drop_all(eng)
    else:
        eng = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    eng.dispose()
    if not url:  # keep the disk footprint flat: a SQLite file per test is deleted as soon as the test ends
        for f in tmp_path.glob("t.db*"):
            f.unlink(missing_ok=True)


@pytest.fixture()
def db(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture()
def client(session_factory):
    def _get():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get
    yield TestClient(app)
    app.dependency_overrides.clear()


def line(**kw):
    base = dict(
        model_name="Dell Latitude 5490",
        units=100,
        condition_grade="B",
        process_minutes=30,
        exception_rate=0.10,
        first_pass_yield=0.88,
        repair_cost=55,
        resale_price=300,
        sell_through=0.92,
    )
    base.update(kw)
    return base


def assumptions(**kw):
    lines = kw.pop("lines", [line()])
    a = dict(
        lines=lines,
        acquisition_cost_per_unit=60,
        inbound_logistics_per_unit=3,
        outbound_shipping_per_unit=13,
        marketplace_fee_rate=0.12,
        return_rate=0.04,
        payout_days=7,
        days_to_sale=45,
    )
    a.update(kw)
    return a


@pytest.fixture()
def world(client, session_factory):
    """Partner + contract + 2 robot cells with a 3-month calendar."""
    with session_factory() as s:
        cells = []
        for nm in ("Cell A", "Cell B"):
            c = RobotCell(cell_name=nm, status="available", standard_daily_capacity_hours=Decimal(12))
            s.add(c)
            cells.append(c)
        s.commit()
        cell_ids = [c.id for c in cells]
    p = client.post(
        "/api/partners",
        json=dict(name="Test University", partner_type="university", data_security_requirement="standard"),
    ).json()
    ct = client.post(
        "/api/contracts",
        json=dict(
            partner_id=p["id"],
            contract_name="Buy-out",
            contract_type="supply_purchase",
            inventory_ownership_model="operator_owned",
            acquisition_cost_per_unit=60,
            payment_terms_days=14,
            status="active",
        ),
    ).json()
    for cid in cell_ids:
        r = client.post(
            "/api/capacity/calendar",
            json=dict(robot_cell_id=cid, start_date="2026-06-01", end_date="2026-08-31", scheduled_hours=12),
        )
        assert r.status_code == 201
    return dict(partner=p, contract=ct, cells=cell_ids)


def new_cohort(client, world, *, code="T-001", units=100, intake="2026-06-16", target="2026-07-15", **akw):
    lines = akw.pop("lines", [line(units=units)])
    r = client.post(
        "/api/cohorts",
        json=dict(
            partner_id=world["partner"]["id"],
            contract_id=world["contract"]["id"],
            cohort_code=code,
            cohort_name=f"Cohort {code}",
            intake_date=intake,
            target_completion_date=target,
            assumptions=assumptions(lines=lines, **akw),
        ),
    )
    assert r.status_code == 201, r.text
    return r.json()


def approved_cohort(client, world, **kw):
    c = new_cohort(client, world, **kw)
    assert client.post(f"/api/cohorts/{c['id']}/underwrite").status_code == 200
    r = client.post(f"/api/cohorts/{c['id']}/approve", json=dict(approver="cfo", rationale="meets policy"))
    assert r.status_code == 200, r.text
    return c


TODAY = date.fromisoformat(FIXED_TODAY)


@pytest.fixture()
def seeded(session_factory, monkeypatch):
    """The full illustrative dataset on a throwaway database, with `today` frozen to the documented demo date."""
    from app.seed import build

    monkeypatch.setattr(settings, "demo_today", "2026-09-30")
    with session_factory() as s:
        build(s)

    def _get():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get
    yield TestClient(app)
    app.dependency_overrides.clear()
