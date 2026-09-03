"""Hawkes intensity and online cluster diagnostics.

This is not a ranking input, not an order path, and not a return.
"""

from __future__ import annotations

from typing import Any

from .clusters import online_clusters
from .events import Event
from .hawkes import intensity_at, log_likelihood
from .indicators import UNIVERSE


def fixture_diagnostics(events: list[Event]) -> dict[str, Any]:
    if not events:
        raise ValueError("diagnose requires at least one fixture event")
    when = max(event.observed_at for event in events)
    intensities = {
        ticker: intensity_at(
            (event for event in events if event.ticker == ticker),
            when,
        )
        for ticker in UNIVERSE
    }
    clusters = online_clusters(events, when)
    return {
        "mode": "local-paper-diagnose",
        "note": "Diagnostics only. Not a ranking input and not a return.",
        "when": when.isoformat().replace("+00:00", "Z"),
        "intensity": intensities,
        "online_clusters": clusters,
        "hawkes_log_likelihood": log_likelihood(events),
        "stats": {
            "n_events": len(events),
            "n_clusters": len(clusters),
            "max_cluster_size": max((row["size"] for row in clusters), default=0),
        },
    }
