"""A small FastAPI service for ingesting trades and reporting exposure by counterparty."""
# ^ A module docstring: the first string in a file documents the whole module.
#   Tools like help() and IDEs show it. Like a Javadoc comment at the top of a class.

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
# Standard library first, third-party second, blank line between (PEP 8 style).

import csv
import io
# csv and io to parse the file;

from collections import defaultdict
# defaultdict: a dict that creates a default value for a missing key instead of
# raising KeyError. defaultdict(int)[k] starts at 0, defaultdict(list)[k] at [].

from datetime import date
# date: a calendar date with no time. Pydantic will parse "2026-10-01" into it.

from decimal import Decimal
# Decimal: exact base-10 numbers. Floats are binary, so 0.1 + 0.2 != 0.3.
# For money you want exact arithmetic, so we use Decimal (like BigDecimal in Java).

from typing import List, Optional
# Optional[str] means "a str or None". On Python 3.10+ you can write str | None.
# List for 3.9-compatible type hints

from fastapi import FastAPI, HTTPException, status, File, UploadFile
# FastAPI: the application object (roughly the Spring Boot application context).
# HTTPException: raise it to return an HTTP error response with a status code.
# status: named constants like status.HTTP_404_NOT_FOUND, clearer than bare 404.
# File and UploadFile to receive the upload

from pydantic import BaseModel, Field, ValidationError
# Pydantic does data validation. Subclass BaseModel to describe the shape of data.
# Field(...) adds constraints such as minimum length or "greater than zero".
# ValidationError to catch bad rows.



app = FastAPI(title="Trades API", version="0.2.0")
# Create the app. title and version show up in the auto-generated docs at /docs.
# Uvicorn finds this object when you run `uvicorn main:app` (module main, object app).

MAX_UPLOAD_BYTES = 1_000_000  # 1 MB # The underscores in 1_000_000 are only for readability; Python ignores them.
ALLOWED_CONTENT_TYPES = {"text/csv", "application/vnd.ms-excel", "text/plain"}
REQUIRED_COLUMNS = {"trade_id", "counterparty", "trader", "trade_date", "notional"}

# ---------------------------------------------------------------------------
# Models: the shape of the data coming in and going out
# ---------------------------------------------------------------------------

class Trade(BaseModel):
    # A Pydantic model. Each annotated attribute is a field. When FastAPI receives
    # a JSON body, it builds a Trade from it and validates every field. If anything
    # is wrong, FastAPI automatically returns 422 Unprocessable Entity with details,
    # and our endpoint code never runs. Similar to @Valid on a @RequestBody DTO.

    trade_id: str = Field(min_length=1)
    # Must be a string with at least 1 character, so "" is rejected.

    counterparty: str = Field(min_length=1)
    # Who the trade is with. Same rule: required and non-empty.

    trader: str = Field(min_length=1)
    # Who booked the trade.

    trade_date: date
    # Must be a valid date. "2026-10-01" is accepted; "not-a-date" gives a 422.

    notional: Decimal = Field(gt=0)
    # The trade amount. gt=0 means "greater than 0", so negative or zero is rejected.
    # Pydantic accepts a number or a numeric string like "1000000" and converts it.


class Exposure(BaseModel):
    # The response shape for the exposure report: one row per counterparty.
    # Using a model for responses documents the output in /docs and controls
    # exactly which fields are returned.
    counterparty: str
    total_notional: Decimal
    # Note: in JSON, Pydantic v2 sends Decimal values as strings, e.g. "11000000",
    # so no precision is lost in transit.
    trade_count: int


class RowError(BaseModel):
    # RowError describes one bad row.
    line: int
    trade_id: Optional[str] = None
    error: str


class UploadResult(BaseModel):
    # UploadResult is the endpoint’s response.
    created: int
    failed: int
    errors: List[RowError]

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

# In-memory store keyed by trade_id. A real service would use a database.
trades: dict[str, Trade] = {}
# A module-level dict acting as our "database". Keying by trade_id gives O(1)
# lookup by ID and makes duplicate detection trivial.
# Limitations worth mentioning in an interview:
#   - Data is lost when the server restarts.
#   - With several worker processes, each one has its own separate copy.
#   - A real service would use a database with a unique constraint on trade_id.


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
# A decorator: it registers the function below as the handler for GET /health.
# Like @GetMapping("/health") in Spring.
def health() -> dict[str, str]:
    # The return type annotation tells FastAPI what the response looks like.
    # Returning a dict is automatically serialized to JSON.
    return {"status": "ok"}
    # Health checks let load balancers and monitoring confirm the service is up.


@app.post("/trades", status_code=status.HTTP_201_CREATED)
# POST creates something. status_code=201 ("Created") is the conventional success
# code for creation, instead of the default 200.
def create_trade(trade: Trade) -> Trade:
    # Because the parameter's type is a Pydantic model, FastAPI reads it from the
    # JSON request body and validates it before this function is called.
    # -> Trade: the response is the created trade, serialized back to JSON.

    # Idempotency guard: the same trade can't be booked twice.
    if trade.trade_id in trades:
        # `in` on a dict checks keys, and it's O(1) on average (a hash lookup).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            # 409 Conflict: the request clashes with the current state (a duplicate).
            detail=f"Trade {trade.trade_id} already exists",
            # An f-string: values in {braces} are inserted into the string.
        )
        # raise stops the function here. FastAPI turns this into a JSON error:
        # {"detail": "Trade T1 already exists"}.
    trades[trade.trade_id] = trade
    # Store the trade under its ID.
    return trade
    
    
