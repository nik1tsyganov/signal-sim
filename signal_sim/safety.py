"""Paper-only safety rails (docs/alt-data-and-safety.md section 3).

R1: PAPER_ONLY is a code-level constant - no env var, config, CLI flag, model
output, or ingested data can flip it. Changing it is a code change plus a
deliberate owner action.
R3: kill_switch_ok is fail-closed - an unreadable check means stop.
R4: assert_event_timestamps is the anti-lookahead rail.
R2 posture: no live broker client exists in this codebase, and this module
must never name a live broker host or port (tests scan for that).
"""

import os
from datetime import datetime

PAPER_ONLY = True

KILL_FILE = "KILL"

# Anchored to the repo root, never the process cwd: a caller running from
# elsewhere must still see the repo-root KILL file (review finding, 2026-09-02).
KILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_TS_FIELDS = ("occurred_at", "filed_at", "observed_at")


class LookaheadError(ValueError):
    """An event claims knowledge from before it was publicly knowable (R4)."""


def kill_switch_ok(root_dir=KILL_ROOT):
    """True only when the kill-switch check ran and found no KILL file.

    Fail-closed: a missing root or an errored check refuses, never proceeds.
    """
    try:
        if not os.path.isdir(root_dir):
            return False
        os.stat(os.path.join(root_dir, KILL_FILE))
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _parse_utc(event, field):
    value = event.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field} missing or not a string: {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} is not ISO 8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"{field} must be timezone-aware, got {value!r}; date-granular "
            "filings get the R4 conservative end-of-day conversion applied "
            "upstream, never a naive timestamp"
        )
    return parsed


def assert_event_timestamps(event):
    """Enforce occurred_at <= filed_at <= observed_at; return the parsed trio.

    filed_at is when the event became publicly knowable (PTR/Form 4 filing);
    observed_at is when our system first saw it. An observed_at before
    filed_at means the pipeline is using information before it existed
    publicly - the lookahead failure this rail exists to stop.
    """
    occurred, filed, observed = (_parse_utc(event, f) for f in _TS_FIELDS)
    if filed < occurred:
        raise LookaheadError(
            f"filed_at {event['filed_at']} predates occurred_at "
            f"{event['occurred_at']}"
        )
    if observed < filed:
        raise LookaheadError(
            f"observed_at {event['observed_at']} predates filed_at "
            f"{event['filed_at']} - event used before it was publicly knowable"
        )
    return occurred, filed, observed
