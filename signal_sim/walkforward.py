"""Tiny expanding walk-forward on fixture marks.

docs/paper-trading-and-quant.md evaluation gate: expanding windows,
purge/embargo covering the label horizon, per-fold fixture-mark PnL.
Declared drift params only. No search that maximizes that PnL.
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from .events import Event
from .fixture_load import load_fixture_events
from .indicators import NEWS_KINDS
from .params import DECISION_DELAY_HOURS, PLACEBO_SEED


DEFAULT_WALKFORWARD = (
    Path(__file__).resolve().parent.parent / "fixtures" / "marks" / "walkforward.json"
)
NOTE = (
    "Expanding fixture-mark folds. Per-fold PnL is fixture-mark, not a live result, "
    "and is not used to search parameters. Comparisons are no-news, shuffled-news, "
    "and news-only ablation on the same folds. They are not a search and not a "
    "functional claim."
)
COMPARISON_NAMES = ("no_news", "shuffled_news", "news_only")
COMPARISON_NOTE = (
    "Same fold clocks and declared drift params. no_news drops NEWS_KINDS. "
    "shuffled_news reassigns news identities onto the same admitted timestamps. "
    "news_only drops confirms. Fixture-mark PnL only. Not a search target."
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


def no_news_events(events: list[Event]) -> list[Event]:
    return [event for event in events if event.kind not in NEWS_KINDS]


def news_only_events(events: list[Event]) -> list[Event]:
    return [event for event in events if event.kind in NEWS_KINDS]


def shuffle_news_clocks(events: list[Event], *, seed: int) -> list[Event]:
    """Reassign news identities onto the same admitted timestamp slots.

    Keeps the bag of (occurred_at, filed_at, observed_at) so seasonality and
    the filed_at <= observed_at contract stay intact. occurred_at is still
    not the decision clock.
    """
    news = [event for event in events if event.kind in NEWS_KINDS]
    other = [event for event in events if event.kind not in NEWS_KINDS]
    if len(news) < 2:
        return list(events)
    slots = sorted(news, key=lambda event: (event.observed_at.isoformat(), event.id))
    payloads = list(slots)
    random.Random(seed).shuffle(payloads)
    remapped = [
        replace(
            payload,
            occurred_at=slot.occurred_at,
            filed_at=slot.filed_at,
            observed_at=slot.observed_at,
        )
        for slot, payload in zip(slots, payloads)
    ]
    return other + remapped


def variant_events(events: list[Event], name: str, *, seed: int) -> list[Event]:
    if name == "declared":
        return list(events)
    if name == "no_news":
        return no_news_events(events)
    if name == "news_only":
        return news_only_events(events)
    if name == "shuffled_news":
        return shuffle_news_clocks(events, seed=seed)
    raise ValueError(f"unknown walk-forward comparison: {name}")


def embargo_hours(book: dict[str, Any]) -> float:
    horizon_hours = (book["exit_at"] - book["decision_at"]).total_seconds() / 3600.0
    delay = float(book.get("decision_delay_hours", DECISION_DELAY_HOURS))
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
    books = load_walkforward_folds(walkforward_path)
    all_events = load_fixture_events(fixtures)
    folds: list[dict[str, Any]] = []
    for index, book in enumerate(books):
        admitted = fold_events(all_events, book["decision_at"])
        assert_no_future_prints(admitted, book["decision_at"])
        horizon_hours = (book["exit_at"] - book["decision_at"]).total_seconds() / 3600.0
        seed = PLACEBO_SEED + index
        declared = _run_fold_variant(
            fixtures=fixtures,
            ledger_path=str(Path(ledger_dir) / f"fold-{index + 1}.sqlite"),
            book=book,
            admitted=admitted,
            variant="declared",
            seed=seed,
            horizon_hours=horizon_hours,
        )
        comparisons: dict[str, Any] = {}
        for name in COMPARISON_NAMES:
            comparisons[name] = _run_fold_variant(
                fixtures=fixtures,
                ledger_path=str(Path(ledger_dir) / f"fold-{index + 1}-{name}.sqlite"),
                book=book,
                admitted=admitted,
                variant=name,
                seed=seed,
                horizon_hours=horizon_hours,
            )
        folds.append(
            {
                "fold": index + 1,
                "name": book["name"],
                "decision_at": declared["decision_at"],
                "exit_at": declared["exit_at"],
                "horizon_hours": declared["horizon_hours"],
                "embargo_hours": embargo_hours(book),
                "n_events": declared["n_events"],
                "event_ids": declared["event_ids"],
                "order_event_ids": declared["order_event_ids"],
                "n_orders": declared["n_orders"],
                "total_pnl": declared["total_pnl"],
                "ending_equity": declared["ending_equity"],
                "pnl_note": declared["pnl_note"],
                "orders": declared["orders"],
                "comparisons": comparisons,
            }
        )
    return {
        "mode": "local-paper-walkforward",
        "note": NOTE,
        "comparison_note": COMPARISON_NOTE,
        "placebo_seed": PLACEBO_SEED,
        "n_folds": len(folds),
        "folds": folds,
    }


def _run_fold_variant(
    *,
    fixtures: Path,
    ledger_path: str,
    book: dict[str, Any],
    admitted: list[Event],
    variant: str,
    seed: int,
    horizon_hours: float,
) -> dict[str, Any]:
    from .drift import drift_targets
    from .sim import run_fixture_replay

    used = variant_events(admitted, variant, seed=seed)
    assert_no_future_prints(used, book["decision_at"])
    targets = drift_targets(
        used,
        when=book["decision_at"],
        size_frac=float(book["size_frac"]),
        horizon_hours=horizon_hours,
    )
    summary = run_fixture_replay(
        fixtures=fixtures,
        ledger_path=ledger_path,
        mark_book=book,
        candidates=targets,
        events=used,
    )
    cited_ids = _ledger_event_ids(ledger_path)
    cited = [event for event in used if event.id in cited_ids]
    assert_no_future_prints(cited, book["decision_at"])
    return {
        "variant": variant,
        "decision_at": summary["decision_at"],
        "exit_at": summary["exit_at"],
        "horizon_hours": summary["horizon_hours"],
        "n_events": len(used),
        "event_ids": [event.id for event in used],
        "order_event_ids": sorted(cited_ids),
        "n_orders": summary["stats"]["n_orders"],
        "total_pnl": summary["total_pnl"],
        "ending_equity": summary["ending_equity"],
        "pnl_note": "fixture-mark PnL. Not a live result. Not a search target.",
        "orders": [row["ticker"] for row in summary["orders"]],
    }


def _ledger_event_ids(ledger_path: str) -> set[str]:
    path = Path(ledger_path)
    if not path.is_file():
        return set()
    connection = sqlite3.connect(ledger_path)
    try:
        tables = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "orders" not in tables:
            return set()
        rows = connection.execute("SELECT event_ids FROM orders").fetchall()
    finally:
        connection.close()
    cited: set[str] = set()
    for (raw,) in rows:
        cited.update(json.loads(raw))
    return cited