@app.post("/trades/upload")
async def upload_trades(file: UploadFile = File(...)) -> UploadResult:
    """Upload a CSV of trades. Valid rows are booked; invalid rows are reported."""
    # 1. File-level checks: reject the whole file if any of these fail.
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "File must be a .csv")
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported content type: {file.content_type}"
        )

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        # 413 Content Too Large (written as a number: the constant's name differs by version)
        raise HTTPException(413, "File is larger than 1 MB")

    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File must be UTF-8 encoded text")

    reader = csv.DictReader(io.StringIO(text))
    missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Missing columns: {', '.join(sorted(missing))}"
        )

    # 2. Row-level checks: book each valid row, collect an error for each bad one.
    created = 0
    errors: List[RowError] = []
    for line_no, row in enumerate(reader, start=2):  # line 1 is the header
        trade_id = (row.get("trade_id") or "").strip() or None
        try:
            trade = Trade.model_validate(row)
        except ValidationError as exc:
            message = "; ".join(
                f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            errors.append(RowError(line=line_no, trade_id=trade_id, error=message))
            continue

        if trade.trade_id in trades:
            errors.append(RowError(line=line_no, trade_id=trade_id, error="Duplicate trade_id"))
            continue

        trades[trade.trade_id] = trade
        created += 1

    return UploadResult(created=created, failed=len(errors), errors=errors)
    # async def and await file.read(...): reading an upload is I/O, so the function is async. FastAPI supports both def and async def endpoints.
    # file.read(MAX_UPLOAD_BYTES + 1): reads at most one byte past the limit. If that extra byte arrives, the file is too big, and you never load a huge file into memory.
    # "utf-8-sig": decodes UTF-8 and also strips the invisible marker Excel puts at the start of CSVs it saves.
    # csv.DictReader: turns each row into a dict keyed by the header, for example {"trade_id": "T100", ...}. That’s exactly the shape Pydantic wants.
    # REQUIRED_COLUMNS - set(...): set difference, meaning the required columns that aren’t in the header.
    # Trade.model_validate(row): runs the same validation rules as POST /trades, so the rules live in one place.
    # continue: skips to the next row, so one bad row doesn’t stop the rest.
    # The duplicate check: it uses the store, so it catches trades already booked and repeats within the same file. Each valid row is stored before the next row is checked.


@app.get("/trades/{trade_id}")
# {trade_id} is a path parameter. A request to /trades/T1 sets trade_id = "T1".
# Like @PathVariable in Spring.
def get_trade(trade_id: str) -> Trade:
    # The parameter name matches the {trade_id} placeholder, which is how FastAPI
    # knows to read it from the path.
    trade = trades.get(trade_id)
    # dict.get returns None for a missing key instead of raising KeyError.
    if trade is None:
        # `is None` is the idiomatic way to check for None (not `== None`).
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found")
    return trade


@app.get("/trades")
def list_trades(counterparty: Optional[str] = None) -> list[Trade]:
    # counterparty isn't in the path, so FastAPI treats it as a query parameter:
    # /trades?counterparty=ACME. The default None makes it optional.
    # Like @RequestParam(required = false) in Spring.
    if counterparty is None:
        return list(trades.values())
        # .values() is a view of the dict's values; list() turns it into a list.
    return [t for t in trades.values() if t.counterparty == counterparty]
    # A list comprehension: build a list of every t that passes the filter.
    # Equivalent to a for loop with an if and .append(). It's O(n) over all trades.


@app.get("/exposure")
def exposure(min_notional: Decimal = Decimal("0")) -> list[Exposure]:
    # Query parameter with a default: /exposure or /exposure?min_notional=10000000.
    # FastAPI converts the query string text into a Decimal for us.
    # Decimal("0") is built from a string; building Decimals from floats can carry
    # binary rounding errors, so strings are the safe habit.
    """Total notional per counterparty, largest first (like GROUP BY ... HAVING ... ORDER BY)."""
    # A function docstring. FastAPI also shows it as the endpoint description in /docs.

    totals: dict[str, Decimal] = defaultdict(Decimal)
    # Decimal() with no argument is Decimal("0"), so each new counterparty
    # starts at 0 and we can add to it without checking whether the key exists.
    counts: dict[str, int] = defaultdict(int)
    # int() is 0, so counts also start at 0.

    for t in trades.values():
        # One pass over all trades: O(n) time.
        totals[t.counterparty] += t.notional
        # SQL equivalent: SUM(notional) ... GROUP BY counterparty
        counts[t.counterparty] += 1
        # SQL equivalent: COUNT(*) ... GROUP BY counterparty

    result = [
        Exposure(counterparty=cp, total_notional=total, trade_count=counts[cp])
        # Build one response object per counterparty, using keyword arguments.
        for cp, total in totals.items()
        # .items() yields (key, value) pairs; we unpack each into cp and total.
        if total > min_notional
        # SQL equivalent: HAVING SUM(notional) > :min_notional
    ]
    return sorted(result, key=lambda e: e.total_notional, reverse=True)
    # sorted() returns a new sorted list (it doesn't modify `result`).
    # key=lambda e: e.total_notional sorts by that field. A lambda is a small
    # anonymous function, like a Java lambda (e -> e.getTotalNotional()).
    # reverse=True gives largest first. SQL equivalent: ORDER BY total DESC.
    # Sorting costs O(k log k), where k is the number of counterparties.
