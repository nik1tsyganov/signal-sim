"""Live intel pull for counts and ticker histograms only.

Does not dump person names, headlines, URLs, or other raw payload fields.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .events import Event, EventValidationError
from .indicators import UNIVERSE
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


def fetch_live_feed_payloads() -> tuple[list[Any], list[Any]]:
    """Same Quiver / World Monitor pull as feeds --live. No PII summary."""
    missing = missing_live_feed_keys()
    if missing:
        raise LiveFeedConfigError(missing)
    return list(altdata.live()), list(worldmonitor.live())


def intensity_event(item: Any) -> Event | None:
    """Coerce a live feed row into a Hawkes Event. Skip rows that are not events."""
    if isinstance(item, Event):
        return item if item.ticker in UNIVERSE else None
    if not isinstance(item, dict):
        return None
    ticker = item.get("ticker")
    if ticker not in UNIVERSE:
        return None
    payload = dict(item)
    payload.setdefault("entities", [])
    payload.setdefault("headline", "")
    payload.setdefault("url", "")
    payload.setdefault("confidence", 0.0)
    payload.setdefault("raw_ref", f"live:{ticker}")
    try:
        event = Event.from_dict(payload)
    except EventValidationError:
        return None
    return event


def live_events_for_intensity(quiver: list[Any], world: list[Any]) -> list[Event]:
    events: list[Event] = []
    for item in list(quiver) + list(world):
        event = intensity_event(item)
        if event is not None:
            events.append(event)
    return events


def pull_live_feeds() -> dict[str, Any]:
    quiver, world = fetch_live_feed_payloads()
    return {
        "mode": "live-intel",
        "quiver": summarize_feed(quiver),
        "worldmonitor": summarize_feed(world),
        "ok": True,
    }
