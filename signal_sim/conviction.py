"""Declared (unfitted) research-live score' and conviction sizing.

Not alpha. Not a fitted return model. Replay ``rank_candidates`` is unchanged.
Congress and insider are scored separately. Intensity is stamped, not a size input.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .events import Event
from .indicators import (
    NEWS_KINDS,
    UNIVERSE,
    filed_confirm_features,
    filed_lag_features,
    intel_features,
)
from .params import (
    CONVICTION_MAX_GROSS_INVEST,
    CONVICTION_MAX_NAME_FRAC,
    CONVICTION_MIN_SCORE,
    CONVICTION_QUIVER_COUNT_REF,
    CONVICTION_TOP_K,
    CONVICTION_W_CONGRESS,
    CONVICTION_W_GOV,
    CONVICTION_W_INSIDER,
    CONVICTION_W_NEWS,
    CONVICTION_W_QUIVER,
    CONVICTION_W_RECENCY,
    CONVICTION_W_SENT,
    CONVICTION_W_WM,
    HALF_LIFE_HOURS,
    conviction_params,
)
from .sentiment import mean_signed_news

NOTE = (
    "Declared score' research book. Conviction-weighted top-K. "
    "Not fitted. Not alpha. Not a broker fill. Paper only."
)
FORMULA = conviction_params()["formula"]
_EPS = 1e-12


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def filing_lag_hours(row: dict[str, Any] | None) -> float | None:
    """Smallest known insider/congress filing lag. Missing lags stay unknown."""
    if not row:
        return None
    known: list[float] = []
    for key in ("insider_lag_hours", "congress_lag_hours", "lag_h"):
        value = _finite(row.get(key))
        if value is not None and value >= 0:
            known.append(value)
    if not known:
        return None
    return min(known)


def score_prime_terms(
    *,
    news_breakout: float = 0,
    congress_confirm: float = 0,
    insider_confirm: float = 0,
    gov_confirm: float = 0,
    quiver_count: float | None = None,
    intel_brief: float = 0,
    wm_intel: float = 0,
    chokepoint: float = 0,
    lag_h: float | None = None,
    sentiment: float | None = None,
    half_life_hours: float = HALF_LIFE_HOURS,
) -> dict[str, float]:
    """Math Eng score'. Declared weights. Not a fit.

    q_term is 0 when quiver_count is missing or non-positive.
    rec_term is 0 when lag_h is unknown.
    sent_term is the signed mean of news prints only when news_breakout >= 1
    and a tone exists. Unknown tone stays 0 so it does not double-count
    news_term or World Monitor flags.
    """
    news_term = math.log1p(max(0.0, float(news_breakout)))
    count = _finite(quiver_count)
    if count is None or count <= 0:
        q_term = 0.0
    else:
        q_term = math.log1p(count) / math.log1p(CONVICTION_QUIVER_COUNT_REF)
    wm_term = float(intel_brief) + float(wm_intel) + float(chokepoint)
    if lag_h is None:
        rec_term = 0.0
    else:
        rec_term = math.exp(-float(lag_h) / float(half_life_hours))
    signed = _finite(sentiment)
    if signed is None or float(news_breakout) < 1:
        sent_term = 0.0
    else:
        sent_term = max(-1.0, min(1.0, signed))
    score = (
        CONVICTION_W_NEWS * news_term
        + CONVICTION_W_CONGRESS * float(congress_confirm)
        + CONVICTION_W_INSIDER * float(insider_confirm)
        + CONVICTION_W_GOV * float(gov_confirm)
        + CONVICTION_W_QUIVER * q_term
        + CONVICTION_W_WM * wm_term
        + CONVICTION_W_RECENCY * rec_term
        + CONVICTION_W_SENT * sent_term
    )
    return {
        "score": score,
        "news_term": news_term,
        "q_term": q_term,
        "wm_term": wm_term,
        "rec_term": rec_term,
        "sent_term": sent_term,
    }


def quiver_counts(
    events: list[Event],
    when: datetime,
    universe: tuple[str, ...] | None = None,
) -> dict[str, int]:
    """Count source=quiver events at or before ``when``. Missing names stay absent."""
    names = UNIVERSE if universe is None else universe
    allowed = set(names)
    counts: dict[str, int] = {}
    for event in events:
        if event.source != "quiver" or event.ticker not in allowed:
            continue
        if event.observed_at > when:
            continue
        counts[event.ticker] = counts.get(event.ticker, 0) + 1
    return counts


def news_breakouts(
    events: list[Event],
    when: datetime,
    universe: tuple[str, ...] | None = None,
    window_start: datetime | None = None,
) -> dict[str, int]:
    names = UNIVERSE if universe is None else universe
    counts: dict[str, int] = {ticker: 0 for ticker in names}
    for event in events:
        if event.kind not in NEWS_KINDS or event.ticker not in counts:
            continue
        if window_start is not None and event.observed_at < window_start:
            continue
        if event.observed_at > when:
            continue
        counts[event.ticker] += 1
    return counts


def research_rank_rows(
    events: list[Event],
    when: datetime,
    universe: tuple[str, ...] | None = None,
    window_start: datetime | None = None,
) -> list[dict[str, Any]]:
    """score' rows for the operating universe. Congress and insider stay un-lumped."""
    names = UNIVERSE if universe is None else universe
    confirms = filed_confirm_features(events, when, universe=names)
    lags = filed_lag_features(events, when, universe=names)
    intel = intel_features(events, when, universe=names)
    quiver = quiver_counts(events, when, universe=names)
    news = news_breakouts(events, when, universe=names, window_start=window_start)
    tones = mean_signed_news(events, when, universe=names)
    rows: list[dict[str, Any]] = []
    for ticker in names:
        feat = confirms.get(ticker) or {}
        brief = intel.get(ticker) or {}
        lag_h = filing_lag_hours(lags.get(ticker))
        q_count = quiver.get(ticker)
        terms = score_prime_terms(
            news_breakout=news.get(ticker, 0),
            congress_confirm=int(feat.get("congress_confirm") or 0),
            insider_confirm=int(feat.get("insider_confirm") or 0),
            gov_confirm=int(feat.get("gov_confirm") or 0),
            quiver_count=None if q_count is None else float(q_count),
            intel_brief=int(brief.get("intel_brief") or 0),
            wm_intel=int(brief.get("wm_intel") or 0),
            chokepoint=int(brief.get("chokepoint") or 0),
            lag_h=lag_h,
            sentiment=tones.get(ticker),
        )
        if terms["score"] <= _EPS:
            continue
        row: dict[str, Any] = {
            "ticker": ticker,
            "score": terms["score"],
            "news_breakout": int(news.get(ticker, 0)),
            "congress_confirm": int(feat.get("congress_confirm") or 0),
            "insider_confirm": int(feat.get("insider_confirm") or 0),
            "gov_confirm": int(feat.get("gov_confirm") or 0),
            "intel_brief": int(brief.get("intel_brief") or 0),
            "wm_intel": int(brief.get("wm_intel") or 0),
            "chokepoint": int(brief.get("chokepoint") or 0),
            "news_term": terms["news_term"],
            "q_term": terms["q_term"],
            "wm_term": terms["wm_term"],
            "rec_term": terms["rec_term"],
            "sent_term": terms["sent_term"],
        }
        if q_count is not None:
            row["quiver_count"] = int(q_count)
        if ticker in tones:
            row["sentiment"] = tones[ticker]
        if lag_h is not None:
            row["lag_h"] = lag_h
            lag_row = lags.get(ticker) or {}
            if "insider_lag_hours" in lag_row:
                row["insider_lag_hours"] = lag_row["insider_lag_hours"]
            if "congress_lag_hours" in lag_row:
                row["congress_lag_hours"] = lag_row["congress_lag_hours"]
        rows.append(row)
    return sorted(rows, key=lambda item: (-float(item["score"]), str(item["ticker"])))


