"""Live intel pull for counts and ticker histograms only.

Does not dump person names, headlines, URLs, or other raw payload fields.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .events import Event
from .secrets import read_env
from .sources import altdata, worldmonitor

LIVE_FEED_KEYS = ("QUIVER_API_KEY", "WORLD_MONITOR_KEY")


class LiveFeedConfigError(ValueError):
    """Required live-intel env names are missing."""

    def __init__(self, missing: list[str]):
        self.missing = list(missing)
        super().__init__("feeds --live missing env: " + ", ".join(self.missing))


def missing_live_feed_keys() -> list[str]:
    return [name for name in LIVE_FEED_KEYS if not read_env(name)]


def event_ticker(item: Any) -> str | None:
    if isinstance(item, Event):
        ticker = item.ticker
    elif isinstance(item, dict):
        ticker = item.get("ticker")
    else:
        return None
    if isinstance(ticker, str) and ticker:
        return ticker
    return None


def ticker_histogram(events: list[Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in events:
        ticker = event_ticker(item)
        if ticker is not None:
            counts[ticker] += 1
    return dict(sorted(counts.items()))


def summarize_feed(events: list[Any]) -> dict[str, Any]:
    return {"n": len(events), "tickers": ticker_histogram(events)}


def pull_live_feeds() -> dict[str, Any]:
    missing = missing_live_feed_keys()
    if missing:
        raise LiveFeedConfigError(missing)
    quiver = altdata.live()
    world = worldmonitor.live()
    return {
        "mode": "live-intel",
        "quiver": summarize_feed(list(quiver)),
        "worldmonitor": summarize_feed(list(world)),
        "ok": True,
    }
