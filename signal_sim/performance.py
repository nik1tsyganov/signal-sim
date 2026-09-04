"""Read-only Alpaca paper performance snapshot.

GETs paper account equity/cash, positions, clock, open orders, recent
orders, and fill activities. Writes an optional dated JSON under
docs/performance/. This is paper plumbing, not live money and not alpha.
It never POSTs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .alpaca_paper import sanitize_fill, sanitize_order
from .params import operate_stamp
from .rebalance import sanitize_account, sanitize_clock
from .runtime_env import paper_submit_flag

NOTE = (
    "Read-only Alpaca paper performance snapshot. Paper account only. "
    "Not live money. Not alpha. Not a broker fill-quality score. "
    "Does not POST. Does not write a local ledger."
)
SNAPSHOT_DIR = Path("docs/performance")
WRITE_NOTE = (
    "Dated paper snapshot JSON under docs/performance/. "
    "Paper account figures only. Not alpha."
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def default_snapshot_path(root: Path | None = None, when: datetime | None = None) -> Path:
    """docs/performance/YYYY-MM-DD.json relative to the repo root."""
    base = root if root is not None else Path(__file__).resolve().parent.parent
    stamp = when if when is not None else _utc_now()
    return base / SNAPSHOT_DIR / f"{stamp.date().isoformat()}.json"


def _position_symbols(positions: list[dict[str, Any]]) -> dict[str, str]:
    symbols: dict[str, str] = {}
    for row in positions:
        symbol = row.get("symbol")
        if not symbol:
            continue
        symbols[str(symbol)] = str(row.get("qty") or "0")
    return symbols


def paper_performance_snapshot(
    client: Any,
    *,
    order_limit: int = 100,
    fill_limit: int = 100,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    """GET paper account, positions, orders, and fills. Never POSTs."""
    when = captured_at if captured_at is not None else _utc_now()
    account = sanitize_account(client.account())
    positions = list(client.positions() or [])
    clock = sanitize_clock(client.clock())
    open_orders = [sanitize_order(row) for row in client.orders(status="open", limit=order_limit)]
    recent_orders = [sanitize_order(row) for row in client.orders(status="all", limit=order_limit)]
    fills_error = None
    fills: list[dict[str, Any]] = []
    try:
        fills = [sanitize_fill(row) for row in client.fills(limit=fill_limit)]
    except (RuntimeError, ValueError, NotImplementedError) as error:
        fills_error = str(error)
    stamp = operate_stamp()
    report: dict[str, Any] = {
        "mode": "alpaca-paper-performance",
        "note": NOTE,
        "label": "paper",
        "alpha": False,
        "live_money": False,
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "captured_at": _stamp(when),
        "read_only": True,
        "submitted": False,
        "order_post": "disabled",
        "submit_flag": paper_submit_flag(),
        "clock": clock,
        "account": account,
        "positions": {
            "n": len(positions),
            "symbols": _position_symbols(positions),
            "rows": positions,
        },
        "open_orders": open_orders,
        "orders": recent_orders,
        "fills": fills,
        "n_open_orders": len(open_orders),
        "n_orders": len(recent_orders),
        "n_fills": len(fills),
        "summary": {
            "paper": True,
            "alpha": False,
            "live_money": False,
            "cash": account.get("cash"),
            "equity": account.get("equity"),
            "n_positions": len(positions),
            "n_open_orders": len(open_orders),
            "n_orders": len(recent_orders),
            "n_fills": len(fills),
            "clock_is_open": clock.get("is_open"),
        },
        "ok": True,
    }
    if fills_error is not None:
        report["fills_error"] = fills_error
    return report


def write_paper_performance(
    report: dict[str, Any],
    path: str | Path,
) -> Path:
    """Write a sanitized paper snapshot JSON. Never logs secret values."""
    if not isinstance(report, dict):
        raise ValueError("performance report is required")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["snapshot_path"] = str(target)
    payload["write_note"] = WRITE_NOTE
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
