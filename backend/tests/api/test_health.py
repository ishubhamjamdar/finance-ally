"""Tests for GET /api/health."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def test_returns_200_when_healthy(client):
    """Checkpoint 2 exit criterion."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["database"] == "ok"


def test_reports_no_source_before_startup(client):
    market = client.get("/api/health").json()["market_data"]
    assert market == {"source": None, "tickers": 0, "market_status": None}


def test_creates_the_database_on_first_request(client, temp_db):
    """PLAN.md §7 lazy init, reached over HTTP: a request against a missing
    file rebuilds it. Run twice, because an initialisation flag cached in the
    module would let the first pass succeed and the second serve `no such
    table`. (`tests/db` proves the same property at the database layer; this
    one proves an ordinary request is enough to trigger it.)
    """
    for _ in range(2):
        temp_db.delete()
        assert not temp_db.exists()

        assert client.get("/api/health").status_code == 200
        assert temp_db.exists()


def test_reports_503_when_the_database_is_unusable(client, monkeypatch):
    """A process that answers 200 while its database is broken is how a
    container stays in service handing out `no such table`."""

    def broken_connect():
        raise RuntimeError("disk I/O error")

    monkeypatch.setattr("app.api.health.connect", broken_connect)

    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "database": "error",
        "market_data": {"source": None, "tickers": 0, "market_status": None},
    }


def test_reports_the_running_source_and_ticker_count():
    # As a context manager, TestClient runs the lifespan — so this is the
    # health report a real deployment serves, not one against a bare app.
    with TestClient(create_app()) as client:
        market = client.get("/api/health").json()["market_data"]

    assert market["source"] == "SimulatorDataSource"
    assert market["tickers"] == 10
    assert market["market_status"] is None
