import pytest
from fastapi.testclient import TestClient

from main import app, trades

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_store():
    trades.clear()
    yield
    trades.clear()


def make_trade(trade_id="T1", counterparty="ACME", notional="1000000"):
    return {
        "trade_id": trade_id,
        "counterparty": counterparty,
        "trader": "alice",
        "trade_date": "2026-10-01",
        "notional": notional,
    }


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_get_trade():
    assert client.post("/trades", json=make_trade()).status_code == 201
    response = client.get("/trades/T1")
    assert response.status_code == 200
    assert response.json()["counterparty"] == "ACME"


def test_duplicate_trade_rejected():
    client.post("/trades", json=make_trade())
    assert client.post("/trades", json=make_trade()).status_code == 409


def test_missing_trade_returns_404():
    assert client.get("/trades/NOPE").status_code == 404


@pytest.mark.parametrize(
    "field, bad_value",
    [("notional", "-5"), ("notional", "abc"), ("trade_date", "not-a-date"), ("counterparty", "")],
)
def test_invalid_trade_rejected(field, bad_value):
    payload = make_trade()
    payload[field] = bad_value
    assert client.post("/trades", json=payload).status_code == 422


def test_exposure_groups_filters_and_sorts():
    client.post("/trades", json=make_trade("T1", "ACME", "6000000"))
    client.post("/trades", json=make_trade("T2", "ACME", "5000000"))
    client.post("/trades", json=make_trade("T3", "GLOBEX", "2000000"))

    response = client.get("/exposure", params={"min_notional": "10000000"})
    body = response.json()
    assert len(body) == 1
    assert body[0]["counterparty"] == "ACME"
    assert body[0]["trade_count"] == 2
