"""World Monitor API adapter."""

import json
import urllib.request
from datetime import datetime, timezone

from signal_sim.events import Event
from signal_sim.secrets import read_env

def _fetch_api(url: str, key: str) -> dict | list:
    req = urllib.request.Request(url)
    req.add_header("X-WorldMonitor-Key", key)
    req.add_header("User-Agent", "signal-sim-paper/0.1")
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode("utf-8"))

def live() -> list[Event]:
    key = read_env("WORLD_MONITOR_KEY")
    if not key:
        raise ValueError("WORLD_MONITOR_KEY is missing")

    events = []
    observed_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    intel_url = "https://api.worldmonitor.app/api/intelligence/v1/get-country-intel-brief?country_code=US"
    chokepoint_url = "https://api.worldmonitor.app/api/supply-chain/v1/get-chokepoint-status"

    intel_payload = _fetch_api(intel_url, key)
    
    if isinstance(intel_payload, list):
        # Legacy test_feeds.py support
        for raw in intel_payload:
            event_dict = {
                "id": str(raw.get("id", "wm-unknown")),
                "source": "worldmonitor",
                "kind": "intel_brief",
                "ticker": raw.get("ticker", "UNKNOWN"),
                "entities": raw.get("entities", []),
                "headline": raw.get("headline", ""),
                "url": raw.get("url", ""),
                "occurred_at": raw.get("occurred_at"),
                "filed_at": None,
                "observed_at": observed_now,
                "confidence": float(raw.get("confidence", 0.0)),
                "raw_ref": f"worldmonitor:{raw.get('id', 'unknown')}"
            }
            events.append(Event.from_dict(event_dict))
        return events

    # 1. Process US Intel Brief
    brief_text = str(intel_payload.get("brief", ""))
    sources_text = str(intel_payload.get("sources", ""))
    combined_text = (brief_text + " " + sources_text).lower()
    
    ticker = None
    if "nvda" in combined_text or "nvidia" in combined_text:
        ticker = "NVDA"
    elif "disney" in combined_text:
        ticker = "DIS"
        
    if ticker:
        occurred_at = intel_payload.get("generatedAt") or intel_payload.get("fetchedAt") or observed_now
        
        events.append(Event.from_dict({
            "id": f"wm-us-intel-{occurred_at}",
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
            "raw_ref": "worldmonitor:us_intel"
        }))

    # 2. Process Chokepoint Status
    chokepoint_payload = _fetch_api(chokepoint_url, key)
    chokepoints = chokepoint_payload.get("chokepoints", [])
    
    for idx, cp in enumerate(chokepoints):
        occurred_at = cp.get("fetchedAt") or cp.get("generatedAt") or observed_now
        events.append(Event.from_dict({
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
            "raw_ref": f"worldmonitor:chokepoint_{idx}"
        }))
        
    return events
