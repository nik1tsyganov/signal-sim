"""Deterministic candidate ranking with no order placement."""

from __future__ import annotations

from datetime import datetime

from .events import Event


UNIVERSE = ("NVDA", "XLE", "DIS")
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
) -> list[dict[str, int | str]]:
    """Rank positive-score paper-trade candidates using observed time only."""
    rows = []
    for ticker in UNIVERSE:
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
