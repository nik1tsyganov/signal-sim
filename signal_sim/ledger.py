"""Read-only inspect of a local paper ledger.

Prints order and fill counts, symbols, sides, qtys, and optional
fixture-mark MTM versus fixtures/marks. This is fixture-mark plumbing,
not alpha. Default is read-only: no broker POST and no sqlite write.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .params import STARTING_CASH, operate_stamp
from .runtime_env import paper_submit_flag
from .sim import _buy_pnl, _inventory, load_mark_book, resolve_mark_book_path

NOTE = (
    "Read-only local ledger inspect. Fixture-mark plumbing, not alpha. "
    "Not a broker fill. Not a live result. Does not POST. Does not write "
    "the sqlite file."
)
MTM_NOTE = (
    "Fixture-mark MTM versus fixtures/marks. Not alpha. Not a broker fill. "
    "Not a live result. Not a search target."
)
WRITE_REFUSED = "ledger inspect is read-only; omit --write"


def inspect_ledger(
    ledger_path: str | Path,
    *,
    fixtures: Path | None = None,
    mark_book_path: str | Path | None = None,
    allocation: float | None = None,
    write: bool = False,
) -> dict[str, Any]:
    """Read a local sqlite ledger. Never POSTs. Never writes unless write=True.

    ``write=True`` is refused in this build so inspect stays read-only.
    """
    if write:
        raise ValueError(WRITE_REFUSED)
    path = Path(ledger_path)
    if not path.is_file():
        raise FileNotFoundError(f"ledger not found: {path}")

    book = None
    if fixtures is not None or mark_book_path is not None:
        resolved = resolve_mark_book_path(mark_book_path) if mark_book_path else None
        book = load_mark_book(resolved)
    cash_base = float(allocation) if allocation is not None else (
        float(book["starting_cash"]) if book is not None else float(STARTING_CASH)
    )

    rows = _read_order_fills(path)
    orders = [_order_row(row, cash_base, book) for row in rows]
    stamp = operate_stamp()
    report: dict[str, Any] = {
        "mode": "paper-ledger-inspect",
        "note": NOTE,
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "read_only": True,
        "submitted": False,
        "order_post": "disabled",
        "submit_flag": paper_submit_flag(),
        "ledger_path": str(path),
        "allocation": cash_base,
        "n_orders": len({row["order_id"] for row in rows}),
        "n_fills": sum(1 for row in rows if row["fill_id"] is not None),
        "orders": orders,
        "mtm": None,
        "ok": True,
    }
    if book is not None:
        report["mtm"] = _fixture_mtm(path, book, cash_base)
        report["mark_book"] = book.get("path")
    return report


def _read_order_fills(path: Path) -> list[dict[str, Any]]:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        try:
            raw = connection.execute(
                "SELECT o.order_id, o.ticker, o.side, o.size_frac, o.status, "
                "o.created_at, f.fill_id, f.price, f.filled_at, "
                "COALESCE(f.cost, 0) "
                "FROM orders o LEFT JOIN fills f ON f.order_id = o.order_id "
                "ORDER BY o.ticker, o.created_at, o.order_id"
            ).fetchall()
        except sqlite3.OperationalError:
            return []
    finally:
        connection.close()
    rows = []
    for (
        order_id,
        ticker,
        side,
        size_frac,
        status,
        created_at,
        fill_id,
        price,
        filled_at,
        cost,
    ) in raw:
        rows.append(
            {
                "order_id": order_id,
                "ticker": ticker,
                "side": side,
                "size_frac": float(size_frac),
                "status": status,
                "created_at": created_at,
                "fill_id": fill_id,
                "price": None if price is None else float(price),
                "filled_at": filled_at,
                "cost": 0.0 if cost is None else float(cost),
            }
        )
    return rows


def _order_row(
    row: dict[str, Any],
    allocation: float,
    book: dict[str, Any] | None,
) -> dict[str, Any]:
    price = row["price"]
    qty = None
    if price is not None and price > 0:
        qty = allocation * float(row["size_frac"]) / price
        if row["side"] == "sell":
            qty = -qty
    out: dict[str, Any] = {
        "symbol": row["ticker"],
        "side": row["side"],
        "qty": qty,
        "size_frac": float(row["size_frac"]),
        "fill_px": price,
        "cost": float(row["cost"]),
        "status": row["status"],
        "filled_at": row["filled_at"],
        "order_id": row["order_id"],
        "mark_kind": None,
        "mark_source": None,
    }
    if book is not None:
        mark = book.get("marks", {}).get(row["ticker"])
        if isinstance(mark, dict) and not mark.get("unused"):
            out["mark_kind"] = str(mark.get("kind") or "fixture_mark")
            out["mark_source"] = str(mark.get("source") or "fixture")
    return out


def _fixture_mtm(
    path: Path,
    book: dict[str, Any],
    allocation: float,
) -> dict[str, Any]:
    held, cash, _last = _inventory(str(path), allocation)
    positions: list[dict[str, Any]] = []
    unmarked: list[str] = []
    for ticker, position in sorted(held.items()):
        mark = book.get("marks", {}).get(ticker)
        if mark is None or mark.get("unused"):
            unmarked.append(ticker)
            continue
        pnl = _buy_pnl(position["shares"], position["fill_px"], mark["exit_px"])
        positions.append(
            {
                "symbol": ticker,
                "side": "buy",
                "qty": position["shares"],
                "size_frac": position["size_frac"],
                "fill_px": position["fill_px"],
                "exit_px": mark["exit_px"],
                "pnl": pnl,
                "mark_kind": str(mark.get("kind") or "fixture_mark"),
                "mark_source": str(mark.get("source") or "fixture"),
            }
        )
    mark_value = sum(row["qty"] * row["exit_px"] for row in positions)
    ending_equity = cash + mark_value
    total_pnl = ending_equity - allocation
    n_winners = sum(1 for row in positions if row["pnl"] > 0)
    n_losers = sum(1 for row in positions if row["pnl"] < 0)
    return {
        "kind": "fixture-mark",
        "note": MTM_NOTE,
        "alpha": False,
        "mark_book": book.get("path"),
        "allocation": allocation,
        "cash": cash,
        "ending_equity": ending_equity,
        "total_pnl": total_pnl,
        "n_winners": n_winners,
        "n_losers": n_losers,
        "n_positions": len(positions),
        "unmarked": unmarked,
        "positions": positions,
    }
