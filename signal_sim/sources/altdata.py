"""Quiver-style alt-data adapter - fixtures only, no key, no live call.

Design: docs/alt-data-and-safety.md sections 1.1 and 5. Event shape follows
the repo-wide contract (see tests/test_kernel.py) plus the alt-data fields
from section 3 R4:

    id, source, kind ("congress_trade" | "insider"), ticker, person,
    transaction ("purchase" | "sale"), amount_range_usd [low, high],
    occurred_at / filed_at / observed_at (ISO 8601, timezone-aware),
    raw_ref, and optional extras (chamber, role, entities, confidence).

Amounts are ranges because disclosures report ranges; an exact Form 4 value
is the degenerate range [x, x]. The loader adds rank_at = filed_at: a
disclosure event ranks on when it became publicly knowable (STOCK Act lag,
up to 45 days after the trade), never on the trade date.
"""

import json
import math
import os

from signal_sim.safety import assert_event_timestamps

KINDS = ("congress_trade", "insider")
TRANSACTIONS = ("purchase", "sale")
REQUIRED_FIELDS = (
    "id",
    "source",
    "kind",
    "ticker",
    "person",
    "transaction",
    "amount_range_usd",
    "occurred_at",
    "filed_at",
    "observed_at",
    "raw_ref",
)


def _finite_bound(bound):
    # bool is an int subclass and json accepts NaN/Infinity: both must fail here
    return (
        isinstance(bound, (int, float))
        and not isinstance(bound, bool)
        and math.isfinite(bound)
    )


def _validated(raw, origin):
    missing = [field for field in REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ValueError(f"{origin}: event missing fields {missing}")
    if raw["kind"] not in KINDS:
        raise ValueError(f"{origin}: unknown kind {raw['kind']!r}")
    if raw["transaction"] not in TRANSACTIONS:
        raise ValueError(f"{origin}: unknown transaction {raw['transaction']!r}")
    amount = raw["amount_range_usd"]
    if (
        not isinstance(amount, list)
        or len(amount) != 2
        or not all(_finite_bound(bound) for bound in amount)
        or amount[0] < 0
        or amount[0] > amount[1]
    ):
        raise ValueError(f"{origin}: amount_range_usd must be [low, high], got {amount!r}")
    _, filed, _ = assert_event_timestamps(raw)
    event = dict(raw)
    event["rank_at"] = raw["filed_at"]
    return event, filed


def load_events(dir_path: str) -> list[dict]:
    """Load and validate every alt-data fixture in dir_path.

    Unlike the news loader, a malformed or lookahead-poisoned event raises -
    silently dropping bad alt-data would hide exactly the failure the safety
    rails exist to catch. Events return sorted by filed_at (the rank input).
    """
    if not os.path.isdir(dir_path):
        return []
    validated = []
    for filename in sorted(os.listdir(dir_path)):
        if not filename.endswith(".json"):
            continue
        with open(os.path.join(dir_path, filename), "r", encoding="utf-8-sig") as f:
            payload = json.load(f)
        if not isinstance(payload, list):
            payload = [payload]
        for raw in payload:
            validated.append(_validated(raw, filename))
    validated.sort(key=lambda pair: pair[1])
    return [event for event, _filed in validated]


class QuiverSource:
    """Adapter boundary for QuiverQuant. No verified key, no recorded terms,
    so there is no live implementation - the stub proves nothing pretends
    to be live (docs/alt-data-and-safety.md section 5, item 4)."""

    def live(self):
        from signal_sim.secrets import read_env
        import urllib.request
        from datetime import datetime, timezone
        
        key = read_env("QUIVER_API_KEY")
        if not key:
            raise NotImplementedError("no verified key + terms")
            
        req = urllib.request.Request("https://api.quiverquant.com/beta/live/congresstrading")
        req.add_header("Authorization", f"Bearer {key}")
        
        with urllib.request.urlopen(req) as response:
            payload = json.loads(response.read().decode("utf-8"))
            
        validated = []
        observed_now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        for raw in payload:
            mapped = {
                "id": str(raw.get("id", "quiver-unknown")),
                "source": "quiver",
                "kind": "congress_trade",
                "ticker": raw.get("ticker", "UNKNOWN"),
                "person": raw.get("representative", "Unknown"),
                "transaction": raw.get("transaction", "purchase").lower(),
                "amount_range_usd": raw.get("amount_range_usd", [0, 0]),
                "occurred_at": raw.get("trade_date"),
                "filed_at": raw.get("report_date"),
                "observed_at": observed_now,
                "raw_ref": f"quiver:{raw.get('id', 'unknown')}"
            }
            if mapped["filed_at"] and mapped["observed_at"] < mapped["filed_at"]:
                mapped["observed_at"] = mapped["filed_at"]
                
            event, _filed = _validated(mapped, "quiver.live")
            validated.append((event, _filed))
            
        validated.sort(key=lambda pair: pair[1])
        return [event for event, _filed in validated]
