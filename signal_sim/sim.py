"""Fixture replay through the local paper ledger.

Completes the v0 path in docs/paper-trading-and-quant.md: the existing
rank_candidates signal emits long-only paper candidates; this module turns
each row into a proposal, sends it through submit_paper_order(), and marks
positions to fixture exit prices. Marks are research fixtures, not a market
data feed. No new ranking rule is introduced here.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .cli import load_fixture_events
from .events import Event
from .indicators import UNIVERSE, rank_candidates
from .paper import OrderRefused, submit_paper_order
from .store import EventStore


DEFAULT_MARKS = Path(__file__).resolve().parent.parent / "fixtures" / "marks" / "universe.json"
MAX_GROSS_FRAC = 1.0
_PNL_SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    starting_cash REAL NOT NULL,
    ending_equity REAL NOT NULL,
    total_pnl REAL NOT NULL,
    decision_at TEXT NOT NULL,
    exit_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    size_frac REAL NOT NULL,
    shares REAL NOT NULL,
    fill_px REAL NOT NULL,
    exit_px REAL NOT NULL,
    pnl REAL NOT NULL
);
"""


def _aware(value: str, field: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware ISO 8601 timestamp")
    return parsed


def _positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a positive finite number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return number


def load_mark_book(path: Path | None = None) -> dict[str, Any]:
    marks_path = DEFAULT_MARKS if path is None else path
    with marks_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("mark book must be an object")
    decision_at = _aware(raw.get("decision_at", ""), "decision_at")
    exit_at = _aware(raw.get("exit_at", ""), "exit_at")
    if exit_at <= decision_at:
        raise ValueError("exit_at must be after decision_at")
    starting_cash = _positive(raw.get("starting_cash"), "starting_cash")
    size_frac = _positive(raw.get("size_frac"), "size_frac")
    if size_frac > MAX_GROSS_FRAC:
        raise ValueError("size_frac must be at most 1")
    marks = raw.get("marks")
    if not isinstance(marks, dict) or not marks:
        raise ValueError("marks must be a non-empty object")
    parsed: dict[str, dict[str, float]] = {}
    for ticker, row in marks.items():
        if ticker not in UNIVERSE:
            raise ValueError(f"mark ticker not in universe: {ticker!r}")
        if not isinstance(row, dict):
            raise ValueError(f"marks.{ticker} must be an object")
        parsed[ticker] = {
            "entry_px": _positive(row.get("entry_px"), f"marks.{ticker}.entry_px"),
            "exit_px": _positive(row.get("exit_px"), f"marks.{ticker}.exit_px"),
        }
    return {
        "source": raw.get("source", "fixture"),
        "note": raw.get("note", ""),
        "decision_at": decision_at,
        "exit_at": exit_at,
        "starting_cash": starting_cash,
        "size_frac": size_frac,
        "marks": parsed,
        "path": str(marks_path),
    }


def _assert_decision_after_events(events: list[Event], decision_at: datetime) -> None:
    if events and max(event.observed_at for event in events) > decision_at:
        raise ValueError("decision_at must not precede the latest fixture observed_at")


def proposal_from_candidate(
    row: dict[str, Any],
    events: list[Event],
    size_frac: float,
    idempotency_key: str,
) -> dict[str, Any]:
    ticker = str(row["ticker"])
    return {
        "ticker": ticker,
        "side": "buy",
        "size_frac": size_frac,
        "event_ids": [event.id for event in events if event.ticker == ticker],
        "idempotency_key": idempotency_key,
    }


def _shares(starting_cash: float, size_frac: float, fill_px: float) -> float:
    return starting_cash * size_frac / fill_px


def _buy_pnl(shares: float, fill_px: float, exit_px: float) -> float:
    return shares * (exit_px - fill_px)


def _write_account(ledger_path: str, summary: dict[str, Any]) -> None:
    connection = sqlite3.connect(ledger_path)
    try:
        connection.executescript(_PNL_SCHEMA)
        connection.execute("DELETE FROM account")
        connection.execute("DELETE FROM positions")
        connection.execute(
            "INSERT INTO account (starting_cash, ending_equity, total_pnl, decision_at, exit_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                summary["starting_cash"],
                summary["ending_equity"],
                summary["total_pnl"],
                summary["decision_at"],
                summary["exit_at"],
            ),
        )
        for row in summary["positions"]:
            connection.execute(
                "INSERT INTO positions (ticker, side, size_frac, shares, fill_px, exit_px, pnl)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row["ticker"],
                    row["side"],
                    row["size_frac"],
                    row["shares"],
                    row["fill_px"],
                    row["exit_px"],
                    row["pnl"],
                ),
            )
        connection.commit()
    finally:
        connection.close()


