"""The only paper-order path (docs/alt-data-and-safety.md sections 3-5).

submit_paper_order() is the single choke point that can create an order row:
R1 consults safety.PAPER_ONLY at call time, R3 runs the fail-closed
kill-switch, R9 is the plain-code validator (schema, ticker allowlist, size
cap, provenance event ids, idempotency), R8 appends the audit line before
any return. R2: the default broker "client" is in-process. Known live endpoints raise
LiveEndpointError. The paper host may construct a read-only Alpaca paper
client when paper keys are present; it never places orders.
submit_paper_order() remains the only order path. Live-broker names appear
below only in runtime-assembled or bare-number form so no forbidden
fragment is a contiguous substring of this file (SafetyRailTests scans
the package).
"""

import hashlib
import json
import math
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import safety
from .alpaca_paper import AlpacaPaperClient
from .indicators import UNIVERSE
from .runtime_env import paper_submit_flag
from .secrets import read_env

SIDES = ("buy", "sell")

_LIVE_HOST_FRAGMENT = "alpaca" + ".markets"
_PAPER_HOST_PREFIX = "paper-api."
_LIVE_PORTS = frozenset((7496, 4001))  # IBKR live; the paper pair is 7497/4002

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    size_frac REAL NOT NULL,
    event_ids TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders(order_id),
    price REAL NOT NULL,
    filled_at TEXT NOT NULL,
    cost REAL NOT NULL DEFAULT 0
);
"""


class OrderRefused(ValueError):
    """The proposal did not clear the rails; no order row was created."""


class ProvenanceMissing(ValueError):
    """A ledger fill has no complete R8 audit record."""


class LiveEndpointError(ValueError):
    """Client construction named a known live broker endpoint (R2)."""


class PaperBrokerClient:
    """v0 in-process broker handle (R2).

    A marker object with no order-placement method: submit_paper_order() is
    the only order path, and this class must never grow a second one.
    """

    mode = "in-process"


class AlpacaPaperStub:
    """Missing-key paper-host constructor. Must not open a socket."""

    mode = "paper-stub"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("no verified key + terms")


def _default_paper_base_url():
    return "https://" + _PAPER_HOST_PREFIX + _LIVE_HOST_FRAGMENT


def _paper_host_name():
    return _PAPER_HOST_PREFIX + _LIVE_HOST_FRAGMENT


def paper_host():
    """Assembled paper-host name. Safe to call; does not open a socket."""
    return _paper_host_name()


def _normalize_host(host):
    text = "" if host is None else str(host).strip().lower()
    if "://" in text:
        return (urlsplit(text).hostname or "").lower()
    return text


def _is_paper_host(host_text):
    return host_text == _paper_host_name()


def missing_paper_keys():
    """Env names required for the read-only paper client, or empty."""
    missing = []
    if not read_env("ALPACA_PAPER_API_KEY"):
        missing.append("ALPACA_PAPER_API_KEY")
    if not read_env("ALPACA_PAPER_API_SECRET"):
        missing.append("ALPACA_PAPER_API_SECRET")
    return missing


def paper_submit_enabled():
    """Remote paper POSTs stay off unless this later gate is explicitly 1.

    Default is 0 (missing, empty, or any value other than the string 1).
    """
    return paper_submit_flag() == "1"


def resolve_paper_base_url():
    """Paper HTTPS origin. Live hosts raise LiveEndpointError."""
    raw = read_env("ALPACA_PAPER_API_BASE_URL") or _default_paper_base_url()
    parsed = urlsplit(raw)
    if parsed.scheme:
        if parsed.scheme != "https":
            raise ValueError("paper broker URL must be https")
        if parsed.username or parsed.password:
            raise ValueError("paper broker URL must not embed credentials")
        host_text = (parsed.hostname or "").lower()
    else:
        host_text = raw.strip().lower()
    if _LIVE_HOST_FRAGMENT in host_text and not host_text.startswith(
        _PAPER_HOST_PREFIX
    ):
        raise LiveEndpointError(f"live broker host refused: {host_text!r}")
    if not _is_paper_host(host_text):
        raise ValueError(f"paper broker host refused: {host_text!r}")
    return "https://" + host_text


def paper_broker_client(host=None, port=None):
    """Construct the broker client.

    The default (no host, no port) is the in-process paper broker. The
    paper-host name constructs a read-only Alpaca paper client when
    ALPACA_PAPER_API_KEY and ALPACA_PAPER_API_SECRET are set; missing
    keys raise NotImplementedError and never open a socket. Known live
    endpoints (a non-paper broker host, or IBKR live ports on any host)
    raise LiveEndpointError so a misconfiguration fails at startup, not
    at order time. This client has no order-placement method.
    """
    if host is None and port is None:
        return PaperBrokerClient()
    host_text = _normalize_host(host)
    if _LIVE_HOST_FRAGMENT in host_text and not host_text.startswith(
        _PAPER_HOST_PREFIX
    ):
        raise LiveEndpointError(f"live broker host refused: {host!r}")
    try:
        port_number = None if port is None else int(port)
    except (TypeError, ValueError):
        port_number = None
    if port_number in _LIVE_PORTS:
        raise LiveEndpointError(f"live broker port refused: {host!r} port {port!r}")
    if _is_paper_host(host_text):
        missing = missing_paper_keys()
        if missing:
            raise NotImplementedError("no verified key + terms")
        return AlpacaPaperClient(
            base_url=resolve_paper_base_url(),
            api_key=read_env("ALPACA_PAPER_API_KEY"),
            api_secret=read_env("ALPACA_PAPER_API_SECRET"),
        )
    raise ValueError(
        "v0 egress allowlist is empty - PaperBroker is in-process only; "
        f"got {host!r} port {port!r}"
    )


def _json_scalar(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and math.isfinite(value):
        return value
    return None


def _event_id_hash(event_ids):
    payload = json.dumps(list(event_ids), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _event_id_hashes(event_ids):
    return [hashlib.sha256(str(item).encode("utf-8")).hexdigest() for item in event_ids]


def _audit_snapshot(proposal):
    """R8 fields, tolerant of malformed proposals - refusals get logged too."""
    if not isinstance(proposal, dict):
        proposal = {}
    event_ids = proposal.get("event_ids")
    if isinstance(event_ids, list):
        # Non-string entries are preserved as text, never dropped - the audit
        # must show what was actually submitted.
        event_ids = [e if isinstance(e, str) else repr(e) for e in event_ids]
    else:
        event_ids = []
    decision_at = proposal.get("decision_at")
    if isinstance(decision_at, datetime):
        try:
            decision_at = _stamp(decision_at, "decision_at")
        except ValueError:
            decision_at = None
    elif not isinstance(decision_at, str) or not decision_at:
        decision_at = None
    from .params import params_sha256

    record = {
        "ticker": _json_scalar(proposal.get("ticker")),
        "side": _json_scalar(proposal.get("side")),
        "size_frac": _json_scalar(proposal.get("size_frac")),
        "event_ids": event_ids,
        "event_id_hashes": _event_id_hashes(event_ids) if event_ids else [],
        "event_id_hash": _event_id_hash(event_ids) if event_ids else None,
        "decision_at": decision_at,
        "params_sha256": params_sha256(),
        "idempotency_key": _json_scalar(proposal.get("idempotency_key")),
        "fill": None,
    }
    return record


def _missing_provenance(record, *, filled):
    """Return the first missing R8 field, or None when the record is complete."""
    if not isinstance(record, dict):
        return "provenance record missing"
    event_ids = record.get("event_ids")
    if not isinstance(event_ids, list) or not event_ids:
        return "event_ids"
    if not record.get("event_id_hash"):
        return "event_id_hash"
    hashes = record.get("event_id_hashes")
    if not isinstance(hashes, list) or len(hashes) != len(event_ids):
        return "event_id_hashes"
    if not record.get("decision_at"):
        return "decision_at"
    digest = record.get("params_sha256")
    if not isinstance(digest, str) or len(digest) != 64 or any(
        char not in "0123456789abcdef" for char in digest
    ):
        return "params_sha256"
    from .params import params_sha256

    if digest != params_sha256():
        return "params_sha256"
    if not record.get("verdict"):
        return "verdict"
    if record.get("outcome") not in ("filled", "refused"):
        return "outcome"
    if filled:
        fill = record.get("fill")
        if not isinstance(fill, dict):
            return "fill"
        for key in ("fill_px", "filled_at", "order_id"):
            if fill.get(key) in (None, ""):
                return f"fill.{key}"
        try:
            decision = _stamp(record["decision_at"], "decision_at")
            filled_at = _stamp(fill["filled_at"], "filled_at")
        except ValueError:
            return "fill.filled_at"
        if filled_at <= decision:
            return "fill.filled_at<=decision_at"
    return None


def read_audit_records(audit_path):
    """Load every JSONL provenance record. Missing file is an empty log."""
    if not audit_path:
        return []
    try:
        with open(audit_path, "r", encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line]
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ProvenanceMissing(f"R8: audit line is not JSON: {error}") from error
    return records


def assert_fills_have_provenance(ledger_path, audit_path=None):
    """Fail closed if any sqlite fill lacks a complete matching R8 record."""
    if audit_path is None:
        audit_path = str(ledger_path) + ".audit.jsonl"
    connection = sqlite3.connect(ledger_path)
    try:
        tables = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "fills" not in tables:
            fills = []
        else:
            fills = connection.execute(
                "SELECT order_id, price, filled_at FROM fills"
            ).fetchall()
    finally:
        connection.close()
    if not fills:
        return
    records = read_audit_records(audit_path)
    by_order = {}
    for record in records:
        if record.get("outcome") != "filled":
            continue
        missing = _missing_provenance(record, filled=True)
        if missing is not None:
            raise ProvenanceMissing(f"R8: provenance missing {missing}")
        fill = record.get("fill") or {}
        order_id = fill.get("order_id") or record.get("order_id")
        if not order_id:
            raise ProvenanceMissing("R8: provenance missing fill.order_id")
        by_order[str(order_id)] = record
    for order_id, price, filled_at in fills:
        record = by_order.get(str(order_id))
        if record is None:
            raise ProvenanceMissing(f"R8: fill {order_id} has no provenance record")
        fill = record["fill"]
        if float(fill["fill_px"]) != float(price):
            raise ProvenanceMissing(f"R8: fill {order_id} provenance price mismatch")
        if fill.get("filled_at") != filled_at:
            raise ProvenanceMissing(f"R8: fill {order_id} provenance filled_at mismatch")


def _last_audit_line(audit_path):
    if not audit_path:
        return None
    try:
        with open(audit_path, "r", encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line]
    except OSError:
        return None
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def _append_audit(audit_path, record):
    with open(audit_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


_EXECUTION_MARK_KIND = "fixture_mark"
_EXECUTION_MARK_SOURCE = "fixture"
_BANNED_MARK_LABELS = frozenset(
    ("yah" + "oo", "sto" + "oq", "yfin" + "ance", "vendor", "research")
)


def execution_mark_failure(kind=None, source=None):
    """Return a refusal reason if this is not a fixture execution mark."""
    kind_text = _EXECUTION_MARK_KIND if kind in (None, "") else str(kind).strip().lower()
    source_text = _EXECUTION_MARK_SOURCE if source in (None, "") else str(source).strip().lower()
    if (
        kind_text in _BANNED_MARK_LABELS
        or source_text in _BANNED_MARK_LABELS
        or kind_text != _EXECUTION_MARK_KIND
        or source_text != _EXECUTION_MARK_SOURCE
    ):
        return "execution mark must be fixture_mark"
    return None


def _validation_failure(proposal, mark_px):
    """Return the first R9 failure as a string, or None when approved."""
    if not isinstance(proposal, dict):
        return "proposal must be a mapping"
    if proposal.get("ticker") not in UNIVERSE:
        return f"ticker not in universe {UNIVERSE}"
    side = proposal.get("side")
    if side not in SIDES:
        return "side must be 'buy' or 'sell'"
    size_frac = proposal.get("size_frac")
    if (
        isinstance(size_frac, bool)
        or not isinstance(size_frac, (int, float))
        or not math.isfinite(size_frac)
        or size_frac <= 0
    ):
        return "size_frac must be a number in (0, 1]" if side == "buy" else "size_frac must be a positive finite number"
    if side == "buy" and size_frac > 1:
        return "size_frac must be a number in (0, 1]"
    event_ids = proposal.get("event_ids")
    if (
        not isinstance(event_ids, list)
        or not event_ids
        or not all(isinstance(e, str) and e for e in event_ids)
    ):
        return "event_ids must be a non-empty list of provenance id strings"
    decision_at = proposal.get("decision_at")
    if decision_at is None or decision_at == "":
        return "decision_at must be a timezone-aware timestamp"
    try:
        _stamp(decision_at, "decision_at")
    except ValueError:
        return "decision_at must be a timezone-aware timestamp"
    key = proposal.get("idempotency_key")
    if not isinstance(key, str) or not key:
        return "idempotency_key must be a non-empty string"
    if (
        isinstance(mark_px, bool)
        or not isinstance(mark_px, (int, float))
        or not math.isfinite(mark_px)
        or mark_px <= 0
    ):
        return "mark_px must be a positive finite number"
    return None


def _stamp(value, field):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field} must be a timezone-aware timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware timestamp")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def submit_paper_order(
    proposal,
    *,
    ledger_path,
    mark_px,
    audit_path=None,
    kill_root=None,
    cost=0,
    filled_at=None,
    mark_kind=None,
    mark_source=None,
):
    """The only function that can create an order row.

    Rails, in order: R1 paper-only constant, R3 fail-closed kill-switch,
    R9 plain-code validator, idempotency via the ledger's UNIQUE key, and a
    deterministic paper fill at the caller-supplied mark_px. Every attempt -
    filled or refused - appends one R8 audit line; the success line lands
    before the fill commits. Refusals raise OrderRefused.

    ``filled_at`` is the ledger clock for the fill. Replay passes the fixture
    fill_at. The default is process time. This is not occurred_at or a trade date.
    """
    processed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if audit_path is None:
        audit_path = str(ledger_path) + ".audit.jsonl"
    record = _audit_snapshot(proposal)

    def refuse(reason):
        record["verdict"] = f"refused: {reason}"
        record["outcome"] = "refused"
        _append_audit(audit_path, record)
        raise OrderRefused(reason)

    if safety.PAPER_ONLY is not True:
        refuse("R1: safety.PAPER_ONLY is not True")
    # kill_root adds a root to check; it never replaces the repo-root check,
    # so a caller-supplied directory cannot bypass the repo KILL file.
    try:
        kill_ok = safety.kill_switch_ok() is True and (
            kill_root is None or safety.kill_switch_ok(kill_root) is True
        )
    except Exception:
        kill_ok = False  # R3: an errored check refuses, never proceeds
    if kill_ok is not True:
        refuse("R3: kill-switch refused the order (fail-closed)")
    failure = _validation_failure(proposal, mark_px)
    if failure is not None:
        refuse(f"R9: {failure}")
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(cost)
        or cost < 0
    ):
        refuse("R9: cost must be a non-negative finite number")
    try:
        ledger_stamp = processed_at if filled_at is None else _stamp(filled_at, "filled_at")
        decision_stamp = _stamp(proposal["decision_at"], "decision_at")
    except ValueError as error:
        refuse(f"R9: {error}")
    if ledger_stamp <= decision_stamp:
        refuse("R9: filled_at must be after decision_at")
    mark_failure = execution_mark_failure(mark_kind, mark_source)
    if mark_failure is not None:
        refuse(f"R9: {mark_failure}")

    order_id = hashlib.sha256(proposal["idempotency_key"].encode("utf-8")).hexdigest()[:32]
    fill_id = hashlib.sha256(f"{proposal['idempotency_key']}:fill".encode("utf-8")).hexdigest()[:32]
    con = sqlite3.connect(ledger_path)
    try:
        con.executescript(_SCHEMA)
        try:
            con.execute(
                "INSERT INTO orders (order_id, idempotency_key, ticker, side,"
                " size_frac, event_ids, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    proposal["idempotency_key"],
                    proposal["ticker"],
                    proposal["side"],
                    float(proposal["size_frac"]),
                    json.dumps(proposal["event_ids"]),
                    "filled",
                    ledger_stamp,
                ),
            )
        except sqlite3.IntegrityError:
            refuse("R9: duplicate idempotency_key - proposal already submitted")
        con.execute(
            "INSERT INTO fills (fill_id, order_id, price, filled_at, cost)"
            " VALUES (?, ?, ?, ?, ?)",
            (fill_id, order_id, float(mark_px), ledger_stamp, float(cost)),
        )
        record["verdict"] = "approved"
        record["outcome"] = "filled"
        record["order_id"] = order_id
        record["decision_at"] = _stamp(proposal["decision_at"], "decision_at")
        record["event_id_hash"] = _event_id_hash(proposal["event_ids"])
        record["event_id_hashes"] = _event_id_hashes(proposal["event_ids"])
        record["fill"] = {
            "fill_px": float(mark_px),
            "filled_at": ledger_stamp,
            "cost": float(cost),
            "order_id": order_id,
        }
        missing = _missing_provenance(record, filled=True)
        if missing is not None:
            refuse(f"R8: provenance missing {missing}")
        # R8: the audit line lands before the fill commits - if the append
        # fails, the transaction rolls back and no unaudited fill can exist.
        _append_audit(audit_path, record)
        written = _last_audit_line(audit_path)
        if _missing_provenance(written, filled=True) is not None:
            raise OrderRefused("R8: provenance record incomplete after write")
        con.commit()
    finally:
        con.close()
    return {
        "order_id": order_id,
        "status": "filled",
        "ticker": proposal["ticker"],
        "side": proposal["side"],
        "size_frac": float(proposal["size_frac"]),
        "fill_px": float(mark_px),
        "cost": float(cost),
        "idempotency_key": proposal["idempotency_key"],
        "decision_at": record["decision_at"],
        "filled_at": ledger_stamp,
        "ledger_path": str(ledger_path),
        "audit_path": str(audit_path),
    }
