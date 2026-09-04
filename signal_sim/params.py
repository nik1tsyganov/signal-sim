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
