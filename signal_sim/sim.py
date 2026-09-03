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
from .hawkes import log_likelihood
from .indicators import UNIVERSE, rank_candidates
from .paper import OrderRefused, submit_paper_order
from .sizer import MAX_GROSS_FRAC, size_targets
from .store import EventStore


DEFAULT_MARKS = Path(__file__).resolve().parent.parent / "fixtures" / "marks" / "universe.json"
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "marks" / "path.json"
FILL_RULE = "decision-time fixture mark; size_frac of starting_cash"
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


def _parse_mark_book(raw: dict[str, Any], marks_path: Path) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("mark book must be an object")
    decision_at = _aware(raw.get("decision_at", ""), "decision_at")
    exit_at = _aware(raw.get("exit_at", ""), "exit_at")
    if exit_at <= decision_at:
        raise ValueError("exit_at must be after decision_at")
    starting_cash = _positive(raw.get("starting_cash"), "starting_cash")
    size_frac = _positive(raw.get("size_frac"), "size_frac")
    max_drawdown = raw.get("max_drawdown", 0.2)
    max_drawdown = _positive(max_drawdown, "max_drawdown")
    if max_drawdown > 1:
        raise ValueError("max_drawdown must be at most 1")
    max_gross_frac = raw.get("max_gross_frac", MAX_GROSS_FRAC)
    max_gross_frac = _positive(max_gross_frac, "max_gross_frac")
    if size_frac > max_gross_frac:
        raise ValueError("size_frac must be at most max_gross_frac")
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
        "max_drawdown": max_drawdown,
        "max_gross_frac": max_gross_frac,
        "marks": parsed,
        "path": str(marks_path),
    }


def load_mark_book(path: Path | None = None) -> dict[str, Any]:
    marks_path = DEFAULT_MARKS if path is None else path
    with marks_path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return _parse_mark_book(raw, marks_path)


def load_mark_path(path: Path | None = None) -> list[dict[str, Any]]:
    path_file = DEFAULT_PATH if path is None else path
    with path_file.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("mark path must be an object")
    steps = raw.get("steps")
    if not isinstance(steps, list) or len(steps) < 2:
        raise ValueError("mark path steps must be a list of at least two steps")
    shared = {key: value for key, value in raw.items() if key != "steps"}
    books: list[dict[str, Any]] = []
    previous_exit = None
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{index}] must be an object")
        merged = dict(shared)
        merged.update(step)
        book = _parse_mark_book(merged, path_file)
        if previous_exit is not None and book["decision_at"] < previous_exit:
            raise ValueError("path step decision_at must not precede the previous exit_at")
        previous_exit = book["exit_at"]
        books.append(book)
    return books


def _assert_decision_after_events(events: list[Event], decision_at: datetime) -> None:
    if events and max(event.observed_at for event in events) > decision_at:
        raise ValueError("decision_at must not precede the latest fixture observed_at")


def proposal_from_candidate(
    ticker: str,
    events: list[Event],
    size_frac: float,
    idempotency_key: str,
    side: str = "buy",
    event_ids: list[str] | None = None,
) -> dict[str, Any]:
    ids = event_ids if event_ids is not None else [event.id for event in events if event.ticker == ticker]
    return {
        "ticker": ticker,
        "side": side,
        "size_frac": size_frac,
        "event_ids": ids,
        "idempotency_key": idempotency_key,
    }


