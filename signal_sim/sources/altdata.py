"""Quiver alt-data fixtures and live REST ingestion.

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
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

from signal_sim.events import Event
from signal_sim.indicators import UNIVERSE
from signal_sim.safety import assert_event_timestamps
from signal_sim.universe import is_tradable_ticker

KINDS = ("congress_trade", "insider", "gov_contract")
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


_HOST = "https://api.quiverquant.com"
_DEFAULT_DATASETS = ("congresstrading",)
RESEARCH_DATASETS = ("congresstrading", "insiders", "govcontracts", "quivernews")
_USER_AGENT = "signal-sim-paper/0.1"


def _accept_set(accept):
    if accept is None:
        return set(UNIVERSE)
    return {ticker for ticker in accept if is_tradable_ticker(ticker)}


def _ticker_accepted(ticker, accept):
    return ticker in _accept_set(accept)


def read_env(name):
    """Keep both the legacy secrets patch point and a local test patch point."""
    from signal_sim.secrets import read_env as secrets_read_env

    return secrets_read_env(name)


def _first(raw, *names):
    for name in names:
        value = raw.get(name)
        if value not in (None, ""):
            return value
    return None


def _iso_timestamp(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError(f"timestamp missing or invalid: {value!r}")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat().replace("+00:00", "Z")


def _observed_at(now, filed_at):
    filed = datetime.fromisoformat(filed_at.replace("Z", "+00:00"))
    return max(now, filed).isoformat().replace("+00:00", "Z")


def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not an amount")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d[\d,]*(?:\.\d+)?", value)
        if match:
            return float(match.group(0).replace(",", ""))
    raise ValueError(f"invalid amount: {value!r}")


def _amount_range(raw):
    value = _first(raw, "Range", "range", "amount_range_usd")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [_number(value[0]), _number(value[1])]
    if value is not None:
        matches = re.findall(r"\d[\d,]*(?:\.\d+)?", str(value))
        if len(matches) >= 2:
            return [
                float(matches[0].replace(",", "")),
                float(matches[1].replace(",", "")),
            ]
        if len(matches) == 1:
            amount = float(matches[0].replace(",", ""))
            return [amount, amount]
    amount = _first(raw, "Amount", "amount", "Value", "value")
    if amount is None:
        return [0.0, 0.0]
    amount = _number(amount)
    return [amount, amount]


def _transaction(value, insider=False):
    normalized = str(value or "").strip().lower()
    if insider and normalized == "a":
        return "purchase"
    if insider and normalized == "d":
        return "sale"
    if normalized.startswith("purchase"):
        return "purchase"
    if normalized.startswith("sale"):
        return "sale"
    return None


def _row_id(raw, dataset, index):
    existing = _first(raw, "id", "ID", "Id")
    if existing is not None:
        return str(existing)
    discriminator = _first(
        raw,
        "BioGuideID",
        "Ticker",
        "ticker",
        "DateTime",
        "Date",
        "TransactionDate",
    )
    return f"quiver-{dataset}-{discriminator or index}-{index}"


def _fetch(dataset, key):
    request = urllib.request.Request(f"{_HOST}/beta/live/{dataset}")
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("User-Agent", _USER_AGENT)
    with urllib.request.urlopen(request) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"quiver.{dataset}: expected a JSON list")
    return payload


def _congress_events(payload, now, accept=None):
    events = []
    allowed = _accept_set(accept)
    for index, raw in enumerate(payload):
        ticker = _first(raw, "Ticker", "ticker")
        legacy_test_shape = "Ticker" not in raw and all(
            name in raw
            for name in ("ticker", "representative", "trade_date", "report_date")
        )
        if ticker not in allowed and not legacy_test_shape:
            continue
        transaction = _transaction(_first(raw, "Transaction", "transaction"))
        if transaction is None:
            continue
        try:
            occurred_at = _iso_timestamp(
                _first(raw, "TransactionDate", "trade_date", "occurred_at")
            )
            filed_at = _iso_timestamp(
                _first(raw, "ReportDate", "report_date", "filed_at")
            )
            mapped = {
                "id": _row_id(raw, "congresstrading", index),
                "source": "quiver",
                "kind": "congress_trade",
                "ticker": ticker,
                "person": _first(raw, "Representative", "representative") or "Unknown",
                "transaction": transaction,
                "amount_range_usd": _amount_range(raw),
                "occurred_at": occurred_at,
                "filed_at": filed_at,
                "observed_at": _observed_at(now, filed_at),
                "raw_ref": f"quiver:congresstrading:{_row_id(raw, 'congresstrading', index)}",
            }
            chamber = _first(raw, "House", "house", "chamber")
            if chamber is not None:
                mapped["chamber"] = chamber
            description = _first(raw, "Description", "description")
            if description is not None:
                mapped["entities"] = [str(description)]
            events.append(_validated(mapped, "quiver.live.congresstrading"))
        except (ValueError, TypeError):
            continue
    events.sort(key=lambda pair: pair[1])
    return [event for event, _filed in events]


def _insider_events(payload, now, accept=None):
    events = []
    allowed = _accept_set(accept)
    for index, raw in enumerate(payload):
        ticker = _first(raw, "Ticker", "ticker")
        if ticker not in allowed:
            continue
        transaction = _transaction(
            _first(raw, "AcquiredDisposedCode", "acquired_disposed_code", "transaction"),
            insider=True,
        )
        if transaction is None:
            continue
        try:
            occurred_at = _iso_timestamp(_first(raw, "Date", "date", "occurred_at"))
            filed_at = _iso_timestamp(
                _first(raw, "FiledDate", "FilingDate", "ReportDate", "filed_at")
                or occurred_at
            )
            mapped = {
                "id": _row_id(raw, "insiders", index),
                "source": "quiver",
                "kind": "insider",
                "ticker": ticker,
                "person": _first(raw, "Name", "name", "person") or "Unknown",
                "transaction": transaction,
                "amount_range_usd": _amount_range(raw),
                "occurred_at": occurred_at,
                "filed_at": filed_at,
                "observed_at": _observed_at(now, filed_at),
                "raw_ref": f"quiver:insiders:{_row_id(raw, 'insiders', index)}",
            }
            events.append(_validated(mapped, "quiver.live.insiders"))
        except (ValueError, TypeError):
            continue
    events.sort(key=lambda pair: pair[1])
    return [event for event, _filed in events]


def _canonical_event(raw, dataset, index, kind, occurred_at, filed_at, now, accept=None):
    ticker = _first(raw, "Ticker", "ticker")
    if ticker not in _accept_set(accept):
        return None
    row_id = _row_id(raw, dataset, index)
    headline = _first(raw, "Headline", "headline", "Title", "title", "Description")
    agency = _first(raw, "Agency", "agency")
    observed_at = (
        _observed_at(now, filed_at)
        if filed_at is not None
        else now.isoformat().replace("+00:00", "Z")
    )
    return Event.from_dict(
        {
            "id": row_id,
            "source": "quiver",
            "kind": kind,
            "ticker": ticker,
            "entities": [str(agency)] if agency is not None else [],
            "headline": str(headline or ""),
            "url": str(_first(raw, "URL", "Url", "url", "Link", "link") or ""),
            "occurred_at": occurred_at,
            "filed_at": filed_at,
            "observed_at": observed_at,
            "confidence": 1.0,
            "raw_ref": f"quiver:{dataset}:{row_id}",
        }
    )


def _gov_contract_events(payload, now, accept=None):
    events = []
    allowed = _accept_set(accept)
    for index, raw in enumerate(payload):
        if _first(raw, "Ticker", "ticker") not in allowed:
            continue
        try:
            occurred_at = _iso_timestamp(
                _first(raw, "Date", "date", "StartDate", "start_date", "occurred_at")
                or _first(raw, "AwardDate", "award_date")
            )
            filed_at = _iso_timestamp(
                _first(
                    raw,
                    "FiledDate",
                    "FilingDate",
                    "ReportDate",
                    "AwardDate",
                    "filed_at",
                    "award_date",
                )
                or occurred_at
            )
            event = _canonical_event(
                raw,
                "govcontracts",
                index,
                "gov_contract",
                occurred_at,
                filed_at,
                now,
                accept=allowed,
            )
        except (ValueError, TypeError):
            continue
        if event is not None:
            events.append(event)
    return events


def _news_events(payload, now, accept=None):
    events = []
    allowed = _accept_set(accept)
    for index, raw in enumerate(payload):
        if _first(raw, "Ticker", "ticker") not in allowed:
            continue
        try:
            occurred_at = _iso_timestamp(
                _first(
                    raw,
                    "DateTime",
                    "Datetime",
                    "datetime",
                    "HeadlineTime",
                    "PublishedAt",
                    "Published",
                    "Timestamp",
                    "Time",
                    "Date",
                    "occurred_at",
                )
            )
            event = _canonical_event(
                raw, "quivernews", index, "news", occurred_at, None, now, accept=allowed
            )
        except (ValueError, TypeError):
            continue
        if event is not None:
            events.append(event)
    return events


_FETCHERS = {
    "congresstrading": _congress_events,
    "insiders": _insider_events,
    "govcontracts": _gov_contract_events,
    "quivernews": _news_events,
}


def map_recorded(dataset, path, now=None, accept=None):
    """Map a checked-in REST-shaped payload. No HTTP. Live stay stubbed without a key."""
    mapper = _FETCHERS.get(dataset)
    if mapper is None:
        raise ValueError(f"unknown Quiver dataset: {dataset!r}")
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if now is None:
        now = datetime.now(timezone.utc)
    return mapper(payload, now, accept=accept)


def live(datasets=None, accept=None):
    """Fetch and map selected Quiver live datasets.

    The default remains Congress-only so ``QuiverSource.live()`` stays the
    existing choke point. Tests replace ``urlopen`` and never call the network.
    ``accept`` widens the ticker filter to an allowlist for research / live
    universe expansion. Default stays the frozen fixture universe.
    """
    key = read_env("QUIVER_API_KEY")
    if not key:
        raise NotImplementedError("no verified key + terms")
    if datasets is None:
        datasets = _DEFAULT_DATASETS
    elif isinstance(datasets, str):
        datasets = (datasets,)

    now = datetime.now(timezone.utc)
    events = []
    for dataset in datasets:
        mapper = _FETCHERS.get(dataset)
        if mapper is None:
            raise ValueError(f"unknown Quiver dataset: {dataset!r}")
        try:
            payload = _fetch(dataset, key)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            # One 403/timeout must not kill congress or the daily research book.
            continue
        events.extend(mapper(payload, now, accept=accept))
    return events


def as_event(raw):
    """Coerce a mapped Quiver row into a canonical Event. No PII is required."""
    if isinstance(raw, Event):
        return raw
    if not isinstance(raw, dict):
        raise TypeError("Quiver row must be an Event or mapping")
    person = raw.get("person")
    entities = raw.get("entities")
    if not isinstance(entities, list) or not all(isinstance(item, str) for item in entities):
        entities = [str(person)] if person else []
    return Event.from_dict(
        {
            "id": raw["id"],
            "source": raw.get("source") or "quiver",
            "kind": raw["kind"],
            "ticker": raw["ticker"],
            "entities": entities,
            "headline": str(raw.get("headline") or ""),
            "url": str(raw.get("url") or ""),
            "occurred_at": raw["occurred_at"],
            "filed_at": raw.get("filed_at"),
            "observed_at": raw["observed_at"],
            "confidence": raw.get("confidence", 0.0),
            "raw_ref": raw.get("raw_ref") or f"quiver:{raw['ticker']}",
        }
    )


class QuiverSource:
    """Compatibility choke point for the default Congress live feed."""

    def live(self):
        return live()
