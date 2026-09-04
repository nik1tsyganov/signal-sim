"""Tiny expanding walk-forward on fixture marks.

docs/paper-trading-and-quant.md evaluation gate: expanding windows,
purge/embargo covering the label horizon, per-fold fixture-mark PnL.
Declared drift params only. No search that maximizes that PnL.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any

from .events import Event
from .fixture_load import load_fixture_events


DEFAULT_WALKFORWARD = (
    Path(__file__).resolve().parent.parent / "fixtures" / "marks" / "walkforward.json"
)
NOTE = (
    "Expanding fixture-mark folds. Per-fold PnL is fixture-mark, not a live result, "
    "and is not used to search parameters."
)


def assert_no_future_prints(events: list[Event], decision_at) -> None:
    """Refuse any print first seen after the fold decision. Fails closed on leak."""
    leaked = [event.id for event in events if event.observed_at > decision_at]
    if leaked:
        raise ValueError(f"later print leaked into earlier fold: {leaked}")


def fold_events(events: list[Event], decision_at) -> list[Event]:
    admitted = [event for event in events if event.observed_at <= decision_at]
    assert_no_future_prints(admitted, decision_at)
    return admitted


def embargo_hours(book: dict[str, Any]) -> float:
    horizon_hours = (book["exit_at"] - book["decision_at"]).total_seconds() / 3600.0
    delay = float(book.get("decision_delay_hours", 1.0))
    return horizon_hours + delay


def load_walkforward_folds(path: Path | None = None) -> list[dict[str, Any]]:
    from .sim import _parse_mark_book

    path_file = DEFAULT_WALKFORWARD if path is None else path
    with path_file.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    if not isinstance(raw, dict):
        raise ValueError("walk-forward file must be an object")
    folds = raw.get("folds")
    if not isinstance(folds, list) or len(folds) < 2:
        raise ValueError("walk-forward folds must be a list of at least two folds")
    shared = {key: value for key, value in raw.items() if key != "folds"}
    books: list[dict[str, Any]] = []
    previous = None
    for index, step in enumerate(folds):
        if not isinstance(step, dict):
            raise ValueError(f"folds[{index}] must be an object")
        merged = dict(shared)
        merged.update(step)
        book = _parse_mark_book(merged, path_file)
        book["name"] = str(step.get("name") or f"fold-{index + 1}")
        if previous is not None:
            wait = embargo_hours(previous)
            earliest = previous["exit_at"] + timedelta(hours=wait)
            if book["decision_at"] < earliest:
                raise ValueError(
                    f"fold {book['name']} decision_at violates embargo "
                    f"of {wait} hours after the previous exit_at"
                )
            if book["decision_at"] <= previous["decision_at"]:
                raise ValueError("walk-forward folds must expand in time")
        previous = book
        books.append(book)
    return books


def run_fixture_walkforward(
    *,
    fixtures: Path,
    ledger_dir: str,
    walkforward_path: Path | None = None,
) -> dict[str, Any]:
    """Score declared drift on each expanding fold. Does not search parameters."""
    from .drift import fixture_drift_book
    from .sim import run_fixture_replay

    books = load_walkforward_folds(walkforward_path)
    all_events = load_fixture_events(fixtures)
    folds: list[dict[str, Any]] = []
    for index, book in enumerate(books):
        admitted = fold_events(all_events, book["decision_at"])
        assert_no_future_prints(admitted, book["decision_at"])
        targets = fixture_drift_book(fixtures, mark_book=book)["targets"]
        ledger_path = str(Path(ledger_dir) / f"fold-{index + 1}.sqlite")
        summary = run_fixture_replay(
            fixtures=fixtures,
            ledger_path=ledger_path,
            mark_book=book,
            candidates=targets,
        )
        cited_ids = _ledger_event_ids(ledger_path)
        cited = [event for event in all_events if event.id in cited_ids]
        assert_no_future_prints(cited, book["decision_at"])
        folds.append(
            {
                "fold": index + 1,
                "name": book["name"],
                "decision_at": summary["decision_at"],
                "exit_at": summary["exit_at"],
                "horizon_hours": summary["horizon_hours"],
                "embargo_hours": embargo_hours(book),
                "n_events": len(admitted),
                "event_ids": [event.id for event in admitted],
                "order_event_ids": sorted(cited_ids),
                "n_orders": summary["stats"]["n_orders"],
                "total_pnl": summary["total_pnl"],
                "ending_equity": summary["ending_equity"],
                "pnl_note": "fixture-mark PnL. Not a live result. Not a search target.",
                "orders": [row["ticker"] for row in summary["orders"]],
            }
        )
    return {
        "mode": "local-paper-walkforward",
        "note": NOTE,
        "n_folds": len(folds),
        "folds": folds,
    }


def _ledger_event_ids(ledger_path: str) -> set[str]:
    connection = sqlite3.connect(ledger_path)
    try:
        rows = connection.execute("SELECT event_ids FROM orders").fetchall()
    finally:
        connection.close()
    cited: set[str] = set()
    for (raw,) in rows:
        cited.update(json.loads(raw))
    return cited
