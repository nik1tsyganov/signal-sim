"""World Monitor API adapter.

Consume the vendor over HTTP only. Do not vendor GPL/AGPL source.
Live stays stubbed until WORLD_MONITOR_KEY exists. Recorded JSON is the
offline path for tests.
"""

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from signal_sim.events import Event
from signal_sim.indicators import UNIVERSE
from signal_sim.secrets import read_env
from signal_sim.universe import is_tradable_ticker


_TICKER_HINTS = {
    "NVDA": ("nvda", "nvidia"),
    "AAPL": ("aapl", "apple"),
    "MSFT": ("msft", "microsoft"),
    "META": ("meta", "facebook"),
    "GOOGL": ("googl", "google", "alphabet"),
    "AMZN": ("amzn", "amazon"),
    "XLE": ("xle",),
    "XOM": ("xom", "exxon"),
    "CVX": ("cvx", "chevron"),
    "DIS": ("disney",),
    "NFLX": ("nflx", "netflix"),
    "CMCSA": ("cmcsa", "comcast"),
    "SPY": ("spy",),
    "QQQ": ("qqq",),
    "XLK": ("xlk",),
    "TSLA": ("tsla", "tesla"),
    "AMD": ("amd",),
    "AVGO": ("avgo", "broadcom"),
    "ORCL": ("orcl", "oracle"),
    "JPM": ("jpm", "jpmorgan", "j.p. morgan"),
    "V": ("visa",),
    "MA": ("mastercard",),
    "WMT": ("wmt", "walmart"),
    "COST": ("costco",),
}


def _fetch_api(url: str, key: str) -> dict | list:
    req = urllib.request.Request(url)
    req.add_header("X-WorldMonitor-Key", key)
    req.add_header("User-Agent", "signal-sim-paper/0.1")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))


def _accept_set(accept) -> set[str]:
    if accept is None:
        return set(UNIVERSE)
    return {ticker for ticker in accept if is_tradable_ticker(ticker)}


def _iso_stamp(value, fallback: str) -> str:
    if value in (None, ""):
        return fallback
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000.0
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _mentioned_tickers(text: str, accept=None) -> list[str]:
    found = []
    blob = text.lower()
    for ticker in _accept_set(accept):
        hints = _TICKER_HINTS.get(ticker, (ticker.lower(),))
        if any(re.search(r"\b" + re.escape(hint) + r"\b", blob) for hint in hints):
            found.append(ticker)
    return found


def _map_list_payload(payload: list, observed_now: str, accept=None) -> list[Event]:
    allowed = _accept_set(accept)
    events = []
    for raw in payload:
        ticker = raw.get("ticker", "UNKNOWN")
        if ticker not in allowed:
            continue
        event_dict = {
            "id": str(raw.get("id", "wm-unknown")),
            "source": "worldmonitor",
            "kind": "intel_brief",
            "ticker": ticker,
            "entities": raw.get("entities", []),
            "headline": raw.get("headline", ""),
            "url": raw.get("url", ""),
            "occurred_at": raw.get("occurred_at"),
            "filed_at": None,
            "observed_at": observed_now,
            "confidence": float(raw.get("confidence", 0.0)),
            "raw_ref": f"worldmonitor:{raw.get('id', 'unknown')}",
        }
        events.append(Event.from_dict(event_dict))
    return events


def _map_intel_brief(payload: dict, observed_now: str, accept=None) -> list[Event]:
    brief_text = str(payload.get("brief", ""))
    sources_text = str(payload.get("sources", ""))
    combined_text = brief_text + " " + sources_text
    occurred_at = _iso_stamp(
        payload.get("generatedAt") or payload.get("fetchedAt"), observed_now
    )
    events = []
    for ticker in _mentioned_tickers(combined_text, accept=accept):
        events.append(
            Event.from_dict(
                {
                    "id": f"wm-us-intel-{ticker}-{occurred_at}",
                    "source": "worldmonitor",
                    "kind": "intel_brief",
                    "ticker": ticker,
                    "entities": [],
                    "headline": "US Country Intel Brief",
                    "url": "",
                    "occurred_at": occurred_at,
                    "filed_at": None,
                    "observed_at": max(observed_now, occurred_at),
                    "confidence": 1.0,
                    "raw_ref": "worldmonitor:us_intel",
                }
            )
        )
    return events


