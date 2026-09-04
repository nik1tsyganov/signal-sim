"""Paper rebalance tickets: print-only by default, optional local or paper apply.

Reads a paper account snapshot and the existing fixture / cluster-drift
target book, then prints intended tickets. Default is print-only: it does
not POST to a broker and does not write the local ledger. ``--apply-local``
records fixture-mark tickets through submit_paper_order. ``--submit-paper``
POSTs sized tickets to the Alpaca paper host only after require_paper_submit
rails pass. Paper IEX sizing marks never become local-ledger fills.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .conviction import paper_name_cap
from .drift import NOTE as DRIFT_NOTE
from .drift import fixture_drift_book
from .events import Event
from .fixture_load import load_fixture_events
from .indicators import UNIVERSE, rank_candidates
from .universe import is_tradable_ticker
from .paper import (
    OrderRefused,
    PaperSubmitRefused,
    assert_fills_have_provenance,
    execution_mark_failure,
    require_paper_submit,
    submit_paper_order,
)
from .params import (
    CONVICTION_DECAY_FLOOR,
    CONVICTION_MIN_SCORE,
    CONVICTION_SOFT_STOP,
    CONVICTION_TRIM_BAND,
    COST_BPS,
    DECISION_DELAY_HOURS,
    operate_stamp,
)
from .research import default_research_dir, load_entry_state, resolve_research_book
from .sells import (
    SellReason,
    decision_pnl_frac,
    parse_aware,
    select_close_reason,
    sell_clause,
)
from .sizer import size_targets
from .sim import load_mark_book, resolve_mark_book_path
from .store import EventStore

NOTE = (
    "Print-only dry-run. Target book versus paper positions (opens, adjusts, "
    "and leftover closes). Not alpha. Not a broker fill. Offline fixture-only "
    "qty prefers fixture entry_px. --live and --submit-paper prefer an observed "
    "paper IEX last trade or snapshot latestTrade when one exists, then the "
    "fixture mark. Never invents a price. Names still unmarked stay skipped. "
    "No remote paper POST. Local apply requires --apply-local and "
    "mark_source=fixture."
)
RESEARCH_SIGNAL = "research-live"
APPLY_NOTE = (
    "Local ledger apply of the same dry-run tickets. Only tickets sized from "
    "mark_kind=fixture_mark and mark_source=fixture are recorded through "
    "submit_paper_order. Paper IEX last-trade or snapshot marks may size "
    "qty but are not execution marks and are not applied. Not a broker fill. "
    "No remote paper POST."
)
APPLY_GATE = "mark_kind=fixture_mark and mark_source=fixture"
SUBMIT_NOTE = (
    "Alpaca paper POST of sized dry-run tickets. Requires "
    "SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1, paper-api host, keys, and "
    "--submit-paper. Default remains print-only. Not live money. "
    "Not alpha. Kill by setting SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0."
)
SUBMIT_GATE = "SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1 and --submit-paper and paper host"
PAPER_SIZING_SOURCE = "alpaca_paper_data"
PAPER_MARK_SKIP = "paper_mark_not_execution"
SIGNAL_DRIFT = "cluster-drift-stub"
SIGNAL_RANK = "rank-candidates"
_ACCOUNT_FIELDS = (
    "status",
    "currency",
    "cash",
    "equity",
    "buying_power",
    "trading_blocked",
    "account_blocked",
    "pattern_day_trader",
    "shorting_enabled",
)
_CLOCK_FIELDS = ("timestamp", "is_open", "next_open", "next_close")
_EPS = 1e-12
_CLIENT_ORDER_ID_MAX = 48


def paper_session_stamp(
    *,
    research_date: str | None = None,
    when: datetime | None = None,
) -> str:
    """Compact UTC session date for paper client_order_id.

    Live books use the research artifact date. Otherwise use ``when`` or now.
    Frozen fixture ``decision_at`` is not a session stamp — reusing it after
    a cancel permanently burns the Alpaca client_order_id.
    """
    raw = research_date.strip() if isinstance(research_date, str) else ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    stamp = when if when is not None else datetime.now(timezone.utc)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).strftime("%Y%m%d")


def paper_client_order_id(
    ticker: str,
    side: str,
    action: str,
    *,
    session: str,
    attempt: int = 1,
) -> str:
    """Date-scoped paper idempotency key. Stays at or under 48 characters."""
    if not isinstance(session, str) or not session.isdigit() or len(session) != 8:
        raise ValueError("paper client_order_id session must be YYYYMMDD")
    if attempt < 1:
        raise ValueError("paper client_order_id attempt must be >= 1")
    key = f"rb:{session}:{ticker}:{side}:{action}"
    if attempt > 1:
        key = f"{key}:r{attempt}"
    if len(key) > _CLIENT_ORDER_ID_MAX:
        raise ValueError("paper client_order_id exceeds 48 characters")
    return key


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str) and value.strip():
        try:
            number = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return number


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _aware_stamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_account(account: Any) -> dict[str, Any]:
    if not isinstance(account, dict):
        return {}
    return {field: account.get(field) for field in _ACCOUNT_FIELDS}


def sanitize_clock(clock: Any) -> dict[str, Any]:
    if not isinstance(clock, dict):
        return {}
    return {field: clock.get(field) for field in _CLOCK_FIELDS}


def allocation_base(account: dict[str, Any], starting_cash: float) -> float:
    """Paper equity, then cash, then the fixture starting_cash fallback."""
    for field in ("equity", "cash"):
        number = _finite_number(account.get(field))
        if number is not None and number > 0:
            return number
    return float(starting_cash)


def paper_held(positions: list[Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """Map paper position rows to signed share counts. Unknown names are skipped."""
    held: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    for item in positions:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or item.get("ticker")
        if not isinstance(symbol, str) or not symbol:
            continue
        qty = _finite_number(item.get("qty"))
        if qty is None:
            skipped.append({"ticker": symbol, "reason": "unreadable_qty"})
            continue
        side = str(item.get("side") or "long").strip().lower()
        if side == "short":
            shares = -abs(qty)
        elif side in {"long", "buy", ""}:
            shares = abs(qty)
        else:
            skipped.append({"ticker": symbol, "reason": "unknown_position_side"})
            continue
        if not is_tradable_ticker(symbol):
            skipped.append({"ticker": symbol, "reason": "invalid_ticker"})
            continue
        row: dict[str, Any] = {"shares": shares, "side": "short" if shares < 0 else "long"}
        entry_px = _finite_number(item.get("avg_entry_price") or item.get("entry_px"))
        if entry_px is not None and entry_px > 0:
            row["entry_px"] = entry_px
        opened = parse_aware(
            item.get("entry_decision_at") or item.get("created_at") or item.get("opened_at")
        )
        if opened is not None:
            row["entry_decision_at"] = opened
        entry_score = _finite_number(item.get("entry_score"))
        if entry_score is not None:
            row["entry_score"] = entry_score
        held[symbol] = row
    return held, skipped


def _ticket_payload(symbol: str, side: str, key: str) -> dict[str, str]:
    return {
        "symbol": symbol,
        "side": side,
        "type": "market",
        "time_in_force": "day",
        "client_order_id": key,
    }


def _rationale(
    *,
    signal: str,
    action: str,
    target_frac: float | None,
    have_shares: float,
    intensity: float | None = None,
    intensity_scale: float | None = None,
    mark_kind: str | None = None,
    sell_reason: SellReason | None = None,
) -> str:
    held = f"paper holds {have_shares:g}"
    if sell_reason is not None:
        parts = [signal, *sell_clause(sell_reason, target_frac), held]
    else:
        frac = 0.0 if target_frac is None else float(target_frac)
        parts = [signal, f"{action} to target_frac={frac:g}", held]
    if intensity is not None:
        scale = 1.0 if intensity_scale is None else float(intensity_scale)
        parts.append(f"intensity={float(intensity):g} scale={scale:g}")
    if mark_kind and mark_kind != "fixture_mark":
        parts.append(f"mark={mark_kind}")
    return "; ".join(parts)


def _sizing_px(mark: dict[str, Any] | None) -> float | None:
    if mark is None or mark.get("unused"):
        return None
    px = _finite_number(mark.get("entry_px"))
    if px is None or px <= 0:
        return None
    return px


def fixture_sizing_marks(marks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Keep only honest fixture execution marks for print-only sizing."""
    resolved: dict[str, dict[str, Any]] = {}
    for ticker, row in marks.items():
        if not isinstance(row, dict):
            continue
        if execution_mark_failure(row.get("kind"), row.get("source")) is not None:
            continue
        px = _sizing_px(row)
        if px is None:
            continue
        resolved[str(ticker)] = {
            "entry_px": px,
            "kind": "fixture_mark",
            "source": "fixture",
        }
    return resolved


