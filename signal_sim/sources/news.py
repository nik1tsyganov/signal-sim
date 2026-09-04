"""News fixture loader.

Raises on malformed JSON so a bad file cannot disappear into an empty rank.
Lists are flattened the same way as the alt-data loader and the CLI fallback.
Poison-named files are skipped; the lookahead fixture belongs to alt-data.
"""

import json
import os


def load_events(dir_path: str) -> list[dict]:
    if not os.path.isdir(dir_path):
        return []
    events = []
    for filename in sorted(os.listdir(dir_path)):
        if not filename.endswith(".json"):
            continue
        if "poison" in filename.lower():
            continue
        path = os.path.join(dir_path, filename)
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
        if isinstance(payload, list):
            events.extend(payload)
        elif isinstance(payload, dict):
            events.append(payload)
        else:
            raise ValueError(f"{filename}: fixture must contain an event or event list")
    return events