def _inventory(ledger_path: str, starting_cash: float) -> tuple[dict[str, dict[str, Any]], float, float | None]:
    """Rebuild cash and positions from filled orders. last_pnl is prior-run MTM or None."""
    connection = sqlite3.connect(ledger_path)
    try:
        try:
            rows = connection.execute(
                "SELECT o.ticker, o.side, o.size_frac, o.event_ids, f.price "
                "FROM orders o JOIN fills f ON f.order_id = o.order_id "
                "WHERE o.status = 'filled' ORDER BY o.created_at, o.order_id"
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        try:
            account = connection.execute("SELECT total_pnl FROM account").fetchone()
        except sqlite3.OperationalError:
            account = None
    finally:
        connection.close()
    held: dict[str, dict[str, Any]] = {}
    cash = starting_cash
    for ticker, side, size_frac, event_ids_raw, price in rows:
        shares = _shares(starting_cash, float(size_frac), float(price))
        ids = json.loads(event_ids_raw) if isinstance(event_ids_raw, str) else []
        current = held.get(
            ticker,
            {"shares": 0.0, "size_frac": 0.0, "fill_px": float(price), "event_ids": ids, "side": "buy"},
        )
        if side == "buy":
            cash -= shares * float(price)
            new_shares = current["shares"] + shares
            if new_shares > 0:
                current["fill_px"] = (
                    current["shares"] * current["fill_px"] + shares * float(price)
                ) / new_shares
        else:
            cash += shares * float(price)
            new_shares = current["shares"] - shares
        current["shares"] = new_shares
        current["event_ids"] = ids or current["event_ids"]
        current["side"] = "buy"
        if new_shares <= 1e-12:
            held.pop(ticker, None)
        else:
            current["size_frac"] = new_shares * current["fill_px"] / starting_cash
            held[ticker] = current
    last_pnl = None if account is None else float(account[0])
    return held, cash, last_pnl


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


def _place(
    *,
    ticker: str,
    side: str,
    size_frac: float,
    mark_px: float,
    events: list[Event],
    event_ids: list[str] | None,
    ledger_path: str,
    audit_path: str | None,
    kill_root: str | None,
    decision_key: str,
    action: str,
) -> dict[str, Any]:
    return submit_paper_order(
        proposal_from_candidate(
            ticker,
            events,
            size_frac,
            f"replay:{decision_key}:{ticker}:{side}:{action}:{size_frac}",
            side=side,
            event_ids=event_ids,
        ),
        ledger_path=ledger_path,
        mark_px=mark_px,
        audit_path=audit_path,
        kill_root=kill_root,
    )


def run_fixture_replay(
    *,
    fixtures: Path,
    ledger_path: str,
    audit_path: str | None = None,
    kill_root: str | None = None,
    mark_book: dict[str, Any] | None = None,
    mark_book_path: Path | None = None,
    candidates: list[dict[str, Any]] | None = None,
    universe: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Rank fixture events, rebalance through submit_paper_order, mark to fixture exits."""
    book = mark_book if mark_book is not None else load_mark_book(mark_book_path)
    events = load_fixture_events(fixtures)
    _assert_decision_after_events(events, book["decision_at"])
    if candidates is None:
        with EventStore() as store:
            store.add_many(events)
            candidates = rank_candidates(store.all(), universe=universe)

    size_frac = float(book["size_frac"])
    starting_cash = float(book["starting_cash"])
    max_drawdown = float(book.get("max_drawdown", 0.2))
    max_gross_frac = float(book.get("max_gross_frac", MAX_GROSS_FRAC))
    decision_key = book["decision_at"].isoformat().replace("+00:00", "Z")
    horizon_hours = (book["exit_at"] - book["decision_at"]).total_seconds() / 3600.0
    targets, skipped = size_targets(
        candidates,
        size_frac=size_frac,
        horizon_hours=horizon_hours,
        max_gross_frac=max_gross_frac,
    )
    held, cash, last_pnl = _inventory(ledger_path, starting_cash)
    for ticker in held:
        if ticker not in book["marks"]:
            raise ValueError(f"held ticker missing fixture mark: {ticker!r}")
    halted = last_pnl is not None and last_pnl <= -max_drawdown * starting_cash
    orders: list[dict[str, Any]] = []
    refusals: list[dict[str, Any]] = [{"ticker": row["ticker"], "reason": row["reason"]} for row in skipped]
    wanted = {str(row["ticker"]): row for row in targets}
    reserved_cash = cash

    def refuse(ticker: str, reason: str) -> None:
        refusals.append({"ticker": ticker, "reason": reason})

    actions: list[tuple[str, str, float, float, list[str] | None, str]] = []
    for ticker, position in list(held.items()):
        if ticker not in wanted:
            mark = book["marks"].get(ticker)
            if mark is None:
                raise ValueError(f"held ticker missing fixture mark: {ticker!r}")
            sell_px = mark["entry_px"]
            trade_frac = position["shares"] * sell_px / starting_cash
            if trade_frac <= 1e-12:
                continue
            actions.append(
                (ticker, "sell", trade_frac, sell_px, position.get("event_ids"), "close")
            )
            reserved_cash += position["shares"] * sell_px
    for row in targets:
        ticker = str(row["ticker"])
        mark = book["marks"].get(ticker)
        if mark is None:
            refuse(ticker, "missing_fixture_mark")
            continue
        mark_px = mark["entry_px"]
        have_shares = float(held.get(ticker, {}).get("shares", 0.0))
        target_shares = starting_cash * float(row["target_frac"]) / mark_px
        delta_shares = target_shares - have_shares
        if abs(delta_shares) <= 1e-12:
            continue
        if delta_shares < 0:
            delta_shares = max(delta_shares, -have_shares)
        trade_frac = abs(delta_shares) * mark_px / starting_cash
        if trade_frac <= 1e-12:
            continue
        if halted and delta_shares > 0:
            refuse(ticker, "drawdown_halt")
            continue
        if delta_shares > 0:
            cost = trade_frac * starting_cash
            if cost - reserved_cash > 1e-9:
                refuse(ticker, "cash_constraint")
                continue
            reserved_cash -= cost
        side = "buy" if delta_shares > 0 else "sell"
        event_ids = None if delta_shares > 0 else held.get(ticker, {}).get("event_ids")
        action = "open" if have_shares <= 1e-12 else "adjust"
        actions.append((ticker, side, trade_frac, mark_px, event_ids, action))

    for ticker, side, frac, mark_px, event_ids, action in actions:
        try:
            filled = _place(
                ticker=ticker,
                side=side,
                size_frac=frac,
                mark_px=mark_px,
                events=events,
                event_ids=event_ids,
                ledger_path=ledger_path,
                audit_path=audit_path,
                kill_root=kill_root,
                decision_key=decision_key,
                action=action,
            )
        except OrderRefused as error:
            refuse(ticker, str(error))
            continue
        notional = starting_cash * frac
        if side == "buy":
            cash -= notional
        else:
            cash += notional
        orders.append(filled)

    held, cash, _last = _inventory(ledger_path, starting_cash)
    positions = []
    for ticker, position in sorted(held.items()):
        mark = book["marks"].get(ticker)
        if mark is None:
            raise ValueError(f"held ticker missing fixture mark: {ticker!r}")
        pnl = _buy_pnl(position["shares"], position["fill_px"], mark["exit_px"])
        positions.append(
            {
                "ticker": ticker,
                "side": "buy",
                "size_frac": position["size_frac"],
                "shares": position["shares"],
                "fill_px": position["fill_px"],
                "exit_px": mark["exit_px"],
                "pnl": pnl,
            }
        )

    mark_value = math.fsum(row["shares"] * row["exit_px"] for row in positions)
    ending_equity = cash + mark_value
    total_pnl = ending_equity - starting_cash
    hawkes_ll = log_likelihood(events, start=book["decision_at"], end=book["exit_at"])
    hawkes_n_arrivals = sum(
        1
        for event in events
        if event.ticker in UNIVERSE and book["decision_at"] <= event.observed_at <= book["exit_at"]
    )
    n_winners = sum(1 for row in positions if row["pnl"] > 0)
    n_losers = sum(1 for row in positions if row["pnl"] < 0)
    n_flat = sum(1 for row in positions if row["pnl"] == 0)
    hit_rate = (n_winners / len(positions)) if positions else None
    turnover = math.fsum(order["size_frac"] for order in orders)
    max_name_frac = max((row["size_frac"] for row in positions), default=0.0)
    unrealized_pnl = math.fsum(row["pnl"] for row in positions)
    realized_pnl = total_pnl - unrealized_pnl
    gross_frac = math.fsum(row["size_frac"] for row in positions)
    summary = {
        "mode": "local-paper-replay",
        "mark_source": book.get("source", "fixture"),
        "mark_note": book.get("note", ""),
        "fill_rule": FILL_RULE,
        "decision_at": decision_key,
        "exit_at": book["exit_at"].isoformat().replace("+00:00", "Z"),
        "horizon_hours": horizon_hours,
        "starting_cash": starting_cash,
        "cash": cash,
        "size_frac": size_frac,
        "max_drawdown": max_drawdown,
        "drawdown_halt": halted,
        "gross_frac": gross_frac,
        "targets": targets,
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
        "positions": positions,
        "total_pnl": total_pnl,
        "ending_equity": ending_equity,
        "hawkes_log_likelihood": hawkes_ll,
        "stats": {
            "n_candidates": len(candidates),
            "n_orders": len(orders),
            "n_refusals": len(refusals),
            "n_positions": len(positions),
            "n_winners": n_winners,
            "n_losers": n_losers,
            "n_flat": n_flat,
            "hit_rate": hit_rate,
            "turnover": turnover,
            "max_name_frac": max_name_frac,
            "gross_frac": gross_frac,
            "mark_value": mark_value,
            "unrealized_pnl": unrealized_pnl,
            "realized_pnl": realized_pnl,
            "total_pnl": total_pnl,
            "ending_equity": ending_equity,
            "hawkes_log_likelihood": hawkes_ll,
            "hawkes_n_arrivals": hawkes_n_arrivals,
        },
        "ledger_path": str(ledger_path),
    }
    _write_account(ledger_path, summary)
    with open(str(ledger_path) + ".run.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary, sort_keys=True) + "\n")
    return summary


def run_fixture_path(
    *,
    fixtures: Path,
    ledger_path: str,
    audit_path: str | None = None,
    kill_root: str | None = None,
    mark_path: Path | None = None,
    universe: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Replay successive fixture mark books on one ledger. Not vendor bars."""
    books = load_mark_path(mark_path)
    steps = [
        run_fixture_replay(
            fixtures=fixtures,
            ledger_path=ledger_path,
            audit_path=audit_path,
            kill_root=kill_root,
            mark_book=book,
            universe=universe,
        )
        for book in books
    ]
    starting_cash = float(books[0]["starting_cash"])
    equity_curve = [float(step["ending_equity"]) for step in steps]
    peak = starting_cash
    worst_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        worst_drawdown = min(worst_drawdown, equity - peak)
    total_pnl = equity_curve[-1] - starting_cash
    summary = {
        "mode": "local-paper-path",
        "mark_source": books[0].get("source", "fixture"),
        "mark_note": books[0].get("note", ""),
        "fill_rule": FILL_RULE,
        "starting_cash": starting_cash,
        "steps": steps,
        "equity_curve": equity_curve,
        "ending_equity": equity_curve[-1],
        "total_pnl": total_pnl,
        "worst_drawdown": worst_drawdown,
        "worst_drawdown_frac": worst_drawdown / starting_cash,
        "ledger_path": str(ledger_path),
        "stats": {
            "n_steps": len(steps),
            "n_orders": sum(len(step["orders"]) for step in steps),
            "ending_equity": equity_curve[-1],
            "total_pnl": total_pnl,
            "worst_drawdown": worst_drawdown,
            "worst_drawdown_frac": worst_drawdown / starting_cash,
            "hit_rate": steps[-1]["stats"].get("hit_rate"),
        },
    }
    with open(str(ledger_path) + ".run.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({key: value for key, value in summary.items() if key != "steps"}, sort_keys=True) + "\n")
    return summary
