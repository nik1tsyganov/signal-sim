"""Declared research-live exits. Not fitted. Not alpha.

Priority when more than one rule fires:
    soft_stop >= horizon_exit >= score_decay >= trim

score_decay covers score'_t < min_score OR score'_t / score'_entry < decay_floor.
Horizon uses the entry decision clock: now >= entry_decision_at + horizon_hours.
Soft stop uses decision-time marks only (fixture_mark or paper IEX sizing mark)
versus the paper entry price. No post-decision prints.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Literal

SellReason = Literal[
    "soft_stop",
    "horizon_exit",
    "score_decay",
    "below_min_score",
    "drop_from_book",
    "overweight_band",
]
CLOSE_PRIORITY: tuple[SellReason, ...] = (
    "soft_stop",
    "horizon_exit",
    "score_decay",
    "below_min_score",
    "drop_from_book",
)
NOTE = (
    "Declared paper exits. Priority: soft_stop >= horizon_exit >= "
    "score_decay >= trim. Not fitted. Not alpha."
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
    if not math.isfinite(number):
        return None
    return number


def parse_aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def decision_pnl_frac(
    *,
    entry_px: float | None,
    mark_px: float | None,
    shares: float,
) -> float | None:
    """Paper MTM at the decision mark. Long: (mark-entry)/entry."""
    entry = _finite(entry_px)
    mark = _finite(mark_px)
    if entry is None or mark is None or entry <= _EPS:
        return None
    pnl = (mark - entry) / entry
    if shares < 0:
        pnl = -pnl
    return pnl


def select_close_reason(
    *,
    in_book: bool,
    score: float | None,
    min_score: float | None,
    decay_floor: float | None,
    entry_score: float | None,
    now: datetime | None,
    entry_decision_at: datetime | None,
    horizon_hours: float | None,
    pnl_frac: float | None,
    soft_stop: float | None,
) -> SellReason | None:
    """Highest-priority close. Trim is not a close; the planner emits it later."""
    fired: list[SellReason] = []
    if (
        soft_stop is not None
        and pnl_frac is not None
        and pnl_frac <= -abs(float(soft_stop)) + _EPS
    ):
        fired.append("soft_stop")
    if (
        now is not None
        and entry_decision_at is not None
        and horizon_hours is not None
        and horizon_hours > 0
        and now >= entry_decision_at + timedelta(hours=float(horizon_hours))
    ):
        fired.append("horizon_exit")
    if score is not None:
        if (
            decay_floor is not None
            and entry_score is not None
            and entry_score > _EPS
            and score / entry_score < float(decay_floor) - _EPS
        ):
            fired.append("score_decay")
        if min_score is not None and score < float(min_score) - _EPS:
            fired.append("below_min_score")
    if not in_book:
        fired.append("drop_from_book")
    for reason in CLOSE_PRIORITY:
        if reason in fired:
            return reason
    return None


def sell_clause(reason: SellReason, target_frac: float | None) -> list[str]:
    """Rationale fragments for a sell/close. Exhaustive on SellReason."""
    if reason == "soft_stop":
        return ["close", "soft stop on decision-time MTM"]
    if reason == "horizon_exit":
        return ["close", "horizon exit vs entry decision clock"]
    if reason == "score_decay":
        return ["close", "score' decay vs entry"]
    if reason == "below_min_score":
        return ["close", "score below min_score"]
    if reason == "drop_from_book":
        return ["close leftover", "not in target book"]
    if reason == "overweight_band":
        frac = 0.0 if target_frac is None else float(target_frac)
        return [f"trim to target_frac={frac:g}", "overweight beyond band"]
    unreachable: SellReason = reason
    raise ValueError(f"unhandled sell reason: {unreachable}")
