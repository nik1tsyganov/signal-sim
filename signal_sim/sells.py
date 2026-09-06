"""Declared research-live exits. Not fitted. Not alpha.

Priority when more than one rule fires:
    soft_stop >= horizon_exit (hard) >= score_decay >= trim / drop_from_book

Hard exits (soft_stop, horizon_exit) still sell when mark-to-market is red.
Discretionary / rank exits (score_decay, below_min_score, drop_from_book,
overweight trim) do not crystallize a loss versus paper avg entry beyond
``min_realize_loss_bps``. Flat or green MTM may realize a gain or scratch.

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
    "Declared paper exits. Priority: soft_stop >= horizon_exit (hard) >= "
    "score_decay >= trim. Discretionary sells hold underwater. Not fitted. "
    "Not alpha."
)
HARD_CLOSE_REASONS: frozenset[SellReason] = frozenset({"soft_stop", "horizon_exit"})
DISCRETIONARY_SELL_REASONS: frozenset[SellReason] = frozenset(
    {"score_decay", "below_min_score", "drop_from_book", "overweight_band"}
)
SELL_BLOCKED_UNDERWATER = "underwater_hold"
HOLD_UNDERWATER = "hold_underwater"
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


def paper_row_pnl_frac(row: dict[str, Any] | None) -> float | None:
    """MTM from a paper position row (avg entry vs mark). Long default."""
    if not isinstance(row, dict):
        return None
    entry = _finite(row.get("avg_entry_price"))
    if entry is None:
        entry = _finite(row.get("entry_px"))
    mark = _finite(row.get("current_price") or row.get("mark_px"))
    qty = _finite(row.get("qty") if row.get("qty") is not None else row.get("shares"))
    if mark is None:
        market_value = _finite(row.get("market_value"))
        if market_value is not None and qty is not None and abs(qty) > _EPS:
            mark = abs(market_value / qty)
    side = str(row.get("side") or "long").strip().lower()
    if qty is None:
        shares = -1.0 if side == "short" else 1.0
    elif side == "short":
        shares = -abs(qty)
    else:
        shares = abs(qty)
    return decision_pnl_frac(entry_px=entry, mark_px=mark, shares=shares)


def underwater_vs_entry(
    pnl_frac: float | None,
    min_realize_loss_bps: float = 0.0,
) -> bool:
    """True when decision-time MTM is red beyond the declared noise buffer.

    Unknown MTM (missing entry or mark) is not treated as underwater.
    """
    if pnl_frac is None:
        return False
    buffer = abs(float(min_realize_loss_bps)) / 10000.0
    return pnl_frac < -buffer - _EPS


def is_hard_exit(reason: SellReason) -> bool:
    return reason in HARD_CLOSE_REASONS


def is_discretionary_sell(reason: SellReason) -> bool:
    return reason in DISCRETIONARY_SELL_REASONS


def allow_sell(
    *,
    reason: SellReason,
    pnl_frac: float | None,
    min_realize_loss_bps: float = 0.0,
) -> bool:
    """Hard exits always fire. Discretionary sells need flat/green MTM."""
    if reason in HARD_CLOSE_REASONS:
        return True
    if reason in DISCRETIONARY_SELL_REASONS:
        return not underwater_vs_entry(pnl_frac, min_realize_loss_bps)
    unreachable: SellReason = reason
    raise ValueError(f"unhandled sell reason: {unreachable}")


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
