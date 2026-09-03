"""Command-line interface for fixture candidate ranking."""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .diagnose import fixture_diagnostics
from .events import Event
from .hawkes import intensity_at
from .indicators import UNIVERSE, rank_candidates
from .store import EventStore


def _direct_events(directory: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(directory.glob("*.json")):
        if "poison" in path.name.lower():
            continue
        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        if isinstance(payload, list):
            records.extend(payload)
        elif isinstance(payload, dict) and isinstance(payload.get("events"), list):
            records.extend(payload["events"])
        elif isinstance(payload, dict):
            records.append(payload)
        else:
            raise ValueError(f"fixture must contain an event or event list: {path}")
    return records


def _call_loader(loader: Callable[..., list[Any]], directory: Path) -> list[Any]:
    required = [
        parameter
        for parameter in inspect.signature(loader).parameters.values()
        if parameter.default is inspect.Parameter.empty
        and parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    return loader(str(directory)) if required else loader()


def _load_group(name: str, directory: Path) -> list[Event]:
    if not directory.is_dir():
        return []
    fixture_paths = list(directory.glob("*.json"))
    module_name = f"signal_sim.sources.{name}"
    spec = importlib.util.find_spec(module_name)
    if spec is not None:
        module = importlib.import_module(module_name)
        loader = getattr(module, "load_events", None)
    else:
        loader = None
    if any("poison" in path.name.lower() for path in fixture_paths):
        records = _direct_events(directory)
    else:
        records = _call_loader(loader, directory) if callable(loader) else _direct_events(directory)
    return [record if isinstance(record, Event) else Event.from_dict(record) for record in records]


def load_fixture_events(fixtures: Path) -> list[Event]:
    return _load_group("news", fixtures / "news") + _load_group("altdata", fixtures / "altdata")


def rank_fixture_events(fixtures: Path | None = None) -> list[dict[str, int | str]]:
    """Rank fixture events at the default mark-book decision. Not a live feed."""
    from .sim import load_mark_book

    root = fixtures if fixtures is not None else Path(__file__).resolve().parent.parent / "fixtures"
    events = load_fixture_events(root)
    decision_at = load_mark_book()["decision_at"]
    with EventStore() as store:
        store.add_many(events)
        return rank_candidates(store.all(), window_end=decision_at)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="signal_sim")
    commands = parser.add_subparsers(dest="command", required=True)
    rank = commands.add_parser("rank", help="rank paper-trade candidates")
    rank.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture events (required; the only supported input)",
    )
    intensity = commands.add_parser("intensity", help="calculate fixture event intensity")
    intensity.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture events (required; the only supported input)",
    )
    diagnose = commands.add_parser(
        "diagnose",
        help="Hawkes intensity and online cluster diagnostics (not a rank input)",
    )
    diagnose.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture events (required; the only supported input)",
    )
    marks = commands.add_parser(
        "marks",
        help="list fixture-mark fillable names and no_mark skips",
    )
    marks.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture mark books (required; the only supported input)",
    )
    serve = commands.add_parser("serve", help="serve the local paper-only desk")
    serve.add_argument("--port", type=int, default=8765, help="local desk port")
    replay = commands.add_parser(
        "replay",
        help="replay fixture ranks through the local paper ledger",
    )
    replay.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture events and marks (required; the only supported input)",
    )
    replay.add_argument(
        "--ledger",
        help="sqlite ledger path (default: a temporary file)",
    )
    replay.add_argument(
        "--path",
        action="store_true",
        help="replay the checked-in three-step fixture mark path (not vendor bars)",
    )
    replay.add_argument(
        "--marks",
        help="fixture mark book JSON (default: fixtures/marks/universe.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"rank", "intensity", "diagnose", "marks", "replay"} and not args.fixtures:
        print(
            f"{args.command} requires --fixtures; only local fixture events are supported",
            file=sys.stderr,
        )
        return 2
    if args.command == "rank":
        print(json.dumps(rank_fixture_events(), separators=(",", ":")))
        return 0
    if args.command == "intensity":
        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        events = load_fixture_events(fixtures)
        when = max(event.observed_at for event in events)
        intensities = {
            ticker: intensity_at(
                (event for event in events if event.ticker == ticker),
                when,
            )
            for ticker in UNIVERSE
        }
        print(json.dumps(intensities, separators=(",", ":")))
        return 0
    if args.command == "diagnose":
        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        events = load_fixture_events(fixtures)
        print(json.dumps(fixture_diagnostics(events), separators=(",", ":")))
        return 0
    if args.command == "marks":
        from .sim import fixture_mark_map

        print(json.dumps(fixture_mark_map(), separators=(",", ":")))
        return 0
    if args.command == "serve":
        from .serve import serve

        serve(args.port)
        return 0
    if args.command == "replay":
        import tempfile
        from .sim import run_fixture_replay

        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        ledger = args.ledger
        if not ledger:
            ledger = tempfile.NamedTemporaryFile(prefix="paper-replay-", suffix=".sqlite", delete=False).name
        if args.path:
            from .sim import run_fixture_path

            summary = run_fixture_path(fixtures=fixtures, ledger_path=ledger)
        else:
            mark_book_path = Path(args.marks) if args.marks else None
            summary = run_fixture_replay(
                fixtures=fixtures,
                ledger_path=ledger,
                mark_book_path=mark_book_path,
            )
        stats = summary.get("stats", {})
        print(
            f"{summary.get('mode', 'replay')}: total_pnl={summary.get('total_pnl')} "
            f"ending_equity={summary.get('ending_equity')} "
            f"n_orders={stats.get('n_orders')} hit_rate={stats.get('hit_rate')}",
            file=sys.stderr,
        )
        print(json.dumps(summary, separators=(",", ":")))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
