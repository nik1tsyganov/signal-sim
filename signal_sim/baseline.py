"""Walk-forward conviction vs equal-weight top-K. Not a live trading path.

Forward-only fixture marks. Declared caps only. Not fitted. Not alpha.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .conviction import conviction_targets, equal_weight_targets
from .params import (
    CONVICTION_MAX_GROSS_INVEST,
    CONVICTION_MAX_NAME_FRAC,
    DECISION_DELAY_HOURS,
    STARTING_CASH,
    conviction_params,
    operate_stamp,
)
from .research import default_research_dir
from .walkforward import embargo_hours

NOTE = (
    "Fixture-mark walk-forward of conviction target_frac versus equal-weight "
    "top-K under the same max_gross_invest / max_name_frac. Forward-only marks. "
    "Not fitted. Not alpha. Not a live trading path. Paper only."
)
DEFAULT_SERIES = (
    Path(__file__).resolve().parent.parent / "fixtures" / "baseline" / "series.json"
)
BASELINE_DIR = Path("docs/baseline")
_EPS = 1e-12


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_stamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field} must be a timezone-aware timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be a timezone-aware timestamp")
    return parsed.astimezone(timezone.utc)


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


def default_baseline_path(root: Path | None = None, when: datetime | None = None) -> Path:
    base = root if root is not None else Path(__file__).resolve().parent.parent
    stamp = when if when is not None else _utc_now()
    return base / BASELINE_DIR / f"{_aware(stamp).date().isoformat()}.json"


def live_research_day_count(root: Path | None = None) -> int:
    base = root if root is not None else Path(__file__).resolve().parent.parent
    folder = default_research_dir(base)
    if not folder.is_dir():
        return 0
    n = 0
    for path in folder.glob("????-??-??.json"):
        name = path.name
        if name.endswith("-paper.json") or name.endswith("-equal-weight.json"):
            continue
        n += 1
    return n


def _fixture_mark(row: Any) -> dict[str, float] | None:
    if not isinstance(row, dict):
        return None
    if str(row.get("kind") or "") != "fixture_mark":
        return None
    if str(row.get("source") or "") != "fixture":
        return None
    entry = _finite(row.get("entry_px"))
    exit_px = _finite(row.get("exit_px"))
    if entry is None or exit_px is None or entry <= 0 or exit_px <= 0:
        return None
    return {"entry_px": entry, "exit_px": exit_px}


def _rank_rows(step: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in step.get("rank") or []:
        if not isinstance(row, dict) or not row.get("ticker"):
            continue
        score = _finite(row.get("score"))
        if score is None:
            continue
        rows.append({"ticker": str(row["ticker"]), "score": score})
    if rows:
        return rows
    for row in (step.get("proposed_book") or {}).get("targets") or []:
        if not isinstance(row, dict) or not row.get("ticker"):
            continue
        score = _finite(row.get("score"))
        frac = _finite(row.get("target_frac"))
        item = {"ticker": str(row["ticker"]), "score": score if score is not None else 1.0}
        if frac is not None:
            item["target_frac"] = frac
        rows.append(item)
    return rows


def _conviction_book(step: dict[str, Any], *, horizon_hours: float) -> list[dict[str, Any]]:
    stamped = []
    for row in (step.get("proposed_book") or {}).get("targets") or []:
        if not isinstance(row, dict) or not row.get("ticker"):
            continue
        frac = _finite(row.get("target_frac"))
        if frac is None or frac <= _EPS:
            continue
        stamped.append({"ticker": str(row["ticker"]), "target_frac": frac, "score": row.get("score")})
    if stamped:
        return stamped
    rows = _rank_rows(step)
    targets, _skipped = conviction_targets(rows, horizon_hours=horizon_hours)
    return [
        {"ticker": row["ticker"], "target_frac": float(row["target_frac"]), "score": row.get("score")}
        for row in targets
    ]


def _equal_book(step: dict[str, Any], *, horizon_hours: float) -> list[dict[str, Any]]:
    rows = _rank_rows(step)
    if not rows:
        rows = [
            {"ticker": row["ticker"], "score": _finite(row.get("score")) or 1.0}
            for row in _conviction_book(step, horizon_hours=horizon_hours)
        ]
    targets, _skipped = equal_weight_targets(rows, horizon_hours=horizon_hours)
    return [
        {"ticker": row["ticker"], "target_frac": float(row["target_frac"]), "score": row.get("score")}
        for row in targets
    ]


def mark_book_equity(
    book: list[dict[str, Any]],
    marks: dict[str, dict[str, float]],
    *,
    starting_equity: float,
    max_gross_invest: float = CONVICTION_MAX_GROSS_INVEST,
    max_name_frac: float = CONVICTION_MAX_NAME_FRAC,
) -> dict[str, Any]:
    """Paper-style MTM: allocate target_frac at entry_px, mark at exit_px.

    Names without a fixture mark stay cash. Never uses a later step's marks.
    """
    if starting_equity <= 0:
        raise ValueError("starting_equity must be positive")
    invested = 0.0
    exit_value = 0.0
    held: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for row in book:
        ticker = str(row["ticker"])
        frac = min(float(max_name_frac), max(0.0, float(row["target_frac"])))
        if frac <= _EPS:
            skipped.append({"ticker": ticker, "reason": "non_positive_target"})
            continue
        if invested / starting_equity + frac - max_gross_invest > _EPS:
            skipped.append({"ticker": ticker, "reason": "gross_frac_cap"})
            continue
        mark = marks.get(ticker)
        if mark is None:
            skipped.append({"ticker": ticker, "reason": "no_mark"})
            continue
        notional = starting_equity * frac
        shares = notional / mark["entry_px"]
        end = shares * mark["exit_px"]
        invested += notional
        exit_value += end
        held.append(
            {
                "ticker": ticker,
                "target_frac": frac,
                "entry_px": mark["entry_px"],
                "exit_px": mark["exit_px"],
                "notional": notional,
                "exit_value": end,
                "pnl": end - notional,
            }
        )
    cash = starting_equity - invested
    ending = cash + exit_value
    return {
        "starting_equity": starting_equity,
        "ending_equity": ending,
        "total_pnl": ending - starting_equity,
        "invested": invested,
        "cash": cash,
        "held": held,
        "skipped": skipped,
    }


def load_baseline_series(path: Path | None = None) -> list[dict[str, Any]]:
    """Load a frozen fixture series. Folds must expand and honor purge/embargo."""
    series_path = DEFAULT_SERIES if path is None else path
    raw = json.loads(series_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("baseline series must be an object")
    steps = raw.get("steps")
    if not isinstance(steps, list) or len(steps) < 2:
        raise ValueError("baseline series steps must be a list of at least two steps")
    delay = float(raw.get("decision_delay_hours", DECISION_DELAY_HOURS))
    books: list[dict[str, Any]] = []
    previous = None
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(f"steps[{index}] must be an object")
        decision_at = _parse_stamp(step.get("decision_at"), "decision_at")
        exit_at = _parse_stamp(step.get("exit_at"), "exit_at")
        if exit_at <= decision_at:
            raise ValueError("exit_at must be after decision_at")
        marks: dict[str, dict[str, float]] = {}
        raw_marks = step.get("marks")
        if not isinstance(raw_marks, dict):
            raise ValueError(f"steps[{index}].marks must be an object")
        for ticker, row in raw_marks.items():
            parsed = _fixture_mark(row)
            if parsed is not None:
                marks[str(ticker)] = parsed
        fold = {
            "date": str(step.get("date") or decision_at.date().isoformat()),
            "decision_at": decision_at,
            "exit_at": exit_at,
            "horizon_hours": (exit_at - decision_at).total_seconds() / 3600.0,
            "decision_delay_hours": delay,
            "rank": _rank_rows(step),
            "proposed_book": step.get("proposed_book") if isinstance(step.get("proposed_book"), dict) else {},
            "marks": marks,
            "raw": step,
        }
        if previous is not None:
            if fold["decision_at"] <= previous["decision_at"]:
                raise ValueError("baseline series steps must expand in time")
            wait = embargo_hours(
                {
                    "decision_at": previous["decision_at"],
                    "exit_at": previous["exit_at"],
                    "decision_delay_hours": previous["decision_delay_hours"],
                }
            )
            earliest = previous["exit_at"] + timedelta(hours=wait)
            if fold["decision_at"] < earliest:
                raise ValueError(
                    f"step {fold['date']} decision_at violates embargo "
                    f"of {wait} hours after the previous exit_at"
                )
        previous = fold
        books.append(fold)
    return books


def compare_walkforward(
    steps: list[dict[str, Any]],
    *,
    starting_cash: float = STARTING_CASH,
) -> dict[str, Any]:
    """Sequential forward-only MTM. Each step uses only that step's marks."""
    conviction_equity = float(starting_cash)
    equal_equity = float(starting_cash)
    conviction_curve: list[dict[str, Any]] = []
    equal_curve: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        horizon = float(step["horizon_hours"])
        conviction_book = _conviction_book(step, horizon_hours=horizon)
        equal_book = _equal_book(step, horizon_hours=horizon)
        conv = mark_book_equity(conviction_book, step["marks"], starting_equity=conviction_equity)
        eqw = mark_book_equity(equal_book, step["marks"], starting_equity=equal_equity)
        conviction_equity = conv["ending_equity"]
        equal_equity = eqw["ending_equity"]
        decision = step["decision_at"]
        exit_at = step["exit_at"]
        stamp = {
            "step": index + 1,
            "date": step["date"],
            "decision_at": decision.isoformat().replace("+00:00", "Z")
            if isinstance(decision, datetime)
            else str(decision),
            "exit_at": exit_at.isoformat().replace("+00:00", "Z")
            if isinstance(exit_at, datetime)
            else str(exit_at),
        }
        conviction_curve.append({**stamp, **conv, "book": conviction_book})
        equal_curve.append({**stamp, **eqw, "book": equal_book})
    return {
        "starting_cash": float(starting_cash),
        "conviction": {
            "ending_equity": conviction_equity,
            "total_pnl": conviction_equity - float(starting_cash),
            "equity_curve": conviction_curve,
        },
        "equal_weight": {
            "ending_equity": equal_equity,
            "total_pnl": equal_equity - float(starting_cash),
            "equity_curve": equal_curve,
        },
        "equity_delta_conviction": conviction_equity - float(starting_cash),
        "equity_delta_equal": equal_equity - float(starting_cash),
        "delta_conviction_minus_equal": conviction_equity - equal_equity,
    }


