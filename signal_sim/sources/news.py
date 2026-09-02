import os
import json

def load_events(dir_path: str) -> list[dict]:
    events = []
    if not os.path.exists(dir_path):
        return events
        
    for filename in os.listdir(dir_path):
        if filename.endswith(".json"):
            file_path = os.path.join(dir_path, filename)
            with open(file_path, "r", encoding="utf-8-sig") as f:
                try:
                    data = json.load(f)
                    # We might want to validate the event fields here, 
                    # but for now we just append the loaded dict.
                    # Required fields: id, source, kind, ticker, entities, headline, url, occurred_at, filed_at, observed_at, confidence, raw_ref
                    events.append(data)
                except json.JSONDecodeError:
                    pass
    return events

