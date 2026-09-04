"""Load checked-in fixture events. No ranking and no orders."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
from pathlib import Path
from typing import Any, Callable

from .events import Event


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
    """Read news and altdata fixtures. Prints are admitted on observed_at later."""
    return _load_group("news", fixtures / "news") + _load_group("altdata", fixtures / "altdata")
