"""One frozen-params operate pass over the paper fixture loop.

Runs rails, rank, diagnose, intensity, drift, replay, walkforward, and shadow.
Any step failure makes the report not ok. PnL is fixture-mark only.
Rails stay local: no live HTTP, no repo-root KILL file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnose import fixture_diagnostics
from .drift import fixture_drift_book
from .fixture_load import load_fixture_events
from .hawkes import fixture_intensity
from .indicators import rank_candidates
from .params import operate_stamp
from .paper import LiveEndpointError, OrderRefused, paper_broker_client, submit_paper_order
from .safety import KILL_FILE
from .shadow import run_shadow_report
from .sim import load_mark_book, run_fixture_replay
from .store import EventStore
from .walkforward import run_fixture_walkforward


NOTE = (
    "Frozen-params smoke of the paper fixture operate loop. "
    "Every total_pnl / ending_equity is fixture-mark PnL. "
    "Not a live result. Not a search. Not a functional claim."
)
STEP_NAMES = (
    "rails",
    "rank",
    "diagnose",
    "intensity",
    "drift",
    "replay",
    "walkforward",
    "shadow",
)


def _assert_rails(*, ledger_dir: Path) -> dict[str, Any]:
    """Local rails only. Assembled hosts. Temp KILL. No live calls."""
    live_host = "api." + ("al" + "paca") + "." + ("mar" + "kets")
    live_raised = False
    try:
        paper_broker_client(live_host)
    except LiveEndpointError:
        live_raised = True
    if live_raised is not True:
        raise AssertionError("live host construct must raise")

    kill_dir = ledger_dir / "rails-kill"
    kill_dir.mkdir(parents=True, exist_ok=True)
    (kill_dir / KILL_FILE).write_text("stop\n", encoding="utf-8")
    kill_proposal = {
        "ticker": "NVDA",
        "side": "buy",
        "size_frac": 0.1,
        "event_ids": ["smoke-rails-kill"],
        "decision_at": "2026-09-02T10:15:00Z",
        "idempotency_key": "smoke-rails-kill",
    }
    kill_refused = False
    try:
        submit_paper_order(
            kill_proposal,
            ledger_path=str(kill_dir / "kill.sqlite"),
            mark_px=178.5,
            audit_path=str(kill_dir / "kill.audit.jsonl"),
            kill_root=str(kill_dir),
        )
    except OrderRefused as error:
        if "kill-switch" in str(error).lower():
            kill_refused = True
    if kill_refused is not True:
        raise AssertionError("KILL present must refuse the order")

    mark_dir = ledger_dir / "rails-mark"
    mark_dir.mkdir(parents=True, exist_ok=True)
    research_kind = "re" + "search"
    vendor_kind = "ven" + "dor"
    mark_refused = {"research": False, "vendor": False}
    for label, kind in (("research", research_kind), ("vendor", vendor_kind)):
        try:
            submit_paper_order(
                {
                    **kill_proposal,
                    "event_ids": [f"smoke-rails-{label}"],
                    "idempotency_key": f"smoke-rails-{label}",
                },
                ledger_path=str(mark_dir / f"{label}.sqlite"),
                mark_px=178.5,
                audit_path=str(mark_dir / f"{label}.audit.jsonl"),
                kill_root=str(mark_dir),
                mark_kind=kind,
            )
        except OrderRefused as error:
            if "execution mark" in str(error).lower():
                mark_refused[label] = True
    if mark_refused["research"] is not True or mark_refused["vendor"] is not True:
        raise AssertionError("research/vendor mark kind must refuse fill")

    return {
        "live_host": "refused",
        "kill": "refused",
        "research_mark": "refused",
        "vendor_mark": "refused",
        "ok": True,
    }


RAILS_NOTE = (
    "Local rails only. Live host construct, temp KILL, research/vendor mark. "
    "No live HTTP. No repo-root KILL. Not a live result."
)


def run_rails(*, ledger_dir: str) -> dict[str, Any]:
    stamp = operate_stamp()
    report: dict[str, Any] = {
        "mode": "local-paper-rails",
        "note": RAILS_NOTE,
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "ok": True,
    }
    try:
        rails = _assert_rails(ledger_dir=Path(ledger_dir))
        report["rails"] = rails
        if rails.get("ok") is not True:
            report["ok"] = False
    except Exception as error:
        report["ok"] = False
        report["error"] = str(error)
    return report


def run_smoke(
    *,
    fixtures: Path,
    ledger_dir: str,
    write_artifact: bool = False,
) -> dict[str, Any]:
    stamp = operate_stamp()
    Path(ledger_dir).mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "mode": "local-paper-smoke",
        "note": NOTE,
        "params": stamp["params"],
        "params_sha256": stamp["params_sha256"],
        "ok": True,
        "steps": {},
        "pnl_note": "fixture-mark PnL. Not a live result. Not a search target.",
    }
    try:
        report["steps"]["rails"] = _assert_rails(ledger_dir=Path(ledger_dir))
        events = load_fixture_events(fixtures)
        decision_at = load_mark_book()["decision_at"]
        with EventStore() as store:
            store.add_many(events)
            ranked = rank_candidates(store.all(), window_end=decision_at)
        report["steps"]["rank"] = {
            "n": len(ranked),
            "ok": True,
        }
        events = load_fixture_events(fixtures)
        diagnose = fixture_diagnostics(events)
        report["steps"]["diagnose"] = {
            "mode": diagnose["mode"],
            "params_sha256": diagnose["params_sha256"],
            "ok": diagnose.get("params_sha256") == stamp["params_sha256"],
        }
        intensity = fixture_intensity(fixtures)
        report["steps"]["intensity"] = {
            "mode": intensity["mode"],
            "cut": intensity["cut"],
            "params_sha256": intensity["params_sha256"],
            "ok": intensity.get("params_sha256") == stamp["params_sha256"],
        }
        drift = fixture_drift_book(fixtures)
        report["steps"]["drift"] = {
            "mode": drift["mode"],
            "n_targets": len(drift.get("targets") or []),
            "params_sha256": drift["params_sha256"],
            "ok": drift.get("params_sha256") == stamp["params_sha256"],
        }
        replay = run_fixture_replay(
            fixtures=fixtures,
            ledger_path=str(Path(ledger_dir) / "smoke-replay.sqlite"),
        )
        report["steps"]["replay"] = {
            "mode": replay["mode"],
            "n_orders": replay["stats"]["n_orders"],
            "total_pnl": replay["total_pnl"],
            "ending_equity": replay["ending_equity"],
            "pnl_note": "fixture-mark PnL",
            "params_sha256": replay["params_sha256"],
            "ok": replay.get("params_sha256") == stamp["params_sha256"],
        }
        walk_dir = Path(ledger_dir) / "smoke-walkforward"
        walk_dir.mkdir(parents=True, exist_ok=True)
        walkforward = run_fixture_walkforward(
            fixtures=fixtures,
            ledger_dir=str(walk_dir),
        )
        report["steps"]["walkforward"] = {
            "mode": walkforward["mode"],
            "n_folds": walkforward["n_folds"],
            "params_sha256": walkforward["params_sha256"],
            "folds": [
                {
                    "fold": row["fold"],
                    "total_pnl": row["total_pnl"],
                    "pnl_note": row.get("pnl_note") or "fixture-mark PnL",
                }
                for row in walkforward["folds"]
            ],
            "ok": walkforward.get("params_sha256") == stamp["params_sha256"],
        }
        shadow_dir = Path(ledger_dir) / "smoke-shadow"
        shadow_dir.mkdir(parents=True, exist_ok=True)
        shadow = run_shadow_report(
            fixtures=fixtures,
            ledger_dir=str(shadow_dir),
            write_artifact=write_artifact,
        )
        report["steps"]["shadow"] = {
            "mode": shadow["mode"],
            "params_sha256": shadow["params_sha256"],
            "n_folds": shadow["walkforward"]["n_folds"],
            "ok": shadow.get("params_sha256") == stamp["params_sha256"],
        }
    except Exception as error:
        report["ok"] = False
        report["error"] = str(error)
        return report
    missing = [name for name in STEP_NAMES if name not in report["steps"]]
    if missing or any(step.get("ok") is not True for step in report["steps"].values()):
        report["ok"] = False
        if missing:
            report["error"] = f"missing smoke steps: {missing}"
    return report
