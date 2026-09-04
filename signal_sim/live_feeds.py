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
from .sources.altdata import RESEARCH_DATASETS, as_event
from .universe import is_tradable_ticker, load_liquid_allowlist

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


def fetch_live_feed_payloads(
    *,
    datasets: tuple[str, ...] | None = None,
    accept: tuple[str, ...] | None = None,
) -> tuple[list[Any], list[Any]]:
    """Same Quiver / World Monitor pull as feeds --live. No PII summary."""
    missing = missing_live_feed_keys()
    if missing:
        raise LiveFeedConfigError(missing)
    allowed = accept if accept is not None else load_liquid_allowlist()
    pull_datasets = RESEARCH_DATASETS if datasets is None else datasets
    return (
        list(altdata.live(datasets=pull_datasets, accept=allowed)),
        list(worldmonitor.live(accept=allowed)),
    )


def intensity_event(item: Any, universe: tuple[str, ...] | None = None) -> Event | None:
    """Coerce a live feed row into a Hawkes Event. Skip rows that are not events."""
    allowed = UNIVERSE if universe is None else universe
    if isinstance(item, Event):
        return item if item.ticker in allowed else None
    if not isinstance(item, dict):
        return None
    ticker = item.get("ticker")
    if ticker not in allowed:
        return None
    payload = dict(item)
    if not payload.get("entities"):
        person = payload.get("person")
        payload["entities"] = [str(person)] if person else []
    payload.setdefault("headline", "")
    payload.setdefault("url", "")
    payload.setdefault("confidence", 0.0)
    payload.setdefault("raw_ref", f"live:{ticker}")
    try:
        if payload.get("kind") in {"congress_trade", "insider"} and payload.get("id"):
            return as_event(payload)
        event = Event.from_dict(payload)
    except (EventValidationError, KeyError, TypeError):
        return None
    return event


def live_events_for_intensity(
    quiver: list[Any],
    world: list[Any],
    universe: tuple[str, ...] | None = None,
) -> list[Event]:
    events: list[Event] = []
    for item in list(quiver) + list(world):
        event = intensity_event(item, universe=universe)
        if event is not None:
            events.append(event)
    return events


def strategy_events(
    quiver: list[Any],
    world: list[Any],
    universe: tuple[str, ...] | None = None,
) -> list[Event]:
    """Typed Events for rank / drift / diagnose. Allowlisted names only."""
    allowed = load_liquid_allowlist() if universe is None else universe
    return live_events_for_intensity(quiver, world, universe=allowed)


def pull_live_feeds() -> dict[str, Any]:
    quiver, world = fetch_live_feed_payloads()
    return {
        "mode": "live-intel",
        "quiver": summarize_feed(quiver),
        "worldmonitor": summarize_feed(world),
        "ok": True,
    }


def usable_intel_tickers(events: list[Any]) -> list[str]:
    names: list[str] = []
    for item in events:
        ticker = event_ticker(item)
        if ticker is not None and is_tradable_ticker(ticker) and ticker not in names:
            names.append(ticker)
    return names
