"""Print-only paper rebalance tickets.

Reads a paper account snapshot and the existing fixture / cluster-drift
target book, then prints intended tickets. It does not POST to a broker,
does not call submit_paper_order, and does not invent a new alpha.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .drift import NOTE as DRIFT_NOTE
from .drift import fixture_drift_book
from .fixture_load import load_fixture_events
from .indicators import UNIVERSE, rank_candidates
from .paper import execution_mark_failure
from .params import COST_BPS, operate_stamp
from .sizer import size_targets
from .sim import load_mark_book, resolve_mark_book_path
from .store import EventStore

NOTE = (
    "Print-only dry-run. Fixture or cluster-drift target book versus paper "
    "positions. Not alpha. Not a broker fill. Qty and notional use fixture "
    "entry_px, not a live quote. No remote paper POST."
)
SIGNAL_DRIFT = "cluster-drift-stub"
SIGNAL_RANK = "rank-candidates"
_ACCOUNT_FIELDS = (
    "status",
    "currency",
    "cash",
    "equity",
    "buying_power",
    "trading_blocked",
    "account_blocked",
    "pattern_day_trader",
    "shorting_enabled",
)
_CLOCK_FIELDS = ("timestamp", "is_open", "next_open", "next_close")
_EPS = 1e-12


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _aware_stamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_account(account: Any) -> dict[str, Any]:
    if not isinstance(account, dict):
        return {}
    return {field: account.get(field) for field in _ACCOUNT_FIELDS}


def sanitize_clock(clock: Any) -> dict[str, Any]:
    if not isinstance(clock, dict):
        return {}
    return {field: clock.get(field) for field in _CLOCK_FIELDS}


def allocation_base(account: dict[str, Any], starting_cash: float) -> float:
    """Paper equity, then cash, then the fixture starting_cash fallback."""
    for field in ("equity", "cash"):
        number = _finite_number(account.get(field))
        if number is not None and number > 0:
            return number
    return float(starting_cash)


def paper_held(positions: list[Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Map paper position rows to signed share counts. Unknown names are skipped."""
    held: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("ticker")
        if not isinstance(symbol, str) or not symbol:
            continue
        qty = _finite_number(item.get("qty"))
        if qty is None:
            skipped.append({"ticker": symbol, "reason": "unreadable_qty"})
            continue
        side = str(item.get("side") or "long").strip().lower()
        if side == "short":
            shares = -abs(qty)
        elif side in {"long", "buy", ""}:
            shares = abs(qty)
        else:
            skipped.append({"ticker": symbol, "reason": "unknown_position_side"})
            continue
        if symbol not in UNIVERSE:
            skipped.append({"ticker": symbol, "reason": "ticker_not_in_universe"})
            continue
        held[symbol] = {"shares": shares, "side": "short" if shares < 0 else "long"}
    return held, skipped


def _ticket_payload(symbol: str, side: str, key: str) -> dict[str, str]:
    return {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "client_order_id": key,
    }


def _rationale(*, signal: str, action: str, target_frac: float | None, have_shares: float) -> str:
    held = f"paper holds {have_shares:g}"
    if action == "close":
        return f"{signal}; close leftover; not in target book; {held}"
    frac = 0.0 if target_frac is None else float(target_frac)
    return f"{signal}; {action} to target_frac={frac:g}; {held}"


