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
    serve = commands.add_parser("serve", help="serve the local paper-only desk")
    serve.add_argument("--port", type=int, default=8765, help="local desk port")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"rank", "intensity"} and not args.fixtures:
        print(
            f"{args.command} requires --fixtures; only local fixture events are supported",
            file=sys.stderr,
        )
        return 2
    if args.command == "rank":
        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        events = load_fixture_events(fixtures)
        with EventStore() as store:
            store.add_many(events)
            candidates = rank_candidates(store.all())
        print(json.dumps(candidates, separators=(",", ":")))
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
    if args.command == "serve":
        from .serve import serve

        serve(args.port)
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
