"""Map ranked candidates to signed paper targets.

This is the execution sizer in docs/paper-trading-and-quant.md, not a new
alpha rule. Each positive-score name gets the configured long size_frac
until the gross cap is reached. Horizon is the fixture decision-to-exit
window, not a fitted holding-time model.
"""

from __future__ import annotations

from typing import Any

MAX_GROSS_FRAC = 1.0


def size_targets(
    candidates: list[dict[str, Any]],
    *,
    size_frac: float,
    horizon_hours: float,
    max_gross_frac: float = MAX_GROSS_FRAC,
    max_name_frac: float = 1.0,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if size_frac <= 0:
        raise ValueError("size_frac must be positive")
    if horizon_hours <= 0:
        raise ValueError("horizon_hours must be positive")
    if max_gross_frac <= 0:
        raise ValueError("max_gross_frac must be positive")
    if max_name_frac <= 0:
        raise ValueError("max_name_frac must be positive")
    targets: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    gross = 0.0
    for row in candidates:
        ticker = str(row["ticker"])
        if size_frac > max_name_frac:
            skipped.append({"ticker": ticker, "reason": "max_name_frac"})
            continue
        if gross + size_frac > max_gross_frac:
            skipped.append({"ticker": ticker, "reason": "gross_frac_cap"})
            continue
        targets.append(
            {
                "ticker": ticker,
                "target_frac": float(size_frac),
                "side": "buy",
                "horizon_hours": float(horizon_hours),
                "score": row.get("score"),
            }
        )
        gross += size_frac
    return targets, skipped