def _honest_paper_mark(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    px = _sizing_px(row)
    if px is None:
        return None
    kind = str(row.get("kind") or "last_trade")
    source = str(row.get("source") or PAPER_SIZING_SOURCE)
    if kind == "fixture_mark" or source == "fixture":
        return None
    return {"entry_px": px, "kind": kind, "source": source}


def resolve_sizing_marks(
    needed: list[str],
    fixture_marks: dict[str, Any],
    client: Any | None = None,
    *,
    prefer_paper: bool = False,
    universe: tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    """Offline: fixture first. --live / --submit-paper: observed paper mark first.

    Never invents a price. Paper IEX last trade / snapshot only when the
    client returns a finite positive mark.
    """
    allowed = set(UNIVERSE if universe is None else universe)
    fixture = fixture_sizing_marks(fixture_marks)
    paper: dict[str, dict[str, Any]] = {}
    wanted = [ticker for ticker in needed if ticker in allowed]
    if client is not None and hasattr(client, "sizing_marks") and wanted:
        if hasattr(client, "set_universe"):
            client.set_universe(allowed)
        try:
            fetched = client.sizing_marks(wanted) or {}
        except Exception:
            fetched = {}
        if isinstance(fetched, dict):
            for ticker in wanted:
                mark = _honest_paper_mark(fetched.get(ticker))
                if mark is not None:
                    paper[ticker] = mark
    resolved: dict[str, dict[str, Any]] = {}
    for ticker in needed:
        if ticker not in allowed:
            continue
        paper_row = paper.get(ticker)
        fixture_row = fixture.get(ticker)
        chosen = paper_row or fixture_row if prefer_paper else fixture_row or paper_row
        if chosen is not None:
            resolved[ticker] = chosen
    return resolved


def local_apply_failure(ticket: dict[str, Any]) -> str | None:
    """Refuse a local fill unless the ticket carries an explicit fixture mark.

    Missing labels do not default to fixture_mark. Paper IEX sizing marks
    return paper_mark_not_execution so they cannot be claimed as fills.
    """
    kind = ticket.get("mark_kind")
    source = ticket.get("mark_source")
    if not isinstance(kind, str) or not kind.strip():
        return "execution mark must be fixture_mark"
    if not isinstance(source, str) or not source.strip():
        return "execution mark must be fixture_mark"
    kind_text = kind.strip().lower()
    source_text = source.strip().lower()
    if source_text == PAPER_SIZING_SOURCE or kind_text in {"last_trade", "snapshot"}:
        return PAPER_MARK_SKIP
    return execution_mark_failure(kind_text, source_text)


def _intensity_cut(events: list[Event]) -> datetime:
    cut = datetime.now(timezone.utc)
    latest = None
    for event in events:
        observed = event.observed_at
        if latest is None or observed > latest:
            latest = observed
    if latest is not None and latest >= cut:
        return latest + timedelta(microseconds=1)
    return cut


def _row_score(row: dict[str, Any] | None) -> float | None:
    if row is None:
        return None
    return _finite_number(row.get("score"))


def plan_rebalance_tickets(
    *,
    targets: list[dict[str, Any]],
    marks: dict[str, dict[str, Any]],
    held: dict[str, dict[str, Any]],
    cash: float,
    allocation: float,
    cost_bps: float,
    signal: str,
    decision_at: str,
    session: str | None = None,
    min_score: float | None = None,
    trim_band: float = 0.0,
    now: datetime | None = None,
    horizon_hours: float | None = None,
    decay_floor: float | None = None,
    soft_stop: float | None = None,
    entry_scores: dict[str, float] | None = None,
    entry_decision_at: dict[str, datetime] | None = None,
    entry_px: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Share-accurate tickets. Same delta as fixture replay; print-only.

    Research-live passes a score floor, overweight band, and declared exits.
    Default ``min_score=None`` / ``trim_band=0`` keeps the fixture drift planner.
    Soft-stop MTM uses the decision-time sizing mark versus paper entry_px.
    """
    tickets: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    wanted = {str(row["ticker"]): row for row in targets}
    reserved_cash = float(cash)
    band = 0.0 if trim_band is None else max(0.0, float(trim_band))
    scores = dict(entry_scores or {})
    entries = dict(entry_decision_at or {})
    cost_px = dict(entry_px or {})

    def skip(ticker: str, reason: str) -> None:
        skipped.append({"ticker": ticker, "reason": reason})

    def emit(
        ticker: str,
        side: str,
        trade_frac: float,
        mark_px: float,
        action: str,
        have_shares: float,
        target_frac: float | None,
        qty: float,
        sell_reason: SellReason | None = None,
    ) -> None:
        key = paper_client_order_id(
            ticker,
            side,
            action,
            session=session or paper_session_stamp(),
        )
        notional = abs(qty) * float(mark_px)
        mark = marks.get(ticker) or {}
        mark_kind = str(mark.get("kind") or "fixture_mark")
        mark_source = str(mark.get("source") or "fixture")
        intensity = None
        intensity_scale = None
        if target_frac is not None:
            intensity = wanted.get(ticker, {}).get("intensity")
            intensity_scale = wanted.get(ticker, {}).get("intensity_scale")
        tickets.append(
            {
                "symbol": ticker,
                "side": side,
                "qty": qty,
                "notional": notional,
                "size_frac": float(trade_frac),
                "mark_px": float(mark_px),
                "mark_kind": mark_kind,
                "mark_source": mark_source,
                "action": action,
                "sell_reason": sell_reason,
                "rationale": _rationale(
                    signal=signal,
                    action=action,
                    target_frac=target_frac,
                    have_shares=have_shares,
                    intensity=None if intensity is None else float(intensity),
                    intensity_scale=None if intensity_scale is None else float(intensity_scale),
                    mark_kind=mark_kind,
                    sell_reason=sell_reason,
                ),
                "submitted": False,
                "payload": _ticket_payload(ticker, side, key),
            }
        )

    def close_reason(ticker: str, have_shares: float, mark_px: float | None) -> SellReason | None:
        row = wanted.get(ticker)
        position = held.get(ticker) or {}
        opened = entries.get(ticker)
        if opened is None:
            opened = parse_aware(position.get("entry_decision_at"))
        cost = cost_px.get(ticker)
        if cost is None:
            cost = _finite_number(position.get("entry_px"))
        prior_score = scores.get(ticker)
        if prior_score is None:
            prior_score = _finite_number(position.get("entry_score"))
        return select_close_reason(
            in_book=row is not None,
            score=_row_score(row) if row is not None else _row_score(position),
            min_score=min_score,
            decay_floor=decay_floor,
            entry_score=prior_score,
            now=now,
            entry_decision_at=opened,
            horizon_hours=horizon_hours,
            pnl_frac=decision_pnl_frac(
                entry_px=cost,
                mark_px=mark_px,
                shares=have_shares,
            ),
            soft_stop=soft_stop,
        )

    for ticker, position in list(held.items()):
        mark = marks.get(ticker)
        sell_px = _sizing_px(mark)
        have_shares = float(position["shares"])
        sell_reason = close_reason(ticker, have_shares, sell_px)
        if sell_reason is None:
            continue
        if sell_px is None:
            skip(ticker, "held_no_mark")
            continue
        trade_frac = abs(have_shares) * sell_px / allocation
        if trade_frac <= _EPS:
            continue
        qty = -have_shares if have_shares > 0 else abs(have_shares)
        side = "sell" if have_shares > 0 else "buy"
        fee = allocation * trade_frac * cost_bps / 10000.0
        reserved_cash += abs(have_shares) * sell_px - fee
        emit(ticker, side, trade_frac, sell_px, "close", have_shares, None, qty, sell_reason)

    for row in targets:
        ticker = str(row["ticker"])
        have_shares = float(held.get(ticker, {}).get("shares", 0.0))
        mark = marks.get(ticker)
        mark_px = _sizing_px(mark)
        if close_reason(ticker, have_shares, mark_px) is not None:
            continue
        if mark_px is None:
            skip(ticker, "no_mark")
            continue
        target_shares = allocation * float(row["target_frac"]) / mark_px
        delta_shares = target_shares - have_shares
        if abs(delta_shares) <= _EPS:
            continue
        if delta_shares < 0:
            delta_shares = max(delta_shares, -have_shares)
        trade_frac = abs(delta_shares) * mark_px / allocation
        if trade_frac <= _EPS:
            continue
        if delta_shares < 0 and band > 0 and trade_frac <= band + _EPS:
            continue
        if (
            delta_shares > 0
            and band > 0
            and abs(have_shares) > _EPS
            and trade_frac <= band + _EPS
        ):
            continue
        if delta_shares > 0:
            notional = trade_frac * allocation
            fee = notional * cost_bps / 10000.0
            if notional + fee - reserved_cash > 1e-9:
                skip(ticker, "cash_constraint")
                continue
            reserved_cash -= notional + fee
        side = "buy" if delta_shares > 0 else "sell"
        action = "open" if abs(have_shares) <= _EPS else "adjust"
        sell_reason = "overweight_band" if side == "sell" and band > 0 else None
        emit(
            ticker,
            side,
            trade_frac,
            mark_px,
            action,
            have_shares,
            float(row["target_frac"]),
            delta_shares,
            sell_reason,
        )
    return tickets, skipped


def _fillable_candidates(
    candidates: list[dict[str, Any]],
    marks: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    fillable: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for row in candidates:
        ticker = str(row["ticker"])
        if _sizing_px(marks.get(ticker)) is None:
            skipped.append({"ticker": ticker, "reason": "no_mark"})
            continue
        fillable.append(row)
    return fillable, skipped


def proposed_rebalance(
    *,
    fixtures: Path | None = None,
    mark_book_path: Path | str | None = None,
    account: dict[str, Any] | None = None,
    positions: list[Any] | None = None,
    clock: dict[str, Any] | None = None,
    client: Any | None = None,
    signal: str = "drift",
    intensity: bool = False,
    live: bool = False,
    live_events: list[Event] | None = None,
    prefer_paper_marks: bool | None = None,
    universe: tuple[str, ...] | None = None,
    research: dict[str, Any] | None = None,
    research_path: Path | str | None = None,
) -> dict[str, Any]:
    """Read paper snapshot + target book; return print-only tickets."""
    if signal not in {"drift", "rank"}:
        raise ValueError("signal must be 'drift' or 'rank'")
    if intensity and not live and signal != "drift":
        raise ValueError("intensity overlay requires the cluster-drift book")
    if live and signal == "rank":
        raise ValueError("intensity overlay requires the cluster-drift book")
    if client is not None:
        if account is None:
            account = client.account()
        if positions is None:
            positions = client.positions()
        if clock is None:
            clock = client.clock()
    if account is None:
        raise ValueError("paper account snapshot is required")
    if positions is None:
        positions = []

    root = fixtures if fixtures is not None else Path(__file__).resolve().parent.parent / "fixtures"
    resolved = resolve_mark_book_path(mark_book_path) if mark_book_path else None
    book = load_mark_book(resolved)
    decision_at = book["decision_at"].isoformat().replace("+00:00", "Z")
    horizon_hours = (book["exit_at"] - book["decision_at"]).total_seconds() / 3600.0
    events = [
        event for event in load_fixture_events(root) if event.observed_at <= book["decision_at"]
    ]

    live_summary = None
    extra_events: list[Event] = []
    intensity_when = None
    apply_intensity = bool(intensity or live)
    research_report = None
    operating = tuple(universe) if universe is not None else UNIVERSE
    use_paper_marks = bool(live) if prefer_paper_marks is None else bool(prefer_paper_marks)
    if live:
        research_report = resolve_research_book(
            fixtures=root,
            mark_book_path=resolved,
            research=research,
            research_path=research_path,
            live_events=live_events,
        )
        operating = tuple(research_report["universe"]["operating"])
        extra_events = []
        live_summary = research_report.get("feeds")
        intensity_when = _intensity_cut([])
        candidates = list(research_report["proposed_book"]["targets"])
        signal_name = RESEARCH_SIGNAL
        signal_note = research_report.get("note") or NOTE
        drift_book = {"intensity_note": research_report.get("note"), "intensity": research_report.get("intensity"), "intensity_cut": "now"}
        apply_intensity = True
    elif signal == "drift":
        drift_book = fixture_drift_book(
            root,
            resolved,
            intensity=apply_intensity,
            extra_events=extra_events or None,
            intensity_when=intensity_when,
            universe=operating if universe is not None else None,
        )
        candidates = list(drift_book["targets"])
        signal_name = SIGNAL_DRIFT
        signal_note = DRIFT_NOTE
    else:
        with EventStore() as store:
            store.add_many(events)
            candidates = rank_candidates(store.all(), window_end=book["decision_at"], universe=operating)
        signal_name = SIGNAL_RANK
        signal_note = "Existing rank_candidates at the mark-book decision_at. Not a new alpha."
        drift_book = {}

    held, held_skips = paper_held(list(positions))
    mark_universe = tuple(dict.fromkeys([*operating, *held]))
    needed = [str(row["ticker"]) for row in candidates]
    needed.extend(ticker for ticker in held if ticker not in needed)
    sizing_marks = resolve_sizing_marks(
        needed,
        book["marks"],
        client,
        prefer_paper=use_paper_marks,
        universe=mark_universe,
    )
    fillable, pre_skips = _fillable_candidates(candidates, sizing_marks)
    name_cap = float(book["max_name_frac"])
    sell_min_score = None
    sell_band = 0.0
    sell_now = None
    sell_horizon = None
    sell_decay = None
    sell_soft_stop = None
    sell_entry_scores: dict[str, float] = {}
    sell_entry_at: dict[str, datetime] = {}
    sell_entry_px: dict[str, float] = {}
    live_gross = float(book["max_gross_frac"])
    if live:
        name_cap = paper_name_cap(name_cap)
        sell_min_score = CONVICTION_MIN_SCORE
        sell_band = CONVICTION_TRIM_BAND
        sell_decay = CONVICTION_DECAY_FLOOR
        sell_soft_stop = CONVICTION_SOFT_STOP
        sell_horizon = horizon_hours
        if research_report is not None:
            sell_now = parse_aware(research_report.get("research_at"))
            stamped_invest = (research_report.get("proposed_book") or {}).get(
                "max_gross_invest"
            )
            if stamped_invest is not None:
                live_gross = float(stamped_invest)
            before = research_report.get("date")
            if isinstance(before, str) and before:
                loaded = load_entry_state(
                    default_research_dir(root.parent if root.name == "fixtures" else root),
                    before=before,
                    symbols=list(held),
                )
                for ticker, row in loaded.items():
                    score = _finite_number(row.get("entry_score"))
                    opened = parse_aware(row.get("entry_decision_at"))
                    if score is not None:
                        sell_entry_scores[ticker] = score
                    if opened is not None:
                        sell_entry_at[ticker] = opened
        for ticker, position in held.items():
            cost = _finite_number(position.get("entry_px"))
            if cost is not None:
                sell_entry_px[ticker] = cost
        fillable = [
            {key: value for key, value in row.items() if key != "intensity_scale"}
            for row in fillable
        ]
    targets, size_skips = size_targets(
        fillable,
        size_frac=float(book["size_frac"]),
        horizon_hours=horizon_hours,
        max_gross_frac=live_gross,
        max_name_frac=name_cap,
    )
    safe_account = sanitize_account(account)
    allocation = allocation_base(safe_account, float(book["starting_cash"]))
    cash = _finite_number(safe_account.get("cash"))
    if cash is None:
        cash = allocation
    research_date = None
    if isinstance(research_report, dict):
        raw_date = research_report.get("date")
        if isinstance(raw_date, str) and raw_date.strip():
            research_date = raw_date.strip()
    session = paper_session_stamp(research_date=research_date)
    tickets, plan_skips = plan_rebalance_tickets(
        targets=targets,
        marks=sizing_marks,
        held=held,
        cash=cash,
        allocation=allocation,
        cost_bps=float(book.get("cost_bps", COST_BPS)),
        signal=signal_name,
        decision_at=decision_at,
        session=session,
        min_score=sell_min_score,
        trim_band=sell_band,
        now=sell_now,
        horizon_hours=sell_horizon,
        decay_floor=sell_decay,
        soft_stop=sell_soft_stop,
        entry_scores=sell_entry_scores or None,
        entry_decision_at=sell_entry_at or None,
        entry_px=sell_entry_px or None,
    )
    skipped = [*held_skips, *pre_skips, *size_skips, *plan_skips]
    stamp = operate_stamp()
    safe_clock = sanitize_clock(clock)
    paper_marked = sorted(
        ticker
        for ticker, row in sizing_marks.items()
        if row.get("source") == PAPER_SIZING_SOURCE
    )
    fixture_marked = sorted(
        ticker for ticker, row in sizing_marks.items() if row.get("source") == "fixture"
    )
    unmarked = sorted({ticker for ticker in needed if ticker not in sizing_marks})
    report = {
        "mode": "paper-rebalance-dry-run",
        "note": NOTE,
        "signal": signal_name,
        "signal_note": signal_note,
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "decision_at": decision_at,
        "session": session,
        "printed_at": _utc_now(),
        "clock": safe_clock,
        "account": safe_account,
        "positions": {
            "n": len(held),
            "symbols": {
                ticker: f"{row['shares']:g}" for ticker, row in sorted(held.items())
            },
        },
        "allocation": allocation,
        "targets": targets,
        "tickets": tickets,
        "skipped": skipped,
        "n_tickets": len(tickets),
        "n_skipped": len(skipped),
        "universe": list(operating),
        "prefer_paper_marks": use_paper_marks,
        "marks": {
            "fixture": fixture_marked,
            "paper_data": paper_marked,
            "unmarked": unmarked,
        },
        "order_post": "disabled",
        "submitted": False,
        "local_applied": False,
        "apply_gate": APPLY_GATE,
        "ok": True,
    }
    if apply_intensity:
        report["intensity_note"] = drift_book.get("intensity_note")
        report["intensity"] = drift_book.get("intensity")
        report["intensity_cut"] = drift_book.get("intensity_cut", "decision_at")
    if live_summary is not None:
        report["live_intel"] = live_summary
    if research_report is not None:
        report["research_date"] = research_report.get("date")
        report["research_universe"] = research_report.get("universe")
    return report


def _apply_event_ids(
    ticker: str,
    events: list[Event],
    decision_at: str,
) -> list[str]:
    ids = [event.id for event in events if event.ticker == ticker]
    if ids:
        return ids
    return [f"rebalance-apply:{decision_at}:{ticker}"]


def _apply_filled_at(decision_at: str) -> str:
    parsed = datetime.fromisoformat(decision_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("decision_at must be a timezone-aware timestamp")
    filled = parsed + timedelta(hours=DECISION_DELAY_HOURS)
    return filled.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def apply_local_rebalance(
    report: dict[str, Any],
    *,
    ledger_path: str | Path,
    fixtures: Path | None = None,
    audit_path: str | None = None,
    kill_root: str | None = None,
    filled_at: str | None = None,
) -> dict[str, Any]:
    """Record dry-run tickets on the local ledger. Fixture marks only.

    Computes nothing new: the caller must pass the same report as print-only
    dry-run. Paper-mark-sized tickets are skipped. submit_paper_order is the
    only write path. This never POSTs to a broker.
    """
    if not isinstance(report, dict):
        raise ValueError("rebalance report is required")
    tickets = report.get("tickets")
    if not isinstance(tickets, list):
        raise ValueError("rebalance report tickets are required")
    decision_at = report.get("decision_at")
    if not isinstance(decision_at, str) or not decision_at:
        raise ValueError("rebalance report decision_at is required")
    allocation = _finite_number(report.get("allocation"))
    if allocation is None or allocation <= 0:
        raise ValueError("rebalance report allocation must be a positive number")
    params = report.get("params") if isinstance(report.get("params"), dict) else {}
    cost_bps = _finite_number(params.get("cost_bps"))
    if cost_bps is None:
        cost_bps = float(COST_BPS)
    root = fixtures if fixtures is not None else Path(__file__).resolve().parent.parent / "fixtures"
    parsed_decision = datetime.fromisoformat(decision_at.replace("Z", "+00:00"))
    if parsed_decision.tzinfo is None or parsed_decision.utcoffset() is None:
        raise ValueError("rebalance report decision_at must be timezone-aware")
    events = [
        event for event in load_fixture_events(root) if event.observed_at <= parsed_decision
    ]
    ledger = str(ledger_path)
    effective_audit = audit_path if audit_path is not None else ledger + ".audit.jsonl"
    fill_stamp = filled_at if filled_at is not None else _apply_filled_at(decision_at)

    applied: list[dict[str, Any]] = []
    apply_skipped: list[dict[str, str]] = []
    written: list[dict[str, Any]] = []
    ok = True

    for ticket in tickets:
        row = dict(ticket) if isinstance(ticket, dict) else {}
        ticker = str(row.get("symbol") or "")
        skip_reason = local_apply_failure(row)
        if skip_reason is not None:
            apply_skipped.append({"ticker": ticker or "unknown", "reason": skip_reason})
            row["submitted"] = False
            row["local_filled"] = False
            written.append(row)
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        idempotency_key = payload.get("client_order_id")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            idempotency_key = paper_client_order_id(
                ticker,
                str(row.get("side") or "buy"),
                str(row.get("action") or "open"),
                session=paper_session_stamp(),
            )
        cost = allocation * float(row["size_frac"]) * cost_bps / 10000.0
        try:
            filled = submit_paper_order(
                {
                    "ticker": ticker,
                    "side": row["side"],
                    "size_frac": float(row["size_frac"]),
                    "event_ids": _apply_event_ids(ticker, events, decision_at),
                    "decision_at": decision_at,
                    "idempotency_key": idempotency_key,
                },
                ledger_path=ledger,
                mark_px=float(row["mark_px"]),
                audit_path=effective_audit,
                kill_root=kill_root,
                cost=cost,
                filled_at=fill_stamp,
                mark_kind=row.get("mark_kind"),
                mark_source=row.get("mark_source"),
            )
        except OrderRefused as error:
            reason = str(error)
            apply_skipped.append({"ticker": ticker, "reason": reason})
            row["submitted"] = False
            row["local_filled"] = False
            written.append(row)
            lowered = reason.lower()
            if "duplicate idempotency_key" not in lowered:
                ok = False
            continue
        applied.append(filled)
        row["submitted"] = False
        row["local_filled"] = True
        row["order_id"] = filled["order_id"]
        written.append(row)

    if applied:
        assert_fills_have_provenance(ledger, effective_audit)

    out = dict(report)
    out["mode"] = "paper-rebalance-apply-local"
    out["note"] = APPLY_NOTE
    out["tickets"] = written
    out["order_post"] = "disabled"
    out["submitted"] = False
    out["local_applied"] = True
    out["apply_gate"] = APPLY_GATE
    out["applied"] = applied
    out["apply_skipped"] = apply_skipped
    out["n_applied"] = len(applied)
    out["n_apply_skipped"] = len(apply_skipped)
    out["ledger_path"] = ledger
    out["audit_path"] = effective_audit
    out["ok"] = ok
    return out


def clear_sizing_failure(ticket: dict[str, Any], universe: tuple[str, ...] | None = None) -> str | None:
    """Refuse a remote paper POST unless qty or notional is a clear size."""
    if not isinstance(ticket, dict):
        return "ticket must be a mapping"
    symbol = ticket.get("symbol") or ticket.get("ticker")
    allowed = UNIVERSE if universe is None else universe
    if symbol not in allowed:
        return "ticker not in universe"
    side = ticket.get("side")
    if side not in {"buy", "sell"}:
        return "side must be 'buy' or 'sell'"
    qty = _finite_number(ticket.get("qty"))
    notional = _finite_number(ticket.get("notional"))
    if qty is not None and abs(qty) > _EPS:
        return None
    if notional is not None and abs(notional) > _EPS:
        return None
    return "qty or notional must be a positive finite number"


def _paper_ticket_sort(ticket: dict[str, Any]) -> tuple[float, float, str]:
    notional = abs(_finite_number(ticket.get("notional")) or 0.0)
    qty = abs(_finite_number(ticket.get("qty")) or 0.0)
    return (notional, qty, str(ticket.get("symbol") or ""))


def submit_paper_rebalance(
    report: dict[str, Any],
    client: Any,
    *,
    limit: int = 1,
    explicit: bool = True,
) -> dict[str, Any]:
    """POST sized dry-run tickets to Alpaca paper. Hard-gated.

    Default limit is 1 (smallest notional). This never writes the local
    ledger and never targets a live host.
    """
    require_paper_submit(explicit=explicit)
    if not isinstance(report, dict):
        raise ValueError("rebalance report is required")
    tickets = report.get("tickets")
    if not isinstance(tickets, list):
        raise ValueError("rebalance report tickets are required")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise PaperSubmitRefused("rebalance --submit-paper --limit must be a positive integer")

    operating = report.get("universe")
    allowed = tuple(operating) if isinstance(operating, list) else UNIVERSE
    ticket_names = []
    for ticket in tickets:
        if isinstance(ticket, dict):
            name = ticket.get("symbol") or ticket.get("ticker")
            if is_tradable_ticker(name) and name not in ticket_names:
                ticket_names.append(str(name))
    allowed = tuple(dict.fromkeys([*allowed, *ticket_names]))
    if hasattr(client, "set_universe"):
        client.set_universe(allowed)
    indexed: list[tuple[int, dict[str, Any]]] = []
    submit_skipped: list[dict[str, str]] = []
    for index, ticket in enumerate(tickets):
        row = dict(ticket) if isinstance(ticket, dict) else {}
        reason = clear_sizing_failure(row, universe=allowed)
        if reason is not None:
            submit_skipped.append(
                {"ticker": str(row.get("symbol") or "unknown"), "reason": reason}
            )
            continue
        indexed.append((index, row))
    indexed.sort(key=lambda item: _paper_ticket_sort(item[1]))
    chosen_indexes = {index for index, _row in indexed[:limit]}
    for index, row in indexed[limit:]:
        submit_skipped.append(
            {
                "ticker": str(row.get("symbol") or "unknown"),
                "reason": "over_limit",
            }
        )

    written: list[dict[str, Any]] = []
    posted: list[dict[str, Any]] = []
    ok = True
    for index, ticket in enumerate(tickets):
        row = dict(ticket) if isinstance(ticket, dict) else {}
        if index not in chosen_indexes:
            row["submitted"] = False
            written.append(row)
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        qty = abs(_finite_number(row.get("qty")) or 0.0)
        proposal = {
            "symbol": row.get("symbol"),
            "side": row.get("side"),
            "qty": qty,
            "client_order_id": payload.get("client_order_id"),
        }
        symbol = str(row.get("symbol") or "unknown")
        try:
            result = client.post_paper_order(proposal, explicit=explicit)
        except (PaperSubmitRefused, ValueError, RuntimeError) as error:
            reason = str(error)
            submit_skipped.append({"ticker": symbol, "reason": reason})
            row["submitted"] = False
            row["paper_error"] = reason
            written.append(row)
            ok = False
            continue
        posted.append(result)
        row["submitted"] = True
        row["order_id"] = result.get("id")
        row["order_status"] = result.get("status")
        row["duplicate"] = bool(result.get("duplicate"))
        written.append(row)

    out = dict(report)
    out["mode"] = "paper-rebalance-submit-paper"
    out["note"] = SUBMIT_NOTE
    out["tickets"] = written
    out["order_post"] = "paper"
    out["submitted"] = bool(posted)
    out["local_applied"] = False
    out["submit_gate"] = SUBMIT_GATE
    out["paper_orders"] = posted
    out["submit_skipped"] = submit_skipped
    out["n_paper_submitted"] = len(posted)
    out["n_submit_skipped"] = len(submit_skipped)
    out["submit_limit"] = limit
    out["ok"] = ok
    return out