def _map_chokepoints(payload: dict, observed_now: str) -> list[Event]:
    events = []
    for idx, cp in enumerate(payload.get("chokepoints", [])):
        occurred_at = _iso_stamp(cp.get("fetchedAt") or cp.get("generatedAt"), observed_now)
        events.append(
            Event.from_dict(
                {
                    "id": f"wm-chokepoint-{idx}-{occurred_at}",
                    "source": "worldmonitor",
                    "kind": "intel_brief",
                    "ticker": "XLE",
                    "entities": [],
                    "headline": f"Chokepoint Status: {cp.get('congestionLevel', 'Unknown')}",
                    "url": "",
                    "occurred_at": occurred_at,
                    "filed_at": None,
                    "observed_at": max(observed_now, occurred_at),
                    "confidence": 1.0,
                    "raw_ref": f"worldmonitor:chokepoint_{idx}",
                }
            )
        )
    return events


def load_recorded(fixtures=None) -> list[Event]:
    """Load checked-in World Monitor JSON using the file's own clock.

    Never uses process ``now()``. Live HTTP is not opened.
    """
    root = Path(fixtures) if fixtures is not None else Path(__file__).resolve().parent.parent.parent / "fixtures"
    intel = root / "recorded" / "worldmonitor" / "us_intel_brief.json"
    choke = root / "recorded" / "worldmonitor" / "chokepoint_status.json"
    if not intel.is_file():
        return []
    with intel.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    clock = None
    if isinstance(payload, dict):
        clock = payload.get("generatedAt") or payload.get("fetchedAt")
    if not clock:
        raise ValueError("recorded World Monitor JSON must carry generatedAt or fetchedAt")
    return map_recorded(intel, choke if choke.is_file() else None, now=clock)


def map_recorded(intel_path, chokepoint_path=None, now=None, accept=None) -> list[Event]:
    """Map checked-in World Monitor JSON. No HTTP."""
    if now is None:
        observed_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    elif isinstance(now, datetime):
        observed_now = now.isoformat().replace("+00:00", "Z")
    else:
        observed_now = str(now)
    with Path(intel_path).open(encoding="utf-8") as handle:
        intel_payload = json.load(handle)
    events = []
    if isinstance(intel_payload, list):
        events.extend(_map_list_payload(intel_payload, observed_now, accept=accept))
    elif isinstance(intel_payload, dict):
        events.extend(_map_intel_brief(intel_payload, observed_now, accept=accept))
    if chokepoint_path is not None:
        with Path(chokepoint_path).open(encoding="utf-8") as handle:
            events.extend(_map_chokepoints(json.load(handle), observed_now))
    return events


def live(accept=None) -> list[Event]:
    key = read_env("WORLD_MONITOR_KEY")
    if not key:
        raise ValueError("WORLD_MONITOR_KEY is missing")

    observed_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    intel_url = "https://api.worldmonitor.app/api/intelligence/v1/get-country-intel-brief?country_code=US"
    chokepoint_url = "https://api.worldmonitor.app/api/supply-chain/v1/get-chokepoint-status"

    intel_payload = _fetch_api(intel_url, key)
    if isinstance(intel_payload, list):
        return _map_list_payload(intel_payload, observed_now, accept=accept)

    events = _map_intel_brief(intel_payload, observed_now, accept=accept)
    events.extend(_map_chokepoints(_fetch_api(chokepoint_url, key), observed_now))
    return events