def run_fixture_replay(
    *,
    fixtures: Path,
    ledger_path: str,
    audit_path: str | None = None,
    kill_root: str | None = None,
    mark_book: dict[str, Any] | None = None,
    mark_book_path: Path | None = None,
) -> dict[str, Any]:
    """Rank fixture events, fill through submit_paper_order, mark to fixture exits."""
    book = mark_book if mark_book is not None else load_mark_book(mark_book_path)
    events = load_fixture_events(fixtures)
    _assert_decision_after_events(events, book["decision_at"])
    with EventStore() as store:
        store.add_many(events)
        candidates = rank_candidates(store.all())

    size_frac = float(book["size_frac"])
    starting_cash = float(book["starting_cash"])
    decision_key = book["decision_at"].isoformat().replace("+00:00", "Z")
    orders: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = []
    positions: list[dict[str, Any]] = []
    gross_frac = 0.0

    for row in candidates:
        ticker = str(row["ticker"])
        mark = book["marks"].get(ticker)
        if mark is None:
            refusals.append({"ticker": ticker, "reason": "missing_fixture_mark"})
            continue
        if gross_frac + size_frac > MAX_GROSS_FRAC:
            refusals.append({"ticker": ticker, "reason": "gross_frac_cap"})
            continue
        proposal = proposal_from_candidate(
            row,
            events,
            size_frac,
            f"replay:{decision_key}:{ticker}",
        )
        try:
            filled = submit_paper_order(
                proposal,
                ledger_path=ledger_path,
                mark_px=mark["entry_px"],
                audit_path=audit_path,
                kill_root=kill_root,
            )
        except OrderRefused as error:
            refusals.append({"ticker": ticker, "reason": str(error)})
            continue
        shares = _shares(starting_cash, size_frac, filled["fill_px"])
        pnl = _buy_pnl(shares, filled["fill_px"], mark["exit_px"])
        position = {
            "ticker": ticker,
            "side": "buy",
            "size_frac": size_frac,
            "shares": shares,
            "fill_px": filled["fill_px"],
            "exit_px": mark["exit_px"],
            "pnl": pnl,
            "order_id": filled["order_id"],
        }
        orders.append(filled)
        positions.append(position)
        gross_frac += size_frac

    total_pnl = math.fsum(row["pnl"] for row in positions)
    summary = {
        "mode": "local-paper-replay",
        "mark_source": book.get("source", "fixture"),
        "mark_note": book.get("note", ""),
        "decision_at": decision_key,
        "exit_at": book["exit_at"].isoformat().replace("+00:00", "Z"),
        "starting_cash": starting_cash,
        "size_frac": size_frac,
        "gross_frac": gross_frac,
        "candidates": candidates,
        "orders": [
            {
                "order_id": order["order_id"],
                "ticker": order["ticker"],
                "side": order["side"],
                "size_frac": order["size_frac"],
                "fill_px": order["fill_px"],
                "status": order["status"],
            }
            for order in orders
        ],
        "refusals": refusals,
        "positions": [
            {key: row[key] for key in ("ticker", "side", "size_frac", "shares", "fill_px", "exit_px", "pnl")}
            for row in positions
        ],
        "total_pnl": total_pnl,
        "ending_equity": starting_cash + total_pnl,
        "ledger_path": str(ledger_path),
    }
    _write_account(ledger_path, summary)
    with open(str(ledger_path) + ".run.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, sort_keys=True) + "\n")
    return summary
