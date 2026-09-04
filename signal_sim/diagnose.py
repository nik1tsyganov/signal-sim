"""Hawkes intensity and online cluster diagnostics.

This is not a ranking input, not an order path, and not a return.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .clusters import online_clusters
from .events import Event
from .hawkes import intensity_map, log_likelihood


def fixture_diagnostics(
    events: list[Event],
    *,
    decision_at: datetime | None = None,
) -> dict[str, Any]:
    """Diagnose fixture prints in the same decision window replay uses."""
    if not events:
        raise ValueError("diagnose requires at least one fixture event")
    if decision_at is None:
        from .sim import load_mark_book

        decision_at = load_mark_book()["decision_at"]
    window = [event for event in events if event.observed_at <= decision_at]
    if not window:
        raise ValueError("diagnose requires at least one fixture event at or before decision_at")
    intensities = intensity_map(window, decision_at)
    clusters = online_clusters(window, decision_at)
    from .indicators import filed_lag_features, intel_features, trendradar_features
    from .sources.worldmonitor import load_recorded

    recorded = [event for event in load_recorded() if event.observed_at <= decision_at]
    intel = intel_features(window + recorded, decision_at)
    hotspot = trendradar_features(window, decision_at)
    lags = filed_lag_features(window, decision_at)
    return {
        "mode": "local-paper-diagnose",
        "note": (
            "Diagnostics only. Cut at default mark-book decision_at, the same window "
            "replay uses. Prints first seen after that decision are excluded. "
            "Not a ranking input and not a return."
        ),
        "when": decision_at.isoformat().replace("+00:00", "Z"),
        "decision_at": decision_at.isoformat().replace("+00:00", "Z"),
        "cut": "decision_at",
        "intensity": intensities,
        "intel": intel,
        "trendradar": hotspot,
        "filing_lags": lags,
        "online_clusters": clusters,
        "hawkes_log_likelihood": log_likelihood(window, end=decision_at),
        "stats": {
            "n_events": len(window),
            "n_events_after_decision": len(events) - len(window),
            "n_clusters": len(clusters),
            "max_cluster_size": max((row["size"] for row in clusters), default=0),
            "n_intel": sum(1 for row in intel.values() if row.get("intel_brief")),
            "n_trendradar": sum(1 for row in hotspot.values() if row.get("trendradar")),
            "n_filing_lags": len(lags),
        },
    }
