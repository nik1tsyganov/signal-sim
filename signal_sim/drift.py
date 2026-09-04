"""Fixture-only online news-cluster drift stub (docs method #3).

Not a fitted model. Not alpha. Emits a signed target book from
online_clusters at the mark-book decision_at. Rank stays unchanged.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from .clusters import online_clusters
from .events import Event
from .fixture_load import load_fixture_events
from .indicators import (
    filed_confirm_features,
    filed_lag_features,
    intel_features,
    trendradar_features,
)
from .params import HALF_LIFE_HOURS, MIN_RELATIVE_STATE
from .sizer import MAX_GROSS_FRAC
NOTE = (
    "Stub. Fixture cluster count only. Declared half-life, not a fitted drift. "
    "Not alpha. Target book for the paper ledger."
)
INTENSITY_NOTE = (
    "Declared Hawkes intensity feature from diagnose/intensity_at params. "
    "Not a fit. Risk overlay may shrink size; it never raises it."
)


def _aware(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def cluster_state(events: list[Event], when: datetime) -> dict[str, dict[str, Any]]:
    """Recency-weighted cluster size per ticker. Sign is +1: fixtures have no negative tone."""
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in online_clusters(events, when):
        ticker = str(row["ticker"])
        last = _aware(str(row["last_seen_at"]))
        age_hours = max(0.0, (when - last).total_seconds() / 3600.0)
        decay = 0.5 ** (age_hours / HALF_LIFE_HOURS)
        signed = float(row["size"]) * decay
        current = by_ticker.get(ticker)
        if current is None:
            by_ticker[ticker] = {
                "ticker": ticker,
                "state": signed,
                "cluster_size": int(row["size"]),
                "n_clusters": 1,
                "last_seen_at": row["last_seen_at"],
            }
            continue
        current["state"] = float(current["state"]) + signed
        current["cluster_size"] = int(current["cluster_size"]) + int(row["size"])
        current["n_clusters"] = int(current["n_clusters"]) + 1
        if str(row["last_seen_at"]) > str(current["last_seen_at"]):
            current["last_seen_at"] = row["last_seen_at"]
    return by_ticker


def drift_targets(
    events: list[Event],
    *,
    when: datetime,
    size_frac: float,
    horizon_hours: float,
    intensities: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Turn cluster state into a signed target book. Not a return forecast.

    Gross and name caps stay with the paper sizer so no_mark names do not
    consume budget before replay refuses them.
    """
    states = cluster_state(events, when)
    peak = max((abs(float(row["state"])) for row in states.values()), default=0.0)
    confirms = filed_confirm_features(events, when)
    lags = filed_lag_features(events, when)
    intel = intel_features(events, when)
    hotspot = trendradar_features(events, when)
    ranked: list[dict[str, Any]] = []
    for row in sorted(states.values(), key=lambda item: (-abs(float(item["state"])), str(item["ticker"]))):
        if peak <= 0:
            continue
        signed = float(row["state"]) / peak
        if abs(signed) < MIN_RELATIVE_STATE:
            continue
        target = {
            "ticker": row["ticker"],
            "score": float(row["state"]),
            "target_frac": size_frac * abs(signed),
            "side": "buy" if signed >= 0 else "sell",
            "horizon_hours": float(horizon_hours),
            "cluster_size": row["cluster_size"],
            "n_clusters": row["n_clusters"],
            "state": float(row["state"]),
            "insider_confirm": 0,
            "congress_confirm": 0,
            "gov_confirm": 0,
            "insider_lag_hours": None,
            "congress_lag_hours": None,
            "intel_brief": 0,
            "wm_intel": 0,
            "chokepoint": 0,
            "trendradar": 0,
        }
        row_confirms = confirms.get(str(row["ticker"]))
        if row_confirms:
            target["insider_confirm"] = row_confirms["insider_confirm"]
            target["congress_confirm"] = row_confirms["congress_confirm"]
            target["gov_confirm"] = row_confirms.get("gov_confirm", 0)
        row_lags = lags.get(str(row["ticker"]))
        if row_lags:
            target["insider_lag_hours"] = row_lags.get("insider_lag_hours")
            target["congress_lag_hours"] = row_lags.get("congress_lag_hours")
        row_intel = intel.get(str(row["ticker"]))
        if row_intel:
            target["intel_brief"] = row_intel["intel_brief"]
            target["wm_intel"] = row_intel["wm_intel"]
            target["chokepoint"] = row_intel["chokepoint"]
        row_tr = hotspot.get(str(row["ticker"]))
        if row_tr:
            target["trendradar"] = row_tr["trendradar"]
        if intensities is not None:
            from .hawkes import intensity_size_scale

            intensity = float(intensities.get(str(row["ticker"]), 0.0))
            target["intensity"] = intensity
            target["intensity_scale"] = intensity_size_scale(intensity)
        ranked.append(target)
    return ranked


