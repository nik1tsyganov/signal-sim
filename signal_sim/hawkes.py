"""Pooled marked exponential Hawkes intensity using observed event times."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Iterable

from .events import Event
from .indicators import CONFIRM_KINDS, NEWS_KINDS, UNIVERSE
from .params import HAWKES_BASELINE, HAWKES_DECAY, HAWKES_EXCITATION


BASELINE = HAWKES_BASELINE
EXCITATION = HAWKES_EXCITATION
DECAY = HAWKES_DECAY
_SECONDS_PER_HOUR = 3600.0
_RANK_FEATURES = ("news_breakout", "insider_confirm")


def _parameters(baseline: float, alpha: float, beta: float) -> None:
    if not math.isfinite(baseline) or baseline <= 0:
        raise ValueError("baseline must be finite and positive")
    if not math.isfinite(alpha) or alpha < 0:
        raise ValueError("alpha must be finite and non-negative")
    if not math.isfinite(beta) or beta <= 0:
        raise ValueError("beta must be finite and positive")


def _mark(event: Event) -> float:
    feature_mark = math.fsum(
        float(value)
        for name in _RANK_FEATURES
        if not isinstance((value := getattr(event, name, None)), bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )
    if feature_mark:
        return feature_mark
    if event.kind in NEWS_KINDS or event.kind in CONFIRM_KINDS:
        return 1.0
    return 0.0


def _hours(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / _SECONDS_PER_HOUR


def _relevant(events: Iterable[Event]) -> list[tuple[datetime, float]]:
    return sorted(
        (event.observed_at, mark)
        for event in events
        if event.ticker in UNIVERSE and (mark := _mark(event)) > 0
    )


def fixture_intensity(fixtures=None):
    """Declared Hawkes intensity cut at the mark-book decision_at.

    Same window as diagnose / rank / replay. Not a fit. Not a ranking input.
    """
    from pathlib import Path

    from .fixture_load import load_fixture_events
    from .params import operate_stamp
    from .sim import load_mark_book

    root = Path(fixtures) if fixtures is not None else Path(__file__).resolve().parent.parent / "fixtures"
    events = load_fixture_events(root)
    decision_at = load_mark_book()["decision_at"]
    window = [event for event in events if event.observed_at <= decision_at]
    stamp = operate_stamp()
    return {
        "mode": "local-paper-intensity",
        "note": (
            "Declared Hawkes intensity cut at mark-book decision_at, the same "
            "window replay uses. Prints first seen after that decision are excluded. "
            "Not a fit. Not a ranking input."
        ),
        "cut": "decision_at",
        "decision_at": decision_at.isoformat().replace("+00:00", "Z"),
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "intensity": intensity_map(window, decision_at),
        "stats": {
            "n_events": len(window),
            "n_events_after_decision": len(events) - len(window),
        },
    }


def intensity_map(
    events: Iterable[Event],
    when: datetime,
    *,
    baseline: float = BASELINE,
    alpha: float = EXCITATION,
    beta: float = DECAY,
) -> dict[str, float]:
    """Declared-parameter intensity per universe ticker. Not a fit."""
    material = list(events)
    return {
        ticker: intensity_at(
            (event for event in material if event.ticker == ticker),
            when,
            baseline=baseline,
            alpha=alpha,
            beta=beta,
        )
        for ticker in UNIVERSE
    }


def intensity_size_scale(intensity: float, baseline: float = BASELINE) -> float:
    """Declared risk overlay: intensity above baseline shrinks size, never raises it."""
    if not math.isfinite(intensity) or intensity <= 0:
        return 1.0
    return min(1.0, baseline / intensity)


def intensity_at(
    events: Iterable[Event],
    when: datetime,
    *,
    baseline: float = BASELINE,
    alpha: float = EXCITATION,
    beta: float = DECAY,
) -> float:
    """Return hourly conditional intensity at ``when`` using only prior observations."""
    _parameters(baseline, alpha, beta)
    excitation = math.fsum(
        alpha * mark * math.exp(-beta * _hours(when, observed_at))
        for observed_at, mark in _relevant(events)
        if observed_at < when
    )
    return float(baseline + excitation)


def log_likelihood(
    events: Iterable[Event],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    baseline: float = BASELINE,
    alpha: float = EXCITATION,
    beta: float = DECAY,
) -> float:
    """Return the exponential Hawkes log-likelihood over an observed-time window."""
    _parameters(baseline, alpha, beta)
    observations = _relevant(events)
    if not observations:
        if start is None or end is None:
            return 0.0
    else:
        start = observations[0][0] if start is None else start
        end = observations[-1][0] if end is None else end
    if start is None or end is None or end < start:
        raise ValueError("end must not be before start")

    arrivals = [observed_at for observed_at, _mark_value in observations if start <= observed_at <= end]
    log_terms = math.fsum(
        math.log(_intensity_from_observations(observations, arrival, baseline, alpha, beta))
        for arrival in arrivals
    )
    integral = baseline * _hours(end, start)
    integral += math.fsum(
        alpha
        * mark
        / beta
        * (
            math.exp(-beta * max(0.0, _hours(start, observed_at)))
            - math.exp(-beta * _hours(end, observed_at))
        )
        for observed_at, mark in observations
        if observed_at < end
    )
    return float(log_terms - integral)


def _intensity_from_observations(
    observations: list[tuple[datetime, float]],
    when: datetime,
    baseline: float,
    alpha: float,
    beta: float,
) -> float:
    return baseline + math.fsum(
        alpha * mark * math.exp(-beta * _hours(when, observed_at))
        for observed_at, mark in observations
        if observed_at < when
    )
