"""Daily paper telemetry pack. Relates book decisions to paper account data.

Same-day artifacts only. No future-day feed join. Not alpha. Paper only.
Does not POST.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .baseline import default_baseline_path
from .decision import default_decision_path
from .params import conviction_params, operate_stamp
from .performance import default_snapshot_path, paper_performance_snapshot
from .research import load_research_artifact, research_artifact_path
from .runtime_env import paper_submit_flag

NOTE = (
    "Daily paper telemetry. Same-day research + paper snapshot. "
    "Not alpha. Paper only. Not a broker fill-quality score. Does not POST."
)
TELEMETRY_DIR = Path("docs/telemetry")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


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


def default_telemetry_path(root: Path | None = None, when: datetime | None = None) -> Path:
    base = root if root is not None else Path(__file__).resolve().parent.parent
    stamp = when if when is not None else _utc_now()
    return base / TELEMETRY_DIR / f"{stamp.date().isoformat()}.json"


def default_telemetry_md_path(root: Path | None = None, when: datetime | None = None) -> Path:
    base = root if root is not None else Path(__file__).resolve().parent.parent
    stamp = when if when is not None else _utc_now()
    return base / TELEMETRY_DIR / f"{stamp.date().isoformat()}.md"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else None


def _prior_snapshot(root: Path, when: datetime) -> dict[str, Any] | None:
    prior = when.date() - timedelta(days=1)
    path = root / "docs" / "performance" / f"{prior.isoformat()}.json"
    loaded = _load_json(path)
    if loaded is not None:
        return loaded
    telemetry = root / TELEMETRY_DIR / f"{prior.isoformat()}.json"
    pack = _load_json(telemetry)
    if pack is None:
        return None
    account = pack.get("account") if isinstance(pack.get("account"), dict) else {}
    if not account:
        return None
    return {"account": account, "date": prior.isoformat()}


def _feed_n(feeds: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if not isinstance(feeds, dict):
        return out
    for name in ("quiver", "worldmonitor"):
        row = feeds.get(name)
        if isinstance(row, dict) and _finite(row.get("n")) is not None:
            out[name] = int(row["n"])
    return out


def _term_drivers(rows: list[dict[str, Any]]) -> dict[str, float]:
    keys = (
        "news_term",
        "q_term",
        "wm_term",
        "rec_term",
        "sent_term",
        "congress_confirm",
        "insider_confirm",
        "gov_confirm",
    )
    totals = {key: 0.0 for key in keys}
    for row in rows:
        for key in keys:
            value = _finite(row.get(key))
            if value is not None:
                totals[key] += value
    return totals


def _book_rows(
    research: dict[str, Any] | None,
    sell_reasons: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    targets = []
    if isinstance(research, dict):
        targets = list((research.get("proposed_book") or {}).get("targets") or [])
    reasons = sell_reasons or {}
    book: list[dict[str, Any]] = []
    for row in targets:
        if not isinstance(row, dict) or not row.get("ticker"):
            continue
        ticker = str(row["ticker"])
        item: dict[str, Any] = {
            "ticker": ticker,
            "score": row.get("score"),
            "target_frac": row.get("target_frac"),
            "sell_reasons": list(reasons.get(ticker, [])),
        }
        for key in ("news_term", "q_term", "wm_term", "rec_term", "sent_term", "sentiment"):
            if key in row:
                item[key] = row[key]
        book.append(item)
    for ticker, fired in reasons.items():
        if any(row["ticker"] == ticker for row in book):
            continue
        book.append({"ticker": ticker, "score": None, "target_frac": 0.0, "sell_reasons": list(fired)})
    return book


def _sell_reasons_from_tickets(tickets: list[Any] | None) -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    for row in tickets or []:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("symbol") or row.get("ticker") or "")
        reason = row.get("sell_reason")
        if not ticker or not isinstance(reason, str) or not reason:
            continue
        reasons.setdefault(ticker, [])
        if reason not in reasons[ticker]:
            reasons[ticker].append(reason)
    return reasons


def build_telemetry_pack(
    *,
    root: Path | None = None,
    when: datetime | None = None,
    research: dict[str, Any] | None = None,
    performance: dict[str, Any] | None = None,
    prior_performance: dict[str, Any] | None = None,
    rebalance: dict[str, Any] | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Assemble the daily pack from same-day artifacts. No future feeds."""
    base = root if root is not None else Path(__file__).resolve().parent.parent
    cut = when if when is not None else _utc_now()
    if cut.tzinfo is None or cut.utcoffset() is None:
        cut = cut.replace(tzinfo=timezone.utc)
    stamp = operate_stamp()
    conviction = conviction_params()
    loaded_research = research
    if loaded_research is None:
        path = research_artifact_path(cut, root=base)
        if path.is_file():
            loaded_research = load_research_artifact(path)
    if performance is None and client is not None:
        performance = paper_performance_snapshot(client, captured_at=cut)
    if performance is None:
        performance = _load_json(default_snapshot_path(base, cut))
    if prior_performance is None:
        prior_performance = _prior_snapshot(base, cut)

    account = {}
    if isinstance(performance, dict) and isinstance(performance.get("account"), dict):
        account = {
            field: performance["account"].get(field)
            for field in ("cash", "equity", "currency", "status")
        }
    equity = _finite(account.get("equity"))
    cash = _finite(account.get("cash"))
    proposed = (loaded_research or {}).get("proposed_book") or {}
    book_gross = _finite(proposed.get("book_gross"))
    if book_gross is None:
        book_gross = 0.0
        for row in proposed.get("targets") or []:
            if isinstance(row, dict) and _finite(row.get("target_frac")) is not None:
                book_gross += float(row["target_frac"])
    invest = _finite(proposed.get("max_gross_invest"))
    if invest is None:
        invest = float(conviction["max_gross_invest"])
    reserve = _finite(proposed.get("cash_reserve_frac"))
    if reserve is None:
        reserve = round(max(0.0, 1.0 - invest), 4)
    paper_cash_frac = None
    if equity is not None and equity > 0 and cash is not None:
        paper_cash_frac = cash / equity

    prior_account = {}
    if isinstance(prior_performance, dict) and isinstance(prior_performance.get("account"), dict):
        prior_account = prior_performance["account"]
    prior_equity = _finite(prior_account.get("equity"))
    prior_cash = _finite(prior_account.get("cash"))
    equity_delta = None if equity is None or prior_equity is None else equity - prior_equity
    cash_delta = None if cash is None or prior_cash is None else cash - prior_cash

    tickets = list((rebalance or {}).get("tickets") or []) if isinstance(rebalance, dict) else []
    reasons = _sell_reasons_from_tickets(tickets)
    rank_rows = list((loaded_research or {}).get("rank") or [])
    research_at = None
    if isinstance(loaded_research, dict):
        research_at = loaded_research.get("research_at")
    decision_at = research_at
    if isinstance(rebalance, dict) and rebalance.get("decision_at"):
        decision_at = rebalance.get("research_date") or research_at or rebalance.get("decision_at")

    marks = {}
    if isinstance(rebalance, dict) and isinstance(rebalance.get("marks"), dict):
        marks = rebalance["marks"]
    elif isinstance(performance, dict) and performance.get("label") == "paper":
        marks = {"paper_data": [], "fixture": [], "unmarked": []}

    feeds = _feed_n((loaded_research or {}).get("feeds"))
    positions = {}
    n_positions = None
    n_fills = None
    if isinstance(performance, dict):
        pos = performance.get("positions") or {}
        if isinstance(pos, dict):
            n_positions = pos.get("n")
            positions = pos.get("symbols") or {}
        n_fills = performance.get("n_fills")

    pack = {
        "mode": "paper-telemetry",
        "note": NOTE,
        "not_alpha": True,
        "paper_only": True,
        "live_money": False,
        "alpha": False,
        "date": cut.date().isoformat(),
        "research_at": research_at,
        "decision_at": decision_at,
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "conviction": conviction,
        "equity": account.get("equity"),
        "cash": account.get("cash"),
        "gross": book_gross,
        "cash_reserve_frac": reserve,
        "max_gross_invest": invest,
        "paper_cash_frac": paper_cash_frac,
        "account": account,
        "positions": positions,
        "n_positions": n_positions,
        "n_fills": n_fills,
        "book": _book_rows(loaded_research, reasons),
        "feeds_n": feeds,
        "score_prime_drivers": _term_drivers(rank_rows),
        "sell_reasons": reasons,
        "paper_delta": {
            "prior_date": (
                prior_performance.get("date")
                if isinstance(prior_performance, dict)
                else None
            ),
            "equity": equity_delta,
            "cash": cash_delta,
        },
        "equity_delta": equity_delta,
        "mark_kinds": marks,
        "sentiment": (loaded_research or {}).get("sentiment"),
        "submit_flag": paper_submit_flag(),
        "read_only": True,
        "submitted": False,
        "order_post": "disabled",
        "ok": True,
    }
    decision = _load_json(default_decision_path(base, cut))
    if isinstance(decision, dict):
        pack["decision_verdict"] = decision.get("verdict")
        pack["recommend_submit"] = decision.get("recommend_submit")
        pack["decision_reasons"] = list(decision.get("reasons") or [])
    baseline = _load_json(default_baseline_path(base, cut))
    if isinstance(baseline, dict):
        pack["equity_delta_conviction"] = baseline.get("equity_delta_conviction")
        pack["equity_delta_equal"] = baseline.get("equity_delta_equal")
        pack["delta_conviction_minus_equal"] = baseline.get("delta_conviction_minus_equal")
    return pack


