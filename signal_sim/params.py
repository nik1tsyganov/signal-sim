"""Declared operate constants loaded from the checked-in manifest.

shadow / walkforward / drift / intensity / replay defaults all read these
values. Not fitted. Not searched.
"""

from __future__ import annotations

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
        "note": NOTE,
    }
