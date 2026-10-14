# Trades API

A small FastAPI practice service for ingesting trades and reporting exposure by counterparty.

## What it shows

- REST endpoints with FastAPI
- Request validation with Pydantic (positive notional, valid dates, required fields)
- Idempotency: the same `trade_id` can't be booked twice (409 Conflict)
- `Decimal` for money instead of floats
- An exposure report: total notional per counterparty, filtered and sorted
- Tests with pytest, including fixtures and `parametrize`

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| POST | `/trades` | Book a trade |
| POST | `/trades/upload` | Upload a CSV of trades; valid rows are booked, bad rows reported by line |
| GET | `/trades/{trade_id}` | Get one trade |
| GET | `/trades?counterparty=X` | List trades, optionally by counterparty |
| GET | `/exposure?min_notional=N` | Total notional per counterparty above N |

## Run it

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000/docs for interactive API docs.

## Test it

```bash
pytest -v
```

## Next steps

- Replace the in-memory store with a database
- Bulk CSV upload endpoint with per-row validation errors
- All-or-nothing upload mode (reject the file if any row fails)
