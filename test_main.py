# pytest discovers tests automatically: files named test_*.py, and functions
# inside them whose names start with test_. No class or registration needed.

import pytest
# Needed here for the decorators @pytest.fixture and @pytest.mark.parametrize.

from fastapi.testclient import TestClient
# TestClient calls the app directly in-process, without starting a real server.
# It's built on the httpx library, which is why httpx is in requirements.txt.
# Similar to MockMvc in Spring tests.

from main import app, trades
# Import the app to test, plus the in-memory store so tests can reset it.

client = TestClient(app)
# One client shared by all tests. client.get(...) and client.post(...) return
# responses with .status_code and .json(), like a real HTTP client.


@pytest.fixture(autouse=True)
# A fixture is reusable setup/teardown code. autouse=True means it runs around
# every test in this file automatically, without being requested by name.
def clear_store():
    trades.clear()
    # Setup: start each test with an empty store, so tests don't affect each other.
    yield
    # The test runs at this point. Code before yield is setup; code after is teardown.
    # Like @BeforeEach and @AfterEach in JUnit, combined in one function.
    trades.clear()
    # Teardown: clean up after the test.


def make_trade(trade_id="T1", counterparty="ACME", notional="1000000"):
    # A helper (not a test, since its name doesn't start with test_).
    # Default arguments let each test override only the fields it cares about,
    # e.g. make_trade("T2", "GLOBEX").
    return {
        "trade_id": trade_id,
        "counterparty": counterparty,
        "trader": "alice",
        "trade_date": "2026-10-01",
        "notional": notional,
    }
    # A plain dict; passing it as json=... sends it as the request body.


def test_health():
    assert client.get("/health").json() == {"status": "ok"}
    # pytest uses the plain assert statement. On failure it shows both sides of
    # the comparison, so no assertEquals-style methods are needed.


def test_create_and_get_trade():
    assert client.post("/trades", json=make_trade()).status_code == 201
    # Create a trade and check we got 201 Created.
    response = client.get("/trades/T1")
    # Then read it back by ID.
    assert response.status_code == 200
    assert response.json()["counterparty"] == "ACME"
    # .json() parses the response body into a Python dict.


def test_duplicate_trade_rejected():
    client.post("/trades", json=make_trade())
    assert client.post("/trades", json=make_trade()).status_code == 409
    # Posting the same trade_id twice must fail with 409 Conflict.
    # This tests the idempotency guard in create_trade.


def test_missing_trade_returns_404():
    assert client.get("/trades/NOPE").status_code == 404
    # The unhappy path matters too: an unknown ID gives 404 Not Found.


@pytest.mark.parametrize(
    "field, bad_value",
    # Names of the arguments the test function receives.
    [("notional", "-5"), ("notional", "abc"), ("trade_date", "not-a-date"), ("counterparty", "")],
    # Each tuple becomes a separate test run, so this one function is 4 tests.
    # Adding a new case means adding one tuple. Like @ParameterizedTest in JUnit.
)
def test_invalid_trade_rejected(field, bad_value):
    payload = make_trade()
    payload[field] = bad_value
    # Start from a valid trade and break exactly one field.
    assert client.post("/trades", json=payload).status_code == 422
    # 422 Unprocessable Entity: FastAPI's automatic response when Pydantic
    # validation fails. Our endpoint code never even runs.


def test_exposure_groups_filters_and_sorts():
    client.post("/trades", json=make_trade("T1", "ACME", "6000000"))
    client.post("/trades", json=make_trade("T2", "ACME", "5000000"))
    client.post("/trades", json=make_trade("T3", "GLOBEX", "2000000"))
    # Arrange: ACME totals 11M across 2 trades; GLOBEX totals 2M.

    response = client.get("/exposure", params={"min_notional": "10000000"})
    # params=... builds the query string: /exposure?min_notional=10000000
    body = response.json()
    assert len(body) == 1
    # Only ACME is above 10M, so GLOBEX is filtered out (the HAVING part).
    assert body[0]["counterparty"] == "ACME"
    assert body[0]["trade_count"] == 2
    # Both ACME trades were grouped together (the GROUP BY part).


# --- CSV upload ---

HEADER = "trade_id,counterparty,trader,trade_date,notional\n"


def upload(content, filename="trades.csv", content_type="text/csv"):
    if isinstance(content, str):
        content = content.encode("utf-8")
    return client.post("/trades/upload", files={"file": (filename, content, content_type)})


def test_upload_valid_csv_creates_trades():
    csv_text = HEADER + "T1,ACME,alice,2026-10-01,1000\nT2,GLOBEX,bob,2026-10-02,2000\n"
    response = upload(csv_text)
    assert response.status_code == 200
    assert response.json() == {"created": 2, "failed": 0, "errors": []}
    assert client.get("/trades/T2").json()["counterparty"] == "GLOBEX"


def test_upload_reports_bad_rows_and_keeps_good_ones():
    csv_text = (
        HEADER
        + "T1,ACME,alice,2026-10-01,1000\n"     # line 2: valid
        + "T2,ACME,alice,2026-10-01,-5\n"       # line 3: negative notional
        + "T3,ACME,alice,not-a-date,1000\n"     # line 4: bad date
        + "T1,ACME,alice,2026-10-01,1000\n"     # line 5: duplicate of line 2
    )
    body = upload(csv_text).json()
    assert body["created"] == 1
    assert body["failed"] == 3
    assert [e["line"] for e in body["errors"]] == [3, 4, 5]
    assert "notional" in body["errors"][0]["error"]
    assert body["errors"][2]["error"] == "Duplicate trade_id"


def test_upload_rejects_non_csv_extension():
    assert upload(HEADER, filename="trades.txt").status_code == 415


def test_upload_rejects_wrong_content_type():
    assert upload(HEADER, content_type="image/png").status_code == 415


def test_upload_rejects_file_over_size_limit():
    big = HEADER + "x" * 1_000_001
    assert upload(big).status_code == 413


def test_upload_rejects_missing_columns():
    response = upload("trade_id,counterparty\nT1,ACME\n")
    assert response.status_code == 400
    assert "notional" in response.json()["detail"]


def test_upload_rejects_non_utf8_file():
    assert upload(b"\xff\xfe\x00bad").status_code == 400


def test_sample_file_uploads_as_expected():
    with open("sample_trades.csv", "rb") as f:
        body = upload(f.read()).json()
    assert body["created"] == 4
    assert body["failed"] == 4
