"""Frozen shadow-paper operate report.

Runs the checked-in walk-forward harness with declared params.
Writes JSON under an artifacts directory when one exists; otherwise stdout
is the operate path. Not a search. Not a live result.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .params import frozen_operate_params
from .walkforward import run_fixture_walkforward


NOTE = (
    "Frozen shadow-paper operate report. Fixture-mark PnL from walkforward "
    "--fixtures (declared plus comparisons). Params are declared constants. "
    "Not a search. Not a live result. Not a functional claim."
)
REPORT_NAME = "shadow-paper-walkforward.json"


def frozen_params() -> dict[str, Any]:
    return frozen_operate_params()


def artifacts_dir() -> Path | None:
    env = os.environ.get("SIGNAL_SIM_ARTIFACTS")
    if env:
        path = Path(env)
        return path if path.is_dir() else None
    for candidate in (
        Path("/opt/cursor/artifacts"),
        Path(__file__).resolve().parent.parent / "artifacts",
    ):
        if candidate.is_dir():
            return candidate
    return None


def run_shadow_report(
    *,
    fixtures: Path,
    ledger_dir: str,
    out_path: Path | None = None,
    write_artifact: bool = True,
) -> dict[str, Any]:
    summary = run_fixture_walkforward(fixtures=fixtures, ledger_dir=ledger_dir)
    report = {
        "mode": "local-paper-shadow",
        "note": NOTE,
        "params": frozen_params(),
        "walkforward": summary,
    }
    if write_artifact is not True:
        return report
    dest = out_path
    if dest is None:
        folder = artifacts_dir()
        if folder is not None:
            dest = folder / REPORT_NAME
    if dest is not None:
        dest = Path(dest)
        dest.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        report = dict(report)
        report["report_path"] = str(dest)
    return report