def conviction_targets(
    rows: list[dict[str, Any]],
    *,
    horizon_hours: float,
    max_gross_invest: float | None = None,
    max_gross_frac: float | None = None,
    max_name_frac: float = CONVICTION_MAX_NAME_FRAC,
    top_k: int = CONVICTION_TOP_K,
    min_score: float = CONVICTION_MIN_SCORE,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """K = top names by score'; one-lever research-live gross.

    target_frac_i = min(max_name_frac, max_gross_invest * score_i / sum_K).
    No post-hoc shrink of every name. Locked replay ``max_gross_frac`` stays
    on the mark book; ``max_gross_frac`` here is only an alias for tests.
    """
    if horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive")
    if max_gross_invest is None:
        max_gross_invest = (
            float(max_gross_frac) if max_gross_frac is not None else CONVICTION_MAX_GROSS_INVEST
        )
    if max_gross_invest <= 0:
        raise ValueError("max_gross_invest must be positive")
    if max_name_frac <= 0:
        raise ValueError("max_name_frac must be positive")
    if top_k < 1:
        raise ValueError("top_k must be a positive integer")
    skipped: list[dict[str, str]] = []
    eligible: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row["ticker"])
        score = _finite(row.get("score"))
        if score is None or score < float(min_score):
            skipped.append({"ticker": ticker, "reason": "below_min_score"})
            continue
        eligible.append(row)
    eligible.sort(key=lambda item: (-float(item["score"]), str(item["ticker"])))
    kept = eligible[:top_k]
    for row in eligible[top_k:]:
        skipped.append({"ticker": str(row["ticker"]), "reason": "outside_top_k"})
    total = math.fsum(float(row["score"]) for row in kept)
    targets: list[dict[str, Any]] = []
    gross = 0.0
    for row in kept:
        ticker = str(row["ticker"])
        score = float(row["score"])
        if total <= _EPS:
            skipped.append({"ticker": ticker, "reason": "non_positive_target"})
            continue
        frac = min(float(max_name_frac), float(max_gross_invest) * score / total)
        if frac <= _EPS:
            skipped.append({"ticker": ticker, "reason": "non_positive_target"})
            continue
        if frac - max_name_frac > _EPS:
            skipped.append({"ticker": ticker, "reason": "max_name_frac"})
            continue
        if gross + frac - max_gross_invest > _EPS:
            skipped.append({"ticker": ticker, "reason": "gross_frac_cap"})
            continue
        target = dict(row)
        target["target_frac"] = frac
        target["side"] = str(row.get("side") or "buy")
        target["horizon_hours"] = float(row.get("horizon_hours") or horizon_hours)
        targets.append(target)
        gross += frac
    return targets, skipped


