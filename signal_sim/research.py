"""Daily live research: expand the book from intel, then write a dated artifact.

The JSON is the next rebalance target book. It is not decorative. It carries
counts, ranked tickers, and sized targets only — no person names, headlines,
URLs, or raw payload fields. Paper only. Not alpha. Not live money.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .conviction import conviction_targets, paper_name_cap, research_rank_rows
from .diagnose import fixture_diagnostics
from .drift import fixture_drift_book
from .events import Event
from .fixture_load import load_fixture_events
from .hawkes import intensity_map
from .indicators import UNIVERSE, filed_confirm_features, intel_features
from .live_feeds import (
    LiveFeedConfigError,
    fetch_live_feed_payloads,
    strategy_events,
    summarize_feed,
)
from .params import (
    CONVICTION_MAX_GROSS_INVEST,
    CONVICTION_SENTIMENT_CAP_N,
    conviction_params,
    operate_stamp,
)
from .sentiment import batch_new_print_tones, mean_signed_news, sentiment_summary
from .sim import load_mark_book, resolve_mark_book_path
from .universe import expand_operating_universe, load_liquid_allowlist

NOTE = (
    "Daily live research book. Fixture universe union top-N allowlisted "
    "Quiver/World Monitor tickers. Declared score' plus conviction weights. "
    "Not fitted. Not alpha. Not a broker fill. No PII. Paper only."
)
SIGNAL = "research-live"
_PII_KEYS = (
    "person",
    "headline",
    "url",
    "raw_ref",
    "entities",
    "representative",
    "name",
)
TARGET_KEYS = (
    "ticker",
    "target_frac",
    "side",
    "horizon_hours",
    "score",
    "cluster_size",
    "n_clusters",
    "state",
    "intensity",
    "insider_confirm",
    "congress_confirm",
    "gov_confirm",
    "intel_brief",
    "wm_intel",
    "chokepoint",
    "news_breakout",
    "quiver_count",
    "news_term",
    "q_term",
    "wm_term",
    "rec_term",
    "sent_term",
    "sentiment",
    "lag_h",
)


def default_research_dir(root: Path | None = None) -> Path:
    base = root if root is not None else Path(__file__).resolve().parent.parent
    return base / "docs" / "research"


def _research_date_paths(folder: Path) -> list[tuple[str, Path]]:
    rows: list[tuple[str, Path]] = []
    if not folder.is_dir():
        return rows
    for path in sorted(folder.glob("*.json")):
        name = path.name
        if name.endswith("-paper.json") or name.endswith("-equal-weight.json"):
            continue
        stamp = path.stem
        if len(stamp) == 10 and stamp[4] == "-" and stamp[7] == "-":
            rows.append((stamp, path))
    return rows


def _prior_research_cut(folder: Path, *, before: str | None) -> datetime | None:
    latest: datetime | None = None
    for stamp, path in _research_date_paths(folder):
        if before is not None and stamp >= before:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        text = raw.get("research_at") or raw.get("date")
        if not isinstance(text, str) or not text:
            continue
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if latest is None or parsed > latest:
            latest = parsed
    return latest


def load_entry_state(
    folder: Path | None,
    *,
    before: str | None,
    symbols: list[str] | tuple[str, ...] | set[str],
) -> dict[str, dict[str, Any]]:
    """First research-live target appearance before ``before`` (entry clock)."""
    wanted = {str(symbol) for symbol in symbols}
    found: dict[str, dict[str, Any]] = {}
    if folder is None or not wanted:
        return found
    for stamp, path in _research_date_paths(folder):
        if before is not None and stamp >= before:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        targets = (raw.get("proposed_book") or {}).get("targets") or []
        research_at = raw.get("research_at") or raw.get("date") or stamp
        for row in targets:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "")
            if ticker not in wanted or ticker in found:
                continue
            score = row.get("score")
            found[ticker] = {
                "entry_decision_at": research_at,
                "entry_score": score,
                "research_date": stamp,
            }
        if len(found) == wanted:
            break
    return found


def research_artifact_path(when: datetime | None = None, *, root: Path | None = None) -> Path:
    stamp = (when or datetime.now(timezone.utc)).date().isoformat()
    return default_research_dir(root) / f"{stamp}.json"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _rank_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ticker": str(row["ticker"]),
        "score": float(row["score"]),
        "news_breakout": int(row.get("news_breakout") or 0),
        "congress_confirm": int(row.get("congress_confirm") or 0),
        "insider_confirm": int(row.get("insider_confirm") or 0),
    }
    if row.get("gov_confirm"):
        out["gov_confirm"] = int(row["gov_confirm"])
    for key in (
        "quiver_count",
        "news_term",
        "q_term",
        "wm_term",
        "rec_term",
        "sent_term",
        "sentiment",
        "lag_h",
    ):
        if key in row:
            out[key] = row[key]
    return out


def _target_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in TARGET_KEYS if key in row}


def assert_no_pii(payload: Any) -> None:
    dumped = json.dumps(payload)
    lowered = dumped.lower()
    for key in _PII_KEYS:
        if f'"{key}"' in lowered:
            raise ValueError(f"research artifact must not include {key}")


def _merge_candidates(
    ranked: list[dict[str, Any]],
    drift_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in ranked:
        ticker = str(row["ticker"])
        if ticker in seen:
            continue
        seen.add(ticker)
        merged.append(dict(row))
    for row in drift_rows:
        ticker = str(row["ticker"])
        if ticker in seen:
            continue
        seen.add(ticker)
        merged.append(dict(row))
    return merged


def _attach_live_features(
    rows: list[dict[str, Any]],
    events: list[Event],
    when: datetime,
    universe: tuple[str, ...],
) -> list[dict[str, Any]]:
    confirms = filed_confirm_features(events, when, universe=universe)
    intel = intel_features(events, when, universe=universe)
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        ticker = str(item["ticker"])
        feat = confirms.get(ticker) or {}
        item.setdefault("insider_confirm", int(feat.get("insider_confirm") or 0))
        item.setdefault("congress_confirm", int(feat.get("congress_confirm") or 0))
        item.setdefault("gov_confirm", int(feat.get("gov_confirm") or 0))
        brief = intel.get(ticker) or {}
        item.setdefault("intel_brief", int(brief.get("intel_brief") or 0))
        item.setdefault("wm_intel", int(brief.get("wm_intel") or 0))
        item.setdefault("chokepoint", int(brief.get("chokepoint") or 0))
        out.append(item)
    return out


def load_research_artifact(path: Path | str) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("research artifact must be an object")
    book = raw.get("proposed_book")
    universe = raw.get("universe")
    if not isinstance(book, dict) or not isinstance(book.get("targets"), list):
        raise ValueError("research artifact proposed_book.targets is required")
    if not isinstance(universe, dict) or not isinstance(universe.get("operating"), list):
        raise ValueError("research artifact universe.operating is required")
    assert_no_pii(raw)
    return raw


def resolve_research_book(
    *,
    fixtures: Path | None = None,
    mark_book_path: Path | str | None = None,
    research: dict[str, Any] | None = None,
    research_path: Path | str | None = None,
    live_events: list[Event] | None = None,
    live_quiver: list[Any] | None = None,
    live_world: list[Any] | None = None,
    when: datetime | None = None,
    write: bool = False,
) -> dict[str, Any]:
    """Load today's artifact when present; otherwise compute the live book."""
    if research is not None:
        return research
    injected = live_events is not None or live_quiver is not None or live_world is not None
    path = Path(research_path) if research_path is not None else research_artifact_path(when)
    if not injected and path.is_file():
        return load_research_artifact(path)
    return run_research(
        fixtures=fixtures,
        mark_book_path=mark_book_path,
        live_events=live_events,
        live_quiver=live_quiver,
        live_world=live_world,
        when=when,
        out_path=path if write else None,
    )


