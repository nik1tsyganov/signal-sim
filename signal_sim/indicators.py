"""Deterministic candidate ranking with no order placement."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .events import Event


_UNIVERSE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "universe.json"


def load_universe(path: Path | None = None) -> tuple[str, ...]:
    """Load the frozen ticker list. Tests may pass a smaller fixture file."""
    raw = json.loads((path or _UNIVERSE_PATH).read_text(encoding="utf-8"))
    tickers = raw.get("tickers")
    if not isinstance(tickers, list) or not tickers:
        raise ValueError("universe tickers must be a non-empty list")
    names: list[str] = []
    for ticker in tickers:
        if not isinstance(ticker, str) or not ticker or not ticker.isascii() or not ticker.isupper():
            raise ValueError(f"invalid universe ticker: {ticker!r}")
        if ticker in names:
            raise ValueError(f"duplicate universe ticker: {ticker!r}")
        names.append(ticker)
    return tuple(names)


def load_sectors(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Load sector membership for the frozen universe."""
    raw = json.loads((path or _UNIVERSE_PATH).read_text(encoding="utf-8"))
    sectors = raw.get("sectors")
    if not isinstance(sectors, dict) or not sectors:
        raise ValueError("universe sectors must be a non-empty object")
    universe = set(load_universe(path))
    parsed: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    for name, tickers in sectors.items():
        if not isinstance(name, str) or not name:
            raise ValueError("sector names must be non-empty strings")
        if not isinstance(tickers, list) or not tickers:
            raise ValueError(f"sector {name!r} tickers must be a non-empty list")
        names = tuple(str(ticker) for ticker in tickers)
        if any(ticker not in universe for ticker in names):
            raise ValueError(f"sector {name!r} contains a ticker outside the universe")
        overlap = seen.intersection(names)
        if overlap:
            raise ValueError(f"duplicate sector ticker: {sorted(overlap)[0]!r}")
        seen.update(names)
        parsed[name] = names
    if seen != universe:
        raise ValueError("sector membership must cover the frozen universe")
    return parsed


UNIVERSE = load_universe()
SECTORS = load_sectors()
NEWS_KINDS = {"news", "intel_brief"}
CONFIRM_KINDS = {"insider", "congress_trade"}
GOV_CONFIRM_KINDS = {"gov_contract"}


def _filed_confirmation(
    events: list[Event],
    ticker: str,
    kinds: set[str],
    window_end: datetime | None,
) -> int:
    return int(
        any(
            event.kind in kinds
            and event.ticker == ticker
            and event.filed_at is not None
            and event.observed_at >= event.filed_at
            and (window_end is None or event.observed_at <= window_end)
            for event in events
        )
    )


def rank_candidates(
    events: list[Event],
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    universe: tuple[str, ...] | None = None,
) -> list[dict[str, int | str]]:
    """Rank positive-score paper-trade candidates using observed time only."""
    rows = []
    for ticker in UNIVERSE if universe is None else universe:
        news_breakout = sum(
            event.kind in NEWS_KINDS
            and event.ticker == ticker
            and (window_start is None or event.observed_at >= window_start)
            and (window_end is None or event.observed_at <= window_end)
            for event in events
        )
        insider_confirm = _filed_confirmation(events, ticker, CONFIRM_KINDS, window_end)
        gov_confirm = _filed_confirmation(events, ticker, GOV_CONFIRM_KINDS, window_end)
        score = news_breakout + insider_confirm + gov_confirm
        if score:
            row = {
                "ticker": ticker,
                "score": score,
                "news_breakout": news_breakout,
                "insider_confirm": insider_confirm,
            }
            # gov_confirm is omitted at zero so news/insider-only outputs keep the legacy row schema.
            if gov_confirm:
                row["gov_confirm"] = gov_confirm
            rows.append(row)
    return sorted(rows, key=lambda row: (-int(row["score"]), str(row["ticker"])))