def fixture_drift_book(
    fixtures: Path | None = None,
    mark_book_path: Path | str | None = None,
    mark_book: dict[str, Any] | None = None,
    *,
    intensity: bool = False,
    extra_events: list[Event] | None = None,
    intensity_when: datetime | None = None,
) -> dict[str, Any]:
    """Score fixture prints at the mark-book decision. No vendor bars.

    extra_events and intensity_when only feed the Hawkes overlay. Cluster
    state stays on fixture prints at decision_at.
    """
    from .sim import load_mark_book

    root = fixtures if fixtures is not None else Path(__file__).resolve().parent.parent / "fixtures"
    book = mark_book if mark_book is not None else load_mark_book(mark_book_path)
    events = [event for event in load_fixture_events(root) if event.observed_at <= book["decision_at"]]
    from .sources.worldmonitor import load_recorded

    recorded = [
        event for event in load_recorded(root) if event.observed_at <= book["decision_at"]
    ]
    feature_events = events + recorded
    horizon_hours = (book["exit_at"] - book["decision_at"]).total_seconds() / 3600.0
    intensities = None
    intensity_cut = "decision_at"
    if intensity:
        from .hawkes import intensity_map

        material = list(events)
        if extra_events:
            material.extend(extra_events)
        when = intensity_when if intensity_when is not None else book["decision_at"]
        intensities = intensity_map(material, when)
        if extra_events or intensity_when is not None:
            intensity_cut = "now"
    targets = drift_targets(
        events,
        when=book["decision_at"],
        size_frac=float(book["size_frac"]),
        horizon_hours=horizon_hours,
        intensities=intensities,
    )
    intel = intel_features(feature_events, book["decision_at"])
    hotspot = trendradar_features(events, book["decision_at"])
    lags = filed_lag_features(events, book["decision_at"])
    confirms = filed_confirm_features(events, book["decision_at"])
    for row in targets:
        feat = intel.get(str(row["ticker"]), {})
        row["intel_brief"] = int(feat.get("intel_brief", 0))
        row["wm_intel"] = int(feat.get("wm_intel", 0))
        row["chokepoint"] = int(feat.get("chokepoint", 0))
        row["trendradar"] = int(hotspot.get(str(row["ticker"]), {}).get("trendradar", 0))
    decision_at = book["decision_at"].isoformat().replace("+00:00", "Z")
    from .params import operate_stamp

    stamp = operate_stamp()
    payload = {
        "mode": "local-paper-drift",
        "note": NOTE,
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "method": "online-news-cluster-drift-stub",
        "half_life_hours": HALF_LIFE_HOURS,
        "min_relative_state": MIN_RELATIVE_STATE,
        "decision_at": decision_at,
        "horizon_hours": horizon_hours,
        "max_gross_frac": float(book.get("max_gross_frac", MAX_GROSS_FRAC)),
        "mark_path": book.get("path"),
        "intel": intel,
        "trendradar": hotspot,
        "confirms": confirms,
        "filing_lags": lags,
        "targets": targets,
    }
    if intensity:
        note = INTENSITY_NOTE
        if extra_events:
            note = (
                INTENSITY_NOTE
                + " Live Quiver/World Monitor events included in the overlay. "
                "Not a fit. Not a ranking input."
            )
        payload["intensity_note"] = note
        payload["intensity"] = intensities
        payload["intensity_cut"] = intensity_cut
    return payload
