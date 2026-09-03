"""World Monitor API adapter."""

import json
import urllib.request
from datetime import datetime, timezone

from signal_sim.events import Event
from signal_sim.secrets import read_env

def live() -> list[Event]:
    key = read_env("WORLD_MONITOR_KEY")
    if not key:
        raise ValueError("WORLD_MONITOR_KEY is missing")

    req = urllib.request.Request("https://api.worldmonitor.app/v1/briefs")
    req.add_header("X-WorldMonitor-Key", key)

    with urllib.request.urlopen(req) as response:
        payload = json.loads(response.read().decode("utf-8"))

    events = []
    observed_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for raw in payload:
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
