"""Operating universe: frozen fixture names plus capped live intel names.

The checked-in fixture list stays the offline default. Live research and
rebalance may union that set with allowlisted US-listed liquid tickers that
actually appear in Quiver / World Monitor events. Garbage symbols never enter.
This does not invent prices and is not a live-money universe.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .indicators import UNIVERSE

_ALLOWLIST_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "liquid_allowlist.json"
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
INTEL_TOP_N = 12


def is_tradable_ticker(value: Any) -> bool:
    """ASCII uppercase 1-5 letters. No dots, warrants, or crypto pairs."""
    return isinstance(value, str) and bool(_TICKER_RE.fullmatch(value))


def load_liquid_allowlist(path: Path | None = None) -> tuple[str, ...]:
    """Load the extra liquid US names. Always includes the fixture universe."""
    raw = json.loads((path or _ALLOWLIST_PATH).read_text(encoding="utf-8"))
    tickers = raw.get("tickers")
    if not isinstance(tickers, list) or not tickers:
        raise ValueError("liquid allowlist tickers must be a non-empty list")
    names: list[str] = []
    for ticker in tickers:
        if not is_tradable_ticker(ticker):
            raise ValueError(f"invalid liquid allowlist ticker: {ticker!r}")
        if ticker not in names:
            names.append(ticker)
    for ticker in UNIVERSE:
        if ticker not in names:
            names.append(ticker)
    return tuple(names)


def allowlist_top_n(path: Path | None = None) -> int:
    raw = json.loads((path or _ALLOWLIST_PATH).read_text(encoding="utf-8"))
    value = raw.get("top_n", INTEL_TOP_N)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return INTEL_TOP_N
    return value


def event_ticker(item: Any) -> str | None:
    ticker = getattr(item, "ticker", None)
    if ticker is None and isinstance(item, dict):
        ticker = item.get("ticker")
    if is_tradable_ticker(ticker):
        return str(ticker)
    return None


def intel_ticker_counts(events: Iterable[Any]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for item in events:
        ticker = event_ticker(item)
        if ticker is not None:
            counts[ticker] += 1
    return dict(sorted(counts.items()))


def expand_operating_universe(
    events: Iterable[Any],
    *,
    fixture: tuple[str, ...] | None = None,
    allowlist: tuple[str, ...] | None = None,
    top_n: int | None = None,
) -> dict[str, Any]:
    """Fixture set union top-N allowlisted intel tickers. Skip names not allowlisted."""
    base = tuple(UNIVERSE if fixture is None else fixture)
    allowed = set(load_liquid_allowlist() if allowlist is None else allowlist)
    allowed.update(base)
    cap = INTEL_TOP_N if top_n is None else int(top_n)
    if cap < 0:
        raise ValueError("top_n must be non-negative")
    counts = intel_ticker_counts(events)
    ranked = [
        ticker
        for ticker, _count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )
        if ticker in allowed and ticker not in base
    ]
    intel = tuple(ranked[:cap])
    operating = tuple(dict.fromkeys([*base, *intel]))
    return {
        "fixture": list(base),
        "intel": list(intel),
        "operating": list(operating),
        "top_n": cap,
        "capped": len(ranked) > cap,
        "intel_counts": {ticker: counts[ticker] for ticker in intel},
    }
