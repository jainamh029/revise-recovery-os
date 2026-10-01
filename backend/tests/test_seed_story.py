"""The demo dataset must tell the intended story -- guards against silent regressions in the narrative."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import get_db
from app.main import app
from app.seed import build


@pytest.fixture()
def seeded(session_factory, monkeypatch):
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


def test_four_contrasting_cohorts(seeded):
    """Formula-driven outcomes (CM% on net recognized revenue; accept >=30%, decline <15%, downside floor 15%)."""
    rows = {r["code"]: r for r in seeded.get("/api/cohorts").json()}
    assert (
        rows["EDU-001"]["recommendation"] == "accept"
        and rows["EDU-001"]["actual_cm"] > rows["EDU-001"]["plan_cm"]
    )
    # ITAD-002: base CM% clears the accept line, but the downside case (<15%) sends it to Review; then it degrades live.
    assert rows["ITAD-002"]["recommendation"] == "review"
    assert rows["ITAD-002"]["base_cm_pct"] >= 0.30
    assert rows["ITAD-002"]["forecast_cm_pct"] < 0.15 < rows["ITAD-002"]["plan_cm_pct"]
    assert rows["ENT-003"]["recommendation"] == "review"  # working-capital headroom, not margin
    assert rows["ENT-003"]["base_cm_pct"] >= 0.30
    assert rows["REC-004"]["status"] == "declined" and rows["REC-004"]["recommendation"] == "decline"
    assert rows["EDU-005"]["status"] == "under_review" and rows["EDU-005"]["recommendation"] is not None


def test_todays_decisions(seeded):
    rows = {r["code"]: r for r in seeded.get("/api/cohorts").json()}
    edu5 = seeded.get(f"/api/cohorts/{rows['EDU-005']['id']}/underwriting").json()
    assert edu5["decision"] == "accept" and edu5["scenarios"]["downside"]["contribution_margin"] > 0
    itad6 = seeded.get(f"/api/cohorts/{rows['ITAD-006']['id']}/underwriting").json()
    assert itad6["decision"] == "review"
    assert any(c["key"] == "robot_capacity" and c["status"] == "fail" for c in itad6["checks"])


def test_active_issues_surface_as_alerts(seeded):
    types = {a["alert_type"] for a in seeded.get("/api/alerts").json()}
    assert {
        "CRITICAL_MARGIN_INTERVENTION",
        "EXCEPTION_RATE_ABOVE_PLAN",
        "LOW_UTILIZATION",
        "OVERDUE_COLLECTION",
        "AGED_INVENTORY",
        "SANITIZATION_FAILURE",
    } <= types


def test_dashboard_and_partner_ranking(seeded):
    d = seeded.get("/api/dashboard/executive").json()
    assert d["active_cohorts"] == 3 and d["pending_review"] == 2
    assert d["overdue_collections"] > 100000 and d["critical_alerts"][0]["severity"] == "critical"
    scores = {p["name"]: p["score"] for p in d["partners"] if p["score"] is not None}
    assert scores["Cascadia State University"] > scores["Meridian ITAD Services"]
    ent = next(c for c in d["cells"] if c["name"] == "Cell B")
    assert ent["utilization"] == 0  # idle capacity is visible