def equal_weight_targets(
    rows: list[dict[str, Any]],
    *,
    horizon_hours: float,
    max_gross_invest: float | None = None,
    max_gross_frac: float | None = None,
    max_name_frac: float = CONVICTION_MAX_NAME_FRAC,
    top_k: int = CONVICTION_TOP_K,
    min_score: float = CONVICTION_MIN_SCORE,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Naive equal top-K under the same paper caps. Not fitted. Not alpha."""
    if horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive")
    if max_gross_invest is None:
        max_gross_invest = (
            float(max_gross_frac) if max_gross_frac is not None else CONVICTION_MAX_GROSS_INVEST
        )
    if max_gross_invest <= 0:
        raise ValueError("max_gross_invest must be positive")
    if max_name_frac <= 0:
        raise ValueError("max_name_frac must be positive")
    if top_k < 1:
        raise ValueError("top_k must be a positive integer")
    skipped: list[dict[str, str]] = []
    eligible: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row["ticker"])
        score = _finite(row.get("score"))
        if score is None or score < float(min_score):
            skipped.append({"ticker": ticker, "reason": "below_min_score"})
            continue
        eligible.append(row)
    eligible.sort(key=lambda item: (-float(item["score"]), str(item["ticker"])))
    kept = eligible[:top_k]
    for row in eligible[top_k:]:
        skipped.append({"ticker": str(row["ticker"]), "reason": "outside_top_k"})
    n_kept = len(kept)
    targets: list[dict[str, Any]] = []
    if n_kept == 0:
        return targets, skipped
    frac = min(float(max_name_frac), float(max_gross_invest) / float(n_kept))
    gross = 0.0
    for row in kept:
        ticker = str(row["ticker"])
        if frac <= _EPS:
            skipped.append({"ticker": ticker, "reason": "non_positive_target"})
            continue
        if gross + frac - max_gross_invest > _EPS:
            skipped.append({"ticker": ticker, "reason": "gross_frac_cap"})
            continue
        target = dict(row)
        target["target_frac"] = frac
        target["side"] = str(row.get("side") or "buy")
        target["horizon_hours"] = float(row.get("horizon_hours") or horizon_hours)
        targets.append(target)
        gross += frac
    return targets, skipped


def paper_name_cap(locked_max_name_frac: float) -> float:
    """Paper research guard. Locked replay max_name_frac may stay 1.0."""
    return min(float(locked_max_name_frac), float(CONVICTION_MAX_NAME_FRAC))


EQUAL_WEIGHT_2026_09_04 = (
    "XLE",
    "MSFT",
    "NFLX",
    "NVDA",
    "AAPL",
    "CMCSA",
    "CVX",
    "DIS",
    "SPY",
    "XOM",
)


def score_features_from_research_artifact(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild score' inputs from a checked-in research JSON.

    Prefer the frozen equal-weight snapshot for the historical A/B. Live dated
    artifacts may already carry conviction ``proposed_book`` targets.
    """
    rank = {str(row["ticker"]): row for row in raw.get("rank") or [] if isinstance(row, dict)}
    confirms = (raw.get("diagnose") or {}).get("confirms") or {}
    quiver = ((raw.get("feeds") or {}).get("quiver") or {}).get("tickers") or {}
    wm_rows = {
        str(row["ticker"]): row
        for row in (raw.get("proposed_book") or {}).get("targets") or []
        if isinstance(row, dict)
    }
    names = []
    for ticker in rank:
        if ticker not in names:
            names.append(ticker)
    for ticker in confirms:
        if ticker not in names:
            names.append(str(ticker))
    rows: list[dict[str, Any]] = []
    for ticker in names:
        ranked = rank.get(ticker) or {}
        feat = confirms.get(ticker) or {}
        wm = wm_rows.get(ticker) or {}
        q_raw = quiver.get(ticker)
        q_count = _finite(q_raw)
        sentiment = _finite(ranked.get("sentiment") if "sentiment" in ranked else wm.get("sentiment"))
        terms = score_prime_terms(
            news_breakout=int(ranked.get("news_breakout") or 0),
            congress_confirm=int(feat.get("congress_confirm") or 0),
            insider_confirm=int(feat.get("insider_confirm") or 0),
            gov_confirm=int(feat.get("gov_confirm") or 0),
            quiver_count=q_count,
            intel_brief=int(wm.get("intel_brief") or 0),
            wm_intel=int(wm.get("wm_intel") or 0),
            chokepoint=int(wm.get("chokepoint") or 0),
            lag_h=None,
            sentiment=sentiment,
        )
        if terms["score"] <= _EPS:
            continue
        row = {
            "ticker": ticker,
            "score": terms["score"],
            "news_breakout": int(ranked.get("news_breakout") or 0),
            "congress_confirm": int(feat.get("congress_confirm") or 0),
            "insider_confirm": int(feat.get("insider_confirm") or 0),
            "gov_confirm": int(feat.get("gov_confirm") or 0),
            "intel_brief": int(wm.get("intel_brief") or 0),
            "wm_intel": int(wm.get("wm_intel") or 0),
            "chokepoint": int(wm.get("chokepoint") or 0),
            **terms,
        }
        if q_count is not None:
            row["quiver_count"] = int(q_count)
        if sentiment is not None:
            row["sentiment"] = sentiment
        rows.append(row)
    return sorted(rows, key=lambda item: (-float(item["score"]), str(item["ticker"])))


def compare_equal_weight_book(
    raw: dict[str, Any],
    *,
    horizon_hours: float = 34.75,
    before: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Print-only A/B versus the frozen equal-weight 2026-09-04 book. No broker POST.

    ``before`` is the historical equal-weight stub, not ``raw["proposed_book"]``.
    Live ``research --live`` writes today's conviction book to the dated ops
    artifact; keep the equal-weight baseline in
    ``docs/research/2026-09-04-equal-weight.json``.
    """
    baseline = list(before if before is not None else EQUAL_WEIGHT_2026_09_04)
    rows = score_features_from_research_artifact(raw)
    targets, skipped = conviction_targets(rows, horizon_hours=horizon_hours)
    after = [row["ticker"] for row in targets]
    return {
        "before": baseline,
        "after": after,
        "enter": [ticker for ticker in after if ticker not in set(baseline)],
        "exit": [ticker for ticker in baseline if ticker not in set(after)],
        "targets": targets,
        "skipped": skipped,
        "nvda_frac": next((row["target_frac"] for row in targets if row["ticker"] == "NVDA"), None),
        "xle_frac": next((row["target_frac"] for row in targets if row["ticker"] == "XLE"), None),
        "conviction": conviction_params(),
        "note": NOTE,
    }