def write_telemetry_pack(
    pack: dict[str, Any],
    path: str | Path,
    *,
    markdown: bool = False,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(pack)
    payload["telemetry_path"] = str(target)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if markdown:
        md_path = target.with_suffix(".md")
        md_path.write_text(telemetry_markdown(payload), encoding="utf-8")
    return target


def telemetry_markdown(pack: dict[str, Any]) -> str:
    book = pack.get("book") or []
    names = ", ".join(str(row.get("ticker")) for row in book if isinstance(row, dict))
    reasons = pack.get("sell_reasons") or {}
    fired = ", ".join(f"{ticker}={'/'.join(items)}" for ticker, items in reasons.items()) or "none"
    feeds = pack.get("feeds_n") or {}
    return (
        f"# Paper telemetry {pack.get('date')}\n\n"
        f"Not alpha. Paper only.\n\n"
        f"- equity={pack.get('equity')} cash={pack.get('cash')} "
        f"gross={pack.get('gross')} cash_reserve_frac={pack.get('cash_reserve_frac')}\n"
        f"- equity_delta={pack.get('equity_delta')}\n"
        f"- decision_verdict={pack.get('decision_verdict')} "
        f"recommend_submit={pack.get('recommend_submit')}\n"
        f"- equity_delta_conviction={pack.get('equity_delta_conviction')} "
        f"equity_delta_equal={pack.get('equity_delta_equal')}\n"
        f"- feeds_n={feeds}\n"
        f"- book: {names or '(empty)'}\n"
        f"- sell reasons: {fired}\n"
    )
