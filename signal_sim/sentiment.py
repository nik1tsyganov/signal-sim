"""Cheap signed tone for research-live score'. No LLM firehose.

Vendor/structured polarity when a payload already carries it (Quiver
Sentiment / transaction). Otherwise a tiny declared lexicon on the
in-memory headline. Missing tone is skipped, not invented as +1.

Signed at print time. Cut at decision_at (observed_at <= cut). Not fitted.
Not alpha. Scores stored on the research artifact are numeric only — no
headlines, URLs, or person names.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .events import Event
from .indicators import NEWS_KINDS, UNIVERSE
from .params import CONVICTION_SENTIMENT_CAP_N

NOTE = (
    "Cheap signed tone. Vendor polarity if present; else a tiny declared "
    "lexicon; else skipped. No LLM. Sign may be negative. Not fitted. Not alpha."
)
VENDOR_KEYS = ("Sentiment", "sentiment", "polarity", "tone")
VENDOR_POS = frozenset({"positive", "bullish", "buy", "purchase", "1", "1.0"})
VENDOR_NEG = frozenset({"negative", "bearish", "sell", "sale", "-1", "-1.0"})
POS_WORDS = frozenset(
    {
        "beat",
        "upgrade",
        "award",
        "surge",
        "rally",
        "record",
        "outperform",
        "bullish",
        "purchase",
    }
)
NEG_WORDS = frozenset(
    {
        "miss",
        "downgrade",
        "dump",
        "crash",
        "lawsuit",
        "fraud",
        "probe",
        "recall",
        "bearish",
        "plunge",
        "sale",
    }
)
_EPS = 1e-12


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _clamp_unit(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))


def _vendor_tone(raw: dict[str, Any]) -> float | None:
    for key in VENDOR_KEYS:
        value = raw.get(key)
        if value is None or value == "":
            continue
        number = _finite(value)
        if number is not None:
            return _clamp_unit(number)
        token = str(value).strip().lower()
        if token in VENDOR_POS:
            return 1.0
        if token in VENDOR_NEG:
            return -1.0
    transaction = raw.get("transaction")
    if isinstance(transaction, str):
        token = transaction.strip().lower()
        if token == "purchase":
            return 1.0
        if token == "sale":
            return -1.0
    return None


def _lexicon_tone(headline: str) -> float | None:
    tokens = [part.strip(".,:;!?\"'()[]").lower() for part in headline.split()]
    words = {token for token in tokens if token}
    pos = len(words & POS_WORDS)
    neg = len(words & NEG_WORDS)
    if pos == 0 and neg == 0:
        return None
    return _clamp_unit((pos - neg) / float(pos + neg))


def signed_print(item: Event | dict[str, Any]) -> float | None:
    """Signed mark in [-1, 1] or None when tone is unknown.

    The old cluster-state stub always used +1. Unknown prints stay unknown
    here so missing tone does not inflate score'.
    """
    if isinstance(item, Event):
        vendor = None
        headline = item.headline
    elif isinstance(item, dict):
        vendor = _vendor_tone(item)
        headline = str(item.get("headline") or "")
    else:
        return None
    if vendor is not None:
        return vendor
    return _lexicon_tone(headline)


def mean_signed_news(
    events: list[Event] | list[Any],
    when: datetime,
    universe: tuple[str, ...] | None = None,
) -> dict[str, float]:
    """Mean signed mark on news/intel prints with observed_at <= when.

    Congress/insider confirms stay out so this does not double-count those
    terms. Tickers with no signed print are absent (sent_term stays 0).
    """
    names = UNIVERSE if universe is None else universe
    allowed = set(names)
    buckets: dict[str, list[float]] = {}
    for item in events:
        if isinstance(item, Event):
            if item.kind not in NEWS_KINDS or item.ticker not in allowed:
                continue
            if item.observed_at > when:
                continue
            ticker = item.ticker
        elif isinstance(item, dict):
            ticker = str(item.get("ticker") or "")
            kind = str(item.get("kind") or "news")
            if ticker not in allowed or kind not in NEWS_KINDS:
                continue
            observed = item.get("observed_at")
            if isinstance(observed, datetime):
                stamp = observed
            elif isinstance(observed, str) and observed:
                stamp = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            else:
                continue
            if stamp.tzinfo is None or stamp.utcoffset() is None or stamp > when:
                continue
        else:
            continue
        tone = signed_print(item)
        if tone is None:
            continue
        buckets.setdefault(ticker, []).append(tone)
    return {
        ticker: sum(values) / float(len(values))
        for ticker, values in buckets.items()
        if values
    }


def batch_new_print_tones(
    events: list[Event],
    *,
    until: datetime,
    since: datetime | None = None,
    cap_n: int = CONVICTION_SENTIMENT_CAP_N,
) -> list[dict[str, Any]]:
    """Top-N new news prints since the last research cut. Numeric tone only."""
    if cap_n < 1:
        return []
    scored: list[tuple[datetime, dict[str, Any]]] = []
    for event in events:
        if event.kind not in NEWS_KINDS:
            continue
        if event.observed_at > until:
            continue
        if since is not None and event.observed_at <= since:
            continue
        tone = signed_print(event)
        if tone is None:
            continue
        scored.append(
            (
                event.observed_at,
                {
                    "ticker": event.ticker,
                    "tone": tone,
                    "kind": event.kind,
                    "source": event.source,
                },
            )
        )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [row for _when, row in scored[:cap_n]]


def sentiment_summary(
    mean_by_ticker: dict[str, float],
    new_prints: list[dict[str, Any]],
) -> dict[str, Any]:
    tones = list(mean_by_ticker.values())
    n_neg = sum(1 for value in tones if value < -_EPS)
    n_pos = sum(1 for value in tones if value > _EPS)
    return {
        "note": NOTE,
        "method": "vendor_or_lexicon",
        "llm": False,
        "n_tickers": len(mean_by_ticker),
        "n_scored_prints": len(new_prints),
        "n_negative": n_neg,
        "n_positive": n_pos,
        "mean_by_ticker": dict(sorted(mean_by_ticker.items())),
        "new_prints": new_prints,
    }
