"""Daily paper go/no-go checklist. Not alpha. Does not POST.

Reads today's research artifact plus the latest paper snapshot / telemetry.
Feed health is optional ``--live``. Thresholds are declared in
``fixtures/params.json`` ``go_nogo`` (plus ``conviction.trim_band``).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from .params import (
    CONVICTION_MAX_GROSS_INVEST,
    go_nogo_params,
    operate_stamp,
)
from .performance import default_snapshot_path
from .research import load_research_artifact, research_artifact_path
from .runtime_env import paper_submit_flag

NOTE = (
    "Daily paper go/no-go. Declared thresholds only. Not fitted. "
    "Not alpha. Paper only. Does not POST. Does not enable live money."
)
DECISION_DIR = Path("docs/decision")
VERDICTS = ("TRADE", "HOLD", "WAIT_OPEN", "NO_GO")
Verdict = Literal["TRADE", "HOLD", "WAIT_OPEN", "NO_GO"]
BLOCKING_VERDICTS = frozenset({"HOLD", "WAIT_OPEN", "NO_GO"})
_EPS = 1e-12


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
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
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def default_decision_path(root: Path | None = None, when: datetime | None = None) -> Path:
    base = root if root is not None else Path(__file__).resolve().parent.parent
    stamp = when if when is not None else _utc_now()
    return base / DECISION_DIR / f"{_aware(stamp).date().isoformat()}.json"


def default_decision_md_path(root: Path | None = None, when: datetime | None = None) -> Path:
    return default_decision_path(root, when).with_suffix(".md")


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def load_decision_artifact(path: Path | str) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("decision artifact must be an object")
    verdict = raw.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError("decision artifact verdict is required")
    return raw


def recommend_submit_for(verdict: str) -> bool:
    if verdict == "TRADE":
        return True
    if verdict in BLOCKING_VERDICTS:
        return False
    raise ValueError(f"unknown go/no-go verdict: {verdict}")


def _feed_n(feeds: Any) -> dict[str, int | None]:
    out: dict[str, int | None] = {"quiver": None, "worldmonitor": None}
    if not isinstance(feeds, dict):
        return out
    for name in ("quiver", "worldmonitor"):
        row = feeds.get(name)
        if isinstance(row, dict) and _finite(row.get("n")) is not None:
            out[name] = int(row["n"])
        elif _finite(row) is not None:
            out[name] = int(row)
    return out


def _position_rows(performance: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(performance, dict):
        return []
    positions = performance.get("positions")
    if isinstance(positions, list):
        return [row for row in positions if isinstance(row, dict)]
    if isinstance(positions, dict):
        rows = positions.get("rows")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        symbols = positions.get("symbols")
        if isinstance(symbols, dict):
            return [{"symbol": str(name), "qty": qty} for name, qty in symbols.items()]
    return []


def _open_order_count(performance: dict[str, Any] | None) -> int:
    if not isinstance(performance, dict):
        return 0
    n = _finite(performance.get("n_open_orders"))
    if n is not None:
        return int(n)
    open_orders = performance.get("open_orders")
    if isinstance(open_orders, list):
        return len(open_orders)
    if isinstance(open_orders, dict):
        counted = _finite(open_orders.get("n"))
        if counted is not None:
            return int(counted)
        symbols = open_orders.get("symbols")
        if isinstance(symbols, list):
            return len(symbols)
    return 0


def _clock_is_open(clock: Any, performance: dict[str, Any] | None) -> bool | None:
    if isinstance(clock, dict) and "is_open" in clock:
        return bool(clock.get("is_open"))
    if isinstance(performance, dict):
        row = performance.get("clock")
        if isinstance(row, dict) and "is_open" in row:
            return bool(row.get("is_open"))
        summary = performance.get("summary")
        if isinstance(summary, dict) and "clock_is_open" in summary:
            return bool(summary.get("clock_is_open"))
    return None


def _account_field(performance: dict[str, Any] | None, field: str) -> float | None:
    if not isinstance(performance, dict):
        return None
    account = performance.get("account")
    if isinstance(account, dict) and _finite(account.get(field)) is not None:
        return _finite(account.get(field))
    if _finite(performance.get(field)) is not None:
        return _finite(performance.get(field))
    summary = performance.get("summary")
    if isinstance(summary, dict):
        return _finite(summary.get(field))
    return None


def _held_weights(
    performance: dict[str, Any] | None,
    equity: float | None,
) -> dict[str, float]:
    weights: dict[str, float] = {}
    if equity is None or equity <= 0:
        return weights
    for row in _position_rows(performance):
        symbol = row.get("symbol") or row.get("ticker")
        if not isinstance(symbol, str) or not symbol:
            continue
        market_value = _finite(row.get("market_value"))
        if market_value is None:
            qty = _finite(row.get("qty"))
            px = _finite(row.get("current_price") or row.get("avg_entry_price"))
            if qty is not None and px is not None:
                market_value = abs(qty) * px
        if market_value is None:
            continue
        weights[symbol] = abs(market_value) / equity
    return weights


def _held_names(performance: dict[str, Any] | None) -> set[str]:
    names: set[str] = set()
    for row in _position_rows(performance):
        symbol = row.get("symbol") or row.get("ticker")
        if isinstance(symbol, str) and symbol:
            names.add(symbol)
    return names


def _target_fracs(research: dict[str, Any] | None) -> dict[str, float]:
    targets: dict[str, float] = {}
    if not isinstance(research, dict):
        return targets
    book = research.get("proposed_book") or {}
    for row in book.get("targets") or []:
        if not isinstance(row, dict):
            continue
        ticker = row.get("ticker")
        frac = _finite(row.get("target_frac"))
        if isinstance(ticker, str) and ticker and frac is not None:
            targets[ticker] = frac
    return targets


def _drawdown(equity: float | None, reference: float | None) -> float | None:
    if equity is None or reference is None or reference <= 0:
        return None
    return max(0.0, (reference - equity) / reference)


def _latest_performance(root: Path, when: datetime) -> dict[str, Any] | None:
    stamp = _aware(when).date().isoformat()
    for path in (
        default_snapshot_path(root, when),
        root / "docs" / "research" / f"{stamp}-paper.json",
        root / "docs" / "telemetry" / f"{stamp}.json",
    ):
        loaded = _load_json(path)
        if loaded is not None:
            return loaded
    folder = root / "docs" / "performance"
    if not folder.is_dir():
        return None
    dated = sorted(folder.glob("????-??-??.json"))
    if not dated:
        return None
    return _load_json(dated[-1])


def _prior_equity(root: Path, when: datetime) -> tuple[str | None, float | None]:
    prior = _aware(when).date() - timedelta(days=1)
    for folder in ("performance", "telemetry"):
        loaded = _load_json(root / "docs" / folder / f"{prior.isoformat()}.json")
        if loaded is None:
            continue
        equity = _account_field(loaded, "equity")
        if equity is not None:
            return prior.isoformat(), equity
    return None, None


def build_go_nogo(
    *,
    root: Path | None = None,
    when: datetime | None = None,
    research: dict[str, Any] | None = None,
    performance: dict[str, Any] | None = None,
    prior_equity: float | None = None,
    feeds: dict[str, Any] | None = None,
    clock: dict[str, Any] | None = None,
    live: bool = False,
    live_keys_missing: list[str] | None = None,
    live_keys_present: bool | None = None,
) -> dict[str, Any]:
    """Structured daily verdict. No network. Inject fixtures in tests."""
    base = root if root is not None else Path(__file__).resolve().parent.parent
    cut = _aware(when if when is not None else _utc_now())
    today = cut.date().isoformat()
    thresholds = go_nogo_params()
    trim_band = float(thresholds["trim_band"])
    reasons: list[str] = []
    no_go = False
    hold = False
    wait_open = False
    stamp = operate_stamp()

    loaded_research = research
    research_path = research_artifact_path(cut, root=base)
    research_fresh = False
    if loaded_research is None and research_path.is_file():
        try:
            loaded_research = load_research_artifact(research_path)
        except ValueError as error:
            loaded_research = None
            no_go = True
            reasons.append(f"research artifact unreadable: {error}")
    if loaded_research is None:
        no_go = True
        reasons.append(f"missing today's research book docs/research/{today}.json")
    else:
        research_date = str(loaded_research.get("date") or "")
        if research_date != today:
            no_go = True
            reasons.append(f"research book date {research_date or 'missing'} is not today {today}")
        else:
            research_fresh = True
            reasons.append(f"research book is fresh for {today}")

    loaded_performance = performance
    if loaded_performance is None:
        loaded_performance = _latest_performance(base, cut)
    if prior_equity is None:
        _prior_date, prior_equity = _prior_equity(base, cut)
    else:
        _prior_date = None

    feed_source = feeds
    if feed_source is None and isinstance(loaded_research, dict):
        feed_source = loaded_research.get("feeds")
    counts = _feed_n(feed_source)
    quiver_n = counts["quiver"]
    world_n = counts["worldmonitor"]
    missing_keys = list(live_keys_missing or [])
    keys_ok = True if live_keys_present is None else bool(live_keys_present)
    if live and missing_keys:
        no_go = True
        keys_ok = False
        reasons.append("live feed keys missing: " + ", ".join(missing_keys) + " (fail closed)")
    elif live and not keys_ok:
        no_go = True
        reasons.append("live feed keys missing (fail closed)")
    min_q = int(thresholds["min_quiver_n"])
    min_w = int(thresholds["min_worldmonitor_n"])
    if live and keys_ok and not missing_keys:
        if quiver_n is None or quiver_n < min_q:
            no_go = True
            reasons.append(f"quiver feed unhealthy n={quiver_n} min={min_q}")
        else:
            reasons.append(f"quiver feed healthy n={quiver_n}")
        if world_n is None or world_n < min_w:
            no_go = True
            reasons.append(f"worldmonitor feed unhealthy n={world_n} min={min_w}")
        else:
            reasons.append(f"worldmonitor feed healthy n={world_n}")
    elif not live:
        if quiver_n is None and world_n is None:
            no_go = True
            reasons.append("feed counts missing from research artifact")
        else:
            if quiver_n is None or quiver_n < min_q:
                no_go = True
                reasons.append(f"quiver feed unhealthy n={quiver_n} min={min_q}")
            else:
                reasons.append(f"quiver feed healthy n={quiver_n}")
            if world_n is None or world_n < min_w:
                no_go = True
                reasons.append(f"worldmonitor feed unhealthy n={world_n} min={min_w}")
            else:
                reasons.append(f"worldmonitor feed healthy n={world_n}")

    equity = _account_field(loaded_performance, "equity")
    cash = _account_field(loaded_performance, "cash")
    start = float(thresholds["starting_cash"])
    dd_start = _drawdown(equity, start)
    dd_prior = _drawdown(equity, prior_equity)
    drawdown = None
    for value in (dd_start, dd_prior):
        if value is None:
            continue
        drawdown = value if drawdown is None else max(drawdown, value)
    soft_dd = float(thresholds["soft_dd"])
    hard_dd = float(thresholds["hard_dd"])
    equity_warn = False
    if drawdown is not None and drawdown > soft_dd + _EPS:
        equity_warn = True
        reasons.append(
            f"equity drawdown {drawdown:.4f} exceeds soft_dd={soft_dd} (warn)"
        )
        if bool(thresholds["hard_dd_blocks"]) and drawdown > hard_dd + _EPS:
            no_go = True
            reasons.append(
                f"equity drawdown {drawdown:.4f} exceeds hard_dd={hard_dd} (block)"
            )
    elif equity is not None:
        reasons.append("equity drawdown inside soft_dd")

    n_open = _open_order_count(loaded_performance)
    if n_open > 0:
        hold = True
        reasons.append(f"open paper orders n={n_open}; do not spray another submit")

    is_open = _clock_is_open(clock, loaded_performance)
    if is_open is False:
        wait_open = True
        reasons.append("market clock is closed; research still OK; wait for open to submit")
    elif is_open is True:
        reasons.append("market clock is open")

    targets = _target_fracs(loaded_research)
    weights = _held_weights(loaded_performance, equity)
    held = _held_names(loaded_performance)
    if not held:
        held = set(weights)
    off_target: list[dict[str, Any]] = []
    for ticker, target in targets.items():
        held_frac = float(weights.get(ticker, 0.0))
        if abs(held_frac - target) > trim_band + _EPS:
            off_target.append(
                {
                    "ticker": ticker,
                    "held_frac": held_frac,
                    "target_frac": target,
                    "abs_delta": abs(held_frac - target),
                    "reason": "weight_band",
                }
            )
    for ticker in sorted(held - set(targets)):
        held_frac = float(weights.get(ticker, 0.0))
        off_target.append(
            {
                "ticker": ticker,
                "held_frac": held_frac,
                "target_frac": 0.0,
                "abs_delta": held_frac,
                "reason": "drop_from_book",
            }
        )
    book = (loaded_research or {}).get("proposed_book") or {} if isinstance(loaded_research, dict) else {}
    max_gross = _finite(book.get("max_gross_invest"))
    if max_gross is None:
        max_gross = CONVICTION_MAX_GROSS_INVEST
    book_gross = _finite(book.get("book_gross"))
    if book_gross is None:
        book_gross = sum(targets.values())
    held_gross = sum(weights.values())
    if cash is not None and equity is not None and equity > 0:
        cash_frac = cash / equity
        held_from_cash = max(0.0, 1.0 - cash_frac)
        if not weights:
            held_gross = held_from_cash
    target_gross = float(book_gross) if book_gross is not None else float(max_gross)
    if (targets or held) and abs(held_gross - target_gross) > trim_band + _EPS:
        off_target.append(
            {
                "ticker": "_gross",
                "held_frac": held_gross,
                "target_frac": target_gross,
                "abs_delta": abs(held_gross - target_gross),
                "reason": "gross_band",
            }
        )
    if held_gross - float(max_gross) > trim_band + _EPS:
        off_target.append(
            {
                "ticker": "_max_gross",
                "held_frac": held_gross,
                "target_frac": float(max_gross),
                "abs_delta": held_gross - float(max_gross),
                "reason": "over_max_gross",
            }
        )
    trade = bool(off_target)
    if trade:
        reasons.append(
            f"book is off-target ({len(off_target)} name/gross band(s) beyond trim_band={trim_band})"
        )
    elif research_fresh:
        reasons.append(f"book is inside trim_band={trim_band}; no submit needed")

    if no_go:
        verdict: Verdict = "NO_GO"
    elif hold:
        verdict = "HOLD"
    elif wait_open:
        verdict = "WAIT_OPEN"
    elif trade:
        verdict = "TRADE"
    else:
        verdict = "HOLD"
        if "book is inside trim_band" not in " ".join(reasons):
            reasons.append("nothing to submit")

    recommend = recommend_submit_for(verdict)
    equity_delta = None if equity is None or prior_equity is None else equity - prior_equity
    report = {
        "mode": "paper-go-nogo",
        "note": NOTE,
        "not_alpha": True,
        "not_fitted": True,
        "paper_only": True,
        "live_money": False,
        "alpha": False,
        "date": today,
        "verdict": verdict,
        "recommend_submit": recommend,
        "reasons": reasons,
        "off_target": off_target,
        "feeds": {
            "live": bool(live),
            "quiver_n": quiver_n,
            "worldmonitor_n": world_n,
            "min_quiver_n": min_q,
            "min_worldmonitor_n": min_w,
            "keys_ok": keys_ok if live else None,
        },
        "equity": equity,
        "cash": cash,
        "equity_delta": equity_delta,
        "drawdown": drawdown,
        "equity_warn": equity_warn,
        "n_open_orders": n_open,
        "clock_is_open": is_open,
        "research_fresh": research_fresh,
        "trim_band": trim_band,
        "held_gross": held_gross,
        "target_gross": target_gross,
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "go_nogo": thresholds,
        "submit_flag": paper_submit_flag(),
        "read_only": True,
        "submitted": False,
        "order_post": "disabled",
        "ok": verdict != "NO_GO",
    }
    return report


def decision_submit_block(
    *,
    root: Path | None = None,
    when: datetime | None = None,
    artifact: dict[str, Any] | None = None,
) -> str | None:
    """Return a refuse message when today's artifact blocks --submit-paper."""
    base = root if root is not None else Path(__file__).resolve().parent.parent
    cut = when if when is not None else _utc_now()
    loaded = artifact
    path = default_decision_path(base, cut)
    if loaded is None:
        if not path.is_file():
            return None
        try:
            loaded = load_decision_artifact(path)
        except ValueError as error:
            return f"rebalance --submit-paper refused: unreadable go/no-go artifact ({error})"
    verdict = loaded.get("verdict")
    if verdict == "TRADE" and loaded.get("recommend_submit") is True:
        return None
    if verdict not in BLOCKING_VERDICTS and verdict != "TRADE":
        return (
            "rebalance --submit-paper refused: today's go/no-go artifact has "
            f"unknown verdict {verdict!r}"
        )
    if verdict == "TRADE" and loaded.get("recommend_submit") is not True:
        return (
            "rebalance --submit-paper refused: today's go/no-go verdict is TRADE "
            "but recommend_submit is false"
        )
    return (
        f"rebalance --submit-paper refused: today's go/no-go verdict is {verdict} "
        "(recommend_submit=false). Run python3 -m signal_sim go-nogo first. "
        "Owner override: --force-submit (still paper rails, not live money)."
    )


def write_go_nogo(
    report: dict[str, Any],
    path: str | Path,
    *,
    markdown: bool = False,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["decision_path"] = str(target)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if markdown:
        target.with_suffix(".md").write_text(go_nogo_markdown(payload), encoding="utf-8")
    return target


def go_nogo_markdown(report: dict[str, Any]) -> str:
    reasons = "\n".join(f"- {row}" for row in report.get("reasons") or []) or "- (none)"
    off = report.get("off_target") or []
    names = ", ".join(
        f"{row.get('ticker')} {row.get('reason')}" for row in off if isinstance(row, dict)
    )
    return (
        f"# Paper go/no-go {report.get('date')}\n\n"
        f"Not alpha. Paper only. Declared thresholds. Not fitted.\n\n"
        f"- verdict={report.get('verdict')} recommend_submit={report.get('recommend_submit')}\n"
        f"- equity_delta={report.get('equity_delta')} drawdown={report.get('drawdown')}\n"
        f"- feeds quiver_n={((report.get('feeds') or {}).get('quiver_n'))} "
        f"worldmonitor_n={((report.get('feeds') or {}).get('worldmonitor_n'))}\n"
        f"- off_target: {names or '(none)'}\n\n"
        f"Reasons:\n{reasons}\n"
    )
