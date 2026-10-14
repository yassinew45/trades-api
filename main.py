"""A small FastAPI service for ingesting trades and reporting exposure by counterparty."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

app = FastAPI(title="Trades API", version="0.1.0")


class Trade(BaseModel):
    trade_id: str = Field(min_length=1)
    counterparty: str = Field(min_length=1)
    trader: str = Field(min_length=1)
    trade_date: date
    notional: Decimal = Field(gt=0)


class Exposure(BaseModel):
    counterparty: str
    total_notional: Decimal
    trade_count: int


# In-memory store keyed by trade_id. A real service would use a database.
trades: dict[str, Trade] = {}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/trades", status_code=status.HTTP_201_CREATED)
def create_trade(trade: Trade) -> Trade:
    # Idempotency guard: the same trade can't be booked twice.
    if trade.trade_id in trades:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Trade {trade.trade_id} already exists",
        )
    trades[trade.trade_id] = trade
    return trade


@app.get("/trades/{trade_id}")
def get_trade(trade_id: str) -> Trade:
    trade = trades.get(trade_id)
    if trade is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trade not found")
    return trade


@app.get("/trades")
def list_trades(counterparty: str | None = None) -> list[Trade]:
    if counterparty is None:
        return list(trades.values())
    return [t for t in trades.values() if t.counterparty == counterparty]


@app.get("/exposure")
def exposure(min_notional: Decimal = Decimal("0")) -> list[Exposure]:
    """Total notional per counterparty, largest first (like GROUP BY ... HAVING ... ORDER BY)."""
    totals: dict[str, Decimal] = defaultdict(Decimal)
    counts: dict[str, int] = defaultdict(int)
    for t in trades.values():
        totals[t.counterparty] += t.notional
        counts[t.counterparty] += 1

    result = [
        Exposure(counterparty=cp, total_notional=total, trade_count=counts[cp])
        for cp, total in totals.items()
        if total > min_notional
    ]
    return sorted(result, key=lambda e: e.total_notional, reverse=True)