def plan_rebalance_tickets(
    *,
    targets: list[dict[str, Any]],
    marks: dict[str, dict[str, Any]],
    held: dict[str, dict[str, Any]],
    cash: float,
    allocation: float,
    cost_bps: float,
    signal: str,
    decision_at: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Share-accurate tickets. Same delta as fixture replay; print-only."""
    tickets: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    wanted = {str(row["ticker"]): row for row in targets}
    reserved_cash = float(cash)

    def skip(ticker: str, reason: str) -> None:
        skipped.append({"ticker": ticker, "reason": reason})

    def emit(
        ticker: str,
        side: str,
        trade_frac: float,
        mark_px: float,
        action: str,
        have_shares: float,
        target_frac: float | None,
        qty: float,
    ) -> None:
        key = f"rebalance:{decision_at}:{ticker}:{side}:{action}"
        notional = abs(qty) * float(mark_px)
        tickets.append(
            {
                "symbol": ticker,
                "side": side,
                "qty": qty,
                "notional": notional,
                "size_frac": float(trade_frac),
                "mark_px": float(mark_px),
                "mark_kind": "fixture_mark",
                "action": action,
                "rationale": _rationale(
                    signal=signal,
                    action=action,
                    target_frac=target_frac,
                    have_shares=have_shares,
                ),
                "submitted": False,
                "payload": _ticket_payload(ticker, side, key),
            }
        )

    for ticker, position in list(held.items()):
        if ticker in wanted:
            continue
        mark = marks.get(ticker)
        if mark is None or mark.get("unused"):
            skip(ticker, "held_no_mark")
            continue
        mark_failure = execution_mark_failure(mark.get("kind"), mark.get("source"))
        if mark_failure is not None:
            skip(ticker, mark_failure)
            continue
        have_shares = float(position["shares"])
        sell_px = float(mark["entry_px"])
        trade_frac = abs(have_shares) * sell_px / allocation
        if trade_frac <= _EPS:
            continue
        qty = -have_shares if have_shares > 0 else abs(have_shares)
        side = "sell" if have_shares > 0 else "buy"
        fee = allocation * trade_frac * cost_bps / 10000.0
        reserved_cash += abs(have_shares) * sell_px - fee
        emit(ticker, side, trade_frac, sell_px, "close", have_shares, None, qty)

    for row in targets:
        ticker = str(row["ticker"])
        mark = marks.get(ticker)
        if mark is None or mark.get("unused"):
            skip(ticker, "no_mark")
            continue
        mark_failure = execution_mark_failure(mark.get("kind"), mark.get("source"))
        if mark_failure is not None:
            skip(ticker, mark_failure)
            continue
        mark_px = float(mark["entry_px"])
        have_shares = float(held.get(ticker, {}).get("shares", 0.0))
        target_shares = allocation * float(row["target_frac"]) / mark_px
        delta_shares = target_shares - have_shares
        if abs(delta_shares) <= _EPS:
            continue
        if delta_shares < 0:
            delta_shares = max(delta_shares, -have_shares)
        trade_frac = abs(delta_shares) * mark_px / allocation
        if trade_frac <= _EPS:
            continue
        if delta_shares > 0:
            notional = trade_frac * allocation
            fee = notional * cost_bps / 10000.0
            if notional + fee - reserved_cash > 1e-9:
                skip(ticker, "cash_constraint")
                continue
            reserved_cash -= notional + fee
        side = "buy" if delta_shares > 0 else "sell"
        action = "open" if abs(have_shares) <= _EPS else "adjust"
        emit(
            ticker,
            side,
            trade_frac,
            mark_px,
            action,
            have_shares,
            float(row["target_frac"]),
            delta_shares,
        )
    return tickets, skipped


def _fillable_candidates(
    candidates: list[dict[str, Any]],
    marks: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    fillable: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for row in candidates:
        ticker = str(row["ticker"])
        mark = marks.get(ticker)
        if mark is None or mark.get("unused"):
            skipped.append({"ticker": ticker, "reason": "no_mark"})
            continue
        mark_failure = execution_mark_failure(mark.get("kind"), mark.get("source"))
        if mark_failure is not None:
            skipped.append({"ticker": ticker, "reason": mark_failure})
            continue
        fillable.append(row)
    return fillable, skipped


def proposed_rebalance(
    *,
    fixtures: Path | None = None,
    mark_book_path: Path | str | None = None,
    account: dict[str, Any] | None = None,
    positions: list[Any] | None = None,
    clock: dict[str, Any] | None = None,
    client: Any | None = None,
    signal: str = "drift",
    intensity: bool = False,
) -> dict[str, Any]:
    """Read paper snapshot + fixture target book; return print-only tickets."""
    if signal not in {"drift", "rank"}:
        raise ValueError("signal must be 'drift' or 'rank'")
    if intensity and signal != "drift":
        raise ValueError("intensity overlay requires the cluster-drift book")
    if client is not None:
        if account is None:
            account = client.account()
        if positions is None:
            positions = client.positions()
        if clock is None:
            clock = client.clock()
    if account is None:
        raise ValueError("paper account snapshot is required")
    if positions is None:
        positions = []

    root = fixtures if fixtures is not None else Path(__file__).resolve().parent.parent / "fixtures"
    resolved = resolve_mark_book_path(mark_book_path) if mark_book_path else None
    book = load_mark_book(resolved)
    decision_at = book["decision_at"].isoformat().replace("+00:00", "Z")
    horizon_hours = (book["exit_at"] - book["decision_at"]).total_seconds() / 3600.0
    events = [
        event for event in load_fixture_events(root) if event.observed_at <= book["decision_at"]
    ]

    if signal == "drift":
        drift_book = fixture_drift_book(
            root,
            resolved,
            intensity=intensity,
        )
        candidates = list(drift_book["targets"])
        signal_name = SIGNAL_DRIFT
        signal_note = DRIFT_NOTE
    else:
        with EventStore() as store:
            store.add_many(events)
            candidates = rank_candidates(store.all(), window_end=book["decision_at"])
        signal_name = SIGNAL_RANK
        signal_note = "Existing rank_candidates at the mark-book decision_at. Not a new alpha."

    fillable, pre_skips = _fillable_candidates(candidates, book["marks"])
    targets, size_skips = size_targets(
        fillable,
        size_frac=float(book["size_frac"]),
        horizon_hours=horizon_hours,
        max_gross_frac=float(book["max_gross_frac"]),
        max_name_frac=float(book["max_name_frac"]),
    )
    safe_account = sanitize_account(account)
    allocation = allocation_base(safe_account, float(book["starting_cash"]))
    cash = _finite_number(safe_account.get("cash"))
    if cash is None:
        cash = allocation
    held, held_skips = paper_held(list(positions))
    tickets, plan_skips = plan_rebalance_tickets(
        targets=targets,
        marks=book["marks"],
        held=held,
        cash=cash,
        allocation=allocation,
        cost_bps=float(book.get("cost_bps", COST_BPS)),
        signal=signal_name,
        decision_at=decision_at,
    )
    skipped = [*held_skips, *pre_skips, *size_skips, *plan_skips]
    stamp = operate_stamp()
    safe_clock = sanitize_clock(clock)
    return {
        "mode": "paper-rebalance-dry-run",
        "note": NOTE,
        "signal": signal_name,
        "signal_note": signal_note,
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "decision_at": decision_at,
        "printed_at": _utc_now(),
        "clock": safe_clock,
        "account": safe_account,
        "positions": {
            "n": len(held),
            "symbols": {
                ticker: f"{row['shares']:g}" for ticker, row in sorted(held.items())
            },
        },
        "allocation": allocation,
        "targets": targets,
        "tickets": tickets,
        "skipped": skipped,
        "n_tickets": len(tickets),
        "n_skipped": len(skipped),
        "order_post": "disabled",
        "submitted": False,
        "ok": True,
    }
