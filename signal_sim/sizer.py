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
        requested = row.get("target_frac")
        frac = float(size_frac) if requested is None else float(requested)
        if row.get("intensity_scale") is not None:
            frac *= min(1.0, float(row["intensity_scale"]))
        if frac <= 0:
            skipped.append({"ticker": ticker, "reason": "non_positive_target"})
            continue
        if frac > max_name_frac:
            skipped.append({"ticker": ticker, "reason": "max_name_frac"})
            continue
        if gross + frac > max_gross_frac:
            skipped.append({"ticker": ticker, "reason": "gross_frac_cap"})
            continue
        target = {
            "ticker": ticker,
            "target_frac": frac,
            "side": str(row.get("side") or "buy"),
            "horizon_hours": float(row.get("horizon_hours") or horizon_hours),
            "score": row.get("score"),
        }
        for key in ("cluster_size", "n_clusters", "state", "intensity", "intensity_scale"):
            if key in row:
                target[key] = row[key]
        targets.append(target)
        gross += frac
    return targets, skipped
