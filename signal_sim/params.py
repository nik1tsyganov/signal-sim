"""Declared operate constants loaded from the checked-in manifest.

shadow / walkforward / drift / intensity / replay defaults all read these
values. Not fitted. Not searched.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "params.json"


def load_params(path: Path | None = None) -> dict[str, Any]:
    raw = json.loads((path or MANIFEST_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("params manifest must be an object")
    return raw


_PARAMS = load_params()
NOTE = str(_PARAMS.get("note") or "Declared constants. Not fitted. Not searched.")
HALF_LIFE_HOURS = float(_PARAMS["half_life_hours"])
MIN_RELATIVE_STATE = float(_PARAMS["min_relative_state"])
HAWKES_BASELINE = float(_PARAMS["hawkes_baseline"])
HAWKES_EXCITATION = float(_PARAMS["hawkes_excitation"])
HAWKES_DECAY = float(_PARAMS["hawkes_decay"])
PLACEBO_SEED = int(_PARAMS["placebo_seed"])
COST_BPS = float(_PARAMS["cost_bps"])
DECISION_DELAY_HOURS = float(_PARAMS["decision_delay_hours"])
STARTING_CASH = float(_PARAMS["starting_cash"])
MAX_DRAWDOWN = float(_PARAMS["max_drawdown"])
MAX_GROSS_FRAC = float(_PARAMS["max_gross_frac"])
MAX_NAME_FRAC = float(_PARAMS["max_name_frac"])
_CONVICTION = _PARAMS.get("conviction")
if not isinstance(_CONVICTION, dict):
    _CONVICTION = {}
CONVICTION_NOTE = str(
    _CONVICTION.get("note")
    or "Declared research-live score' weights. Not fitted. Not searched. Not alpha."
)
CONVICTION_MAX_NAME_FRAC = float(_CONVICTION.get("max_name_frac", 0.2))
CONVICTION_MAX_GROSS_INVEST = float(_CONVICTION.get("max_gross_invest", 0.8))
CONVICTION_TOP_K = int(_CONVICTION.get("top_k", 10))
CONVICTION_MIN_SCORE = float(_CONVICTION.get("min_score", 1.0))
CONVICTION_TRIM_BAND = float(_CONVICTION.get("trim_band", 0.02))
CONVICTION_DECAY_FLOOR = float(_CONVICTION.get("decay_floor", 0.5))
CONVICTION_SOFT_STOP = float(_CONVICTION.get("soft_stop", 0.08))
CONVICTION_W_NEWS = float(_CONVICTION.get("w_news", 0.75))
CONVICTION_W_CONGRESS = float(_CONVICTION.get("w_congress", 3.0))
CONVICTION_W_INSIDER = float(_CONVICTION.get("w_insider", 3.0))
CONVICTION_W_GOV = float(_CONVICTION.get("w_gov", 2.0))
CONVICTION_W_QUIVER = float(_CONVICTION.get("w_quiver", 3.0))
CONVICTION_W_WM = float(_CONVICTION.get("w_wm", 2.0))
CONVICTION_W_RECENCY = float(_CONVICTION.get("w_recency", 2.0))
CONVICTION_W_SENT = float(_CONVICTION.get("w_sent", 0.5))
CONVICTION_QUIVER_COUNT_REF = float(_CONVICTION.get("quiver_count_ref", 20))
CONVICTION_SENTIMENT_CAP_N = int(_CONVICTION.get("sentiment_cap_n", 20))


def frozen_operate_params() -> dict[str, Any]:
    return {
        "half_life_hours": HALF_LIFE_HOURS,
        "min_relative_state": MIN_RELATIVE_STATE,
        "placebo_seed": PLACEBO_SEED,
        "hawkes_baseline": HAWKES_BASELINE,
        "hawkes_excitation": HAWKES_EXCITATION,
        "hawkes_decay": HAWKES_DECAY,
        "cost_bps": COST_BPS,
        "decision_delay_hours": DECISION_DELAY_HOURS,
        "starting_cash": STARTING_CASH,
        "max_drawdown": MAX_DRAWDOWN,
        "max_gross_frac": MAX_GROSS_FRAC,
        "max_name_frac": MAX_NAME_FRAC,
        "note": NOTE,
    }


def params_sha256(values: dict[str, Any] | None = None) -> str:
    payload = frozen_operate_params() if values is None else values
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def operate_stamp() -> dict[str, Any]:
    frozen = frozen_operate_params()
    return {"params": frozen, "params_sha256": params_sha256(frozen)}


def conviction_params() -> dict[str, Any]:
    """Declared score' weights for the research-live book. Not in the locked digest."""
    return {
        "note": CONVICTION_NOTE,
        "max_name_frac": CONVICTION_MAX_NAME_FRAC,
        "max_gross_invest": CONVICTION_MAX_GROSS_INVEST,
        "cash_reserve_frac": round(max(0.0, 1.0 - CONVICTION_MAX_GROSS_INVEST), 4),
        "top_k": CONVICTION_TOP_K,
        "min_score": CONVICTION_MIN_SCORE,
        "trim_band": CONVICTION_TRIM_BAND,
        "decay_floor": CONVICTION_DECAY_FLOOR,
        "soft_stop": CONVICTION_SOFT_STOP,
        "sell_priority": "soft_stop >= horizon_exit >= score_decay >= trim",
        "w_news": CONVICTION_W_NEWS,
        "w_congress": CONVICTION_W_CONGRESS,
        "w_insider": CONVICTION_W_INSIDER,
        "w_gov": CONVICTION_W_GOV,
        "w_quiver": CONVICTION_W_QUIVER,
        "w_wm": CONVICTION_W_WM,
        "w_recency": CONVICTION_W_RECENCY,
        "w_sent": CONVICTION_W_SENT,
        "quiver_count_ref": CONVICTION_QUIVER_COUNT_REF,
        "sentiment_cap_n": CONVICTION_SENTIMENT_CAP_N,
        "half_life_hours": HALF_LIFE_HOURS,
        "formula": (
            "score' = 0.75*log1p(news_breakout) + 3.0*congress_confirm + "
            "3.0*insider_confirm + 2.0*gov_confirm + 3.0*log1p(quiver_count)/log1p(20) "
            "+ 2.0*(intel_brief+wm_intel+chokepoint) + 2.0*exp(-lag_h/half_life_hours) "
            "+ 0.5*sent_term"
        ),
    }
