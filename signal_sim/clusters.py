"""Online news clusters rebuilt at a decision time.

This is the data-gate cluster replay in docs/paper-trading-and-quant.md.
It is a diagnostic, not a ranking rule and not an order path.
A cluster cannot gain members observed after the simulated decision.
"""

from __future__ import annotations

from datetime import datetime

from .events import Event
from .indicators import NEWS_KINDS, UNIVERSE


def online_clusters(
    events: list[Event],
    when: datetime,
    universe: tuple[str, ...] | None = None,
) -> list[dict[str, object]]:
    allowed = set(UNIVERSE if universe is None else universe)
    groups: dict[tuple[str, str], dict[str, object]] = {}
    for event in events:
        if event.kind not in NEWS_KINDS or event.ticker not in allowed:
            continue
        if event.observed_at > when:
            continue
        day = event.observed_at.date().isoformat()
        key = (event.ticker, day)
        row = groups.get(key)
        if row is None:
            groups[key] = {
                "ticker": event.ticker,
                "day": day,
                "size": 1,
                "first_seen_at": event.observed_at,
                "last_seen_at": event.observed_at,
            }
            continue
        row["size"] = int(row["size"]) + 1
        if event.observed_at < row["first_seen_at"]:
            row["first_seen_at"] = event.observed_at
        if event.observed_at > row["last_seen_at"]:
            row["last_seen_at"] = event.observed_at
    rendered = []
    for row in sorted(groups.values(), key=lambda item: (str(item["ticker"]), str(item["day"]))):
        rendered.append(
            {
                "ticker": row["ticker"],
                "day": row["day"],
                "size": row["size"],
                "first_seen_at": row["first_seen_at"].isoformat().replace("+00:00", "Z"),
                "last_seen_at": row["last_seen_at"].isoformat().replace("+00:00", "Z"),
            }
        )
    return rendered