def run_baseline_compare(
    *,
    fixtures: Path | None = None,
    series_path: Path | None = None,
    root: Path | None = None,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Compare conviction vs equal-weight on the frozen fixture series."""
    repo = root if root is not None else Path(__file__).resolve().parent.parent
    path = series_path
    if path is None:
        if fixtures is not None:
            candidate = fixtures / "baseline" / "series.json"
            path = candidate if candidate.is_file() else DEFAULT_SERIES
        else:
            path = DEFAULT_SERIES
    steps = load_baseline_series(path)
    compared = compare_walkforward(steps, starting_cash=STARTING_CASH)
    stamp = operate_stamp()
    live_days = live_research_day_count(repo)
    thin = live_days < 2
    report = {
        "mode": "baseline-compare",
        "note": NOTE,
        "not_alpha": True,
        "not_fitted": True,
        "paper_only": True,
        "live_money": False,
        "alpha": False,
        "source": "fixtures",
        "series_path": str(path),
        "live_history_days": live_days,
        "live_history_thin": thin,
        "live_history_note": (
            f"Live research series has {live_days} dated day(s). "
            "Comparator uses the frozen fixture series until daily research "
            "accumulates. Not alpha. Not fitted."
        ),
        "date": _aware(when if when is not None else _utc_now()).date().isoformat(),
        "n_steps": len(steps),
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "conviction_params": conviction_params(),
        **compared,
        "ok": True,
    }
    return report


def write_baseline_compare(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(report)
    payload["baseline_path"] = str(target)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