def run_research(
    *,
    fixtures: Path | None = None,
    mark_book_path: Path | str | None = None,
    live_events: list[Event] | None = None,
    live_quiver: list[Any] | None = None,
    live_world: list[Any] | None = None,
    when: datetime | None = None,
    out_path: Path | str | None = None,
    allowlist: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Pull intel, expand the universe, rank/diagnose/intensity, size a book."""
    root = fixtures if fixtures is not None else Path(__file__).resolve().parent.parent / "fixtures"
    repo = root.parent if root.name == "fixtures" else root
    resolved = resolve_mark_book_path(mark_book_path) if mark_book_path else None
    book = load_mark_book(resolved)
    requested = when
    now = _utc_now()
    allowed = allowlist if allowlist is not None else load_liquid_allowlist()
    quiver: list[Any] = []
    world: list[Any] = []
    extra: list[Event] = []
    if live_events is not None:
        extra = [event for event in live_events if event.ticker in allowed or event.ticker in UNIVERSE]
        world = list(extra)
    else:
        if live_quiver is None or live_world is None:
            quiver, world = fetch_live_feed_payloads(accept=allowed)
        else:
            quiver = list(live_quiver)
            world = list(live_world)
        extra = strategy_events(quiver, world, universe=allowed)
    cut = requested if requested is not None else now
    if cut.tzinfo is None or cut.utcoffset() is None:
        raise ValueError("research cut must be timezone-aware")
    if requested is None:
        latest = cut
        for event in extra:
            if event.observed_at > latest:
                latest = event.observed_at
        if latest > cut:
            cut = latest + timedelta(microseconds=1)

    fixture_events = [
        event for event in load_fixture_events(root) if event.observed_at <= cut
    ]

    universe_info = expand_operating_universe(extra, fixture=UNIVERSE, allowlist=allowed)
    operating = tuple(universe_info["operating"])
    material = list(fixture_events) + extra

    ranked = research_rank_rows(material, cut, universe=operating)
    drift_book = fixture_drift_book(
        root,
        resolved,
        intensity=True,
        extra_events=extra,
        intensity_when=cut,
        universe=operating,
        include_extra_in_clusters=True,
    )
    drift_by_ticker = {
        str(row["ticker"]): row for row in list(drift_book.get("targets") or [])
    }
    candidates = []
    for row in ranked:
        item = dict(row)
        extra_row = drift_by_ticker.get(str(item["ticker"])) or {}
        for key in ("cluster_size", "n_clusters", "state", "intensity"):
            if key in extra_row:
                item[key] = extra_row[key]
        candidates.append(item)
    horizon_hours = (book["exit_at"] - book["decision_at"]).total_seconds() / 3600.0
    targets, size_skips = conviction_targets(
        candidates,
        horizon_hours=horizon_hours,
        max_gross_invest=CONVICTION_MAX_GROSS_INVEST,
        max_name_frac=paper_name_cap(float(book["max_name_frac"])),
    )
    prior_cut = _prior_research_cut(default_research_dir(repo), before=cut.date().isoformat())
    tones = mean_signed_news(material, cut, universe=operating)
    new_prints = batch_new_print_tones(
        material,
        until=cut,
        since=prior_cut,
        cap_n=CONVICTION_SENTIMENT_CAP_N,
    )
    sentiment = sentiment_summary(tones, new_prints)
    book_gross = math.fsum(float(row["target_frac"]) for row in targets)
    intensities = intensity_map(material, cut, universe=operating)
    diagnose = fixture_diagnostics(
        fixture_events,
        decision_at=cut,
        extra_events=extra,
        universe=operating,
    )
    stamp = operate_stamp()
    feeds = {
        "quiver": summarize_feed(quiver if quiver else extra if live_events is None else []),
        "worldmonitor": summarize_feed(world if world else extra),
    }
    if live_events is not None and not quiver:
        feeds = {
            "quiver": summarize_feed([event for event in extra if event.source == "quiver"]),
            "worldmonitor": summarize_feed(
                [event for event in extra if event.source == "worldmonitor"]
            ),
            "injected": True,
        }
    report = {
        "mode": "daily-research",
        "note": NOTE,
        "signal": SIGNAL,
        "date": cut.date().isoformat(),
        "research_at": _iso(cut),
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "universe": universe_info,
        "feeds": feeds,
        "rank": [_rank_row(row) for row in ranked],
        "diagnose": {
            "stats": diagnose.get("stats"),
            "confirms": diagnose.get("confirms"),
            "n_clusters": (diagnose.get("stats") or {}).get("n_clusters"),
        },
        "intensity": intensities,
        "conviction": conviction_params(),
        "sentiment": sentiment,
        "proposed_book": {
            "signal": SIGNAL,
            "note": NOTE,
            "targets": [_target_row(row) for row in targets],
            "n_targets": len(targets),
            "skipped": size_skips,
            "max_name_frac": paper_name_cap(float(book["max_name_frac"])),
            "max_gross_invest": CONVICTION_MAX_GROSS_INVEST,
            "cash_reserve_frac": round(max(0.0, 1.0 - CONVICTION_MAX_GROSS_INVEST), 4),
            "book_gross": book_gross,
            "max_gross_frac": float(book["max_gross_frac"]),
        },
        "ok": True,
    }
    assert_no_pii(report)
    if out_path is not None:
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report = dict(report)
        report["written"] = str(path)
    return report


def paper_performance_path(when: datetime | None = None, *, root: Path | None = None) -> Path:
    stamp = (when or datetime.now(timezone.utc)).date().isoformat()
    return default_research_dir(root) / f"{stamp}-paper.json"


def write_paper_performance(
    client: Any,
    *,
    out_path: Path | str | None = None,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Sanitized paper account snapshot. No secrets. Not alpha."""
    account = client.account()
    positions = client.positions()
    clock = client.clock()
    orders = []
    if hasattr(client, "orders"):
        try:
            orders = client.orders(status="open", limit=50)
        except Exception:
            orders = []
    cut = when if when is not None else _utc_now()
    report = {
        "mode": "paper-performance",
        "note": (
            "Sanitized Alpaca paper snapshot. Not alpha. Not live money. "
            "Wait for open orders to fill before another full-book submit."
        ),
        "date": cut.date().isoformat(),
        "written_at": _iso(cut),
        "account": {
            field: account.get(field)
            for field in (
                "status",
                "currency",
                "cash",
                "equity",
                "buying_power",
                "trading_blocked",
                "account_blocked",
            )
        },
        "positions": {
            "n": len(positions),
            "symbols": {
                str(row.get("symbol")): str(row.get("qty") or "0")
                for row in positions
                if row.get("symbol")
            },
        },
        "clock": {
            field: clock.get(field)
            for field in ("timestamp", "is_open", "next_open", "next_close")
        },
        "open_orders": {
            "n": len(orders),
            "symbols": sorted(
                {
                    str(row.get("symbol"))
                    for row in orders
                    if isinstance(row, dict) and row.get("symbol")
                }
            ),
        },
        "ok": True,
    }
    path = Path(out_path) if out_path is not None else paper_performance_path(cut)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = dict(report)
    report["written"] = str(path)
    return report
