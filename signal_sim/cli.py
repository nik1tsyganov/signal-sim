"""Command-line interface for fixture candidate ranking."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .diagnose import fixture_diagnostics
from .fixture_load import load_fixture_events
from .hawkes import fixture_intensity
from .indicators import rank_candidates
from .store import EventStore


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
    drift = commands.add_parser(
        "drift",
        help="emit a fixture-only cluster-drift target book (stub, not alpha)",
    )
    drift.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture events (required; the only supported input)",
    )
    drift.add_argument(
        "--marks",
        help="fixture mark book JSON or alias: liquid (default), two-name",
    )
    drift.add_argument(
        "--intensity",
        action="store_true",
        help="attach declared Hawkes intensity (diagnose params; not a fit)",
    )
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
        help="fixture mark book JSON or alias: liquid (default), two-name",
    )
    replay.add_argument(
        "--drift",
        action="store_true",
        help="size from the cluster-drift stub instead of rank (rank stays unchanged)",
    )
    replay.add_argument(
        "--intensity",
        action="store_true",
        help="apply declared Hawkes intensity overlay when --drift is set",
    )
    walkforward = commands.add_parser(
        "walkforward",
        help="expanding fixture-mark folds with purge/embargo (not a param search)",
    )
    walkforward.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture events and marks (required; the only supported input)",
    )
    shadow = commands.add_parser(
        "shadow",
        help="frozen shadow-paper walk-forward report (not a param search)",
    )
    shadow.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture events and marks (required; the only supported input)",
    )
    shadow.add_argument(
        "--out",
        help="write the JSON report here (default: artifacts dir or stdout only)",
    )
    rails = commands.add_parser(
        "rails",
        help="local rails only: live host, temp KILL, research/vendor mark",
    )
    rails.add_argument(
        "--fixtures",
        action="store_true",
        help="required; rails stay on local fixtures and do not place live calls",
    )
    smoke = commands.add_parser(
        "smoke",
        help="one frozen-params pass of rails/rank/diagnose/intensity/drift/replay/walkforward/shadow",
    )
    smoke.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture events and marks (required; the only supported input)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {
        "rank",
        "intensity",
        "diagnose",
        "marks",
        "drift",
        "replay",
        "walkforward",
        "shadow",
        "rails",
        "smoke",
    } and not args.fixtures:
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
        print(json.dumps(fixture_intensity(fixtures), separators=(",", ":")))
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
    if args.command == "drift":
        from .drift import fixture_drift_book
        from .sim import resolve_mark_book_path

        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        mark_book_path = resolve_mark_book_path(args.marks) if args.marks else None
        print(
            json.dumps(
                fixture_drift_book(
                    fixtures,
                    mark_book_path,
                    intensity=getattr(args, "intensity", False),
                ),
                separators=(",", ":"),
            )
        )
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
        if getattr(args, "intensity", False) and not getattr(args, "drift", False):
            print("replay --intensity requires --drift", file=sys.stderr)
            return 2
        if args.path:
            from .sim import run_fixture_path

            summary = run_fixture_path(
                fixtures=fixtures,
                ledger_path=ledger,
                drift=getattr(args, "drift", False),
                intensity=getattr(args, "intensity", False),
            )
        else:
            from .sim import resolve_mark_book_path

            mark_book_path = resolve_mark_book_path(args.marks) if args.marks else None
            candidates = None
            if getattr(args, "drift", False):
                from .drift import fixture_drift_book

                candidates = fixture_drift_book(
                    fixtures,
                    mark_book_path,
                    intensity=getattr(args, "intensity", False),
                )["targets"]
            summary = run_fixture_replay(
                fixtures=fixtures,
                ledger_path=ledger,
                mark_book_path=mark_book_path,
                candidates=candidates,
            )
            if getattr(args, "drift", False):
                summary = dict(summary)
                summary["signal"] = "cluster-drift-stub"
        stats = summary.get("stats", {})
        print(
            f"{summary.get('mode', 'replay')}: total_pnl={summary.get('total_pnl')} "
            f"ending_equity={summary.get('ending_equity')} "
            f"n_orders={stats.get('n_orders')} hit_rate={stats.get('hit_rate')}",
            file=sys.stderr,
        )
        print(json.dumps(summary, separators=(",", ":")))
        return 0
    if args.command == "walkforward":
        import tempfile
        from .walkforward import run_fixture_walkforward

        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        ledger_dir = tempfile.mkdtemp(prefix="paper-walkforward-")
        summary = run_fixture_walkforward(fixtures=fixtures, ledger_dir=ledger_dir)
        for row in summary["folds"]:
            print(
                f"fold {row['fold']} {row['name']} declared: fixture-mark total_pnl={row['total_pnl']} "
                f"n_events={row['n_events']} n_orders={row['n_orders']}",
                file=sys.stderr,
            )
            for name, comparison in row.get("comparisons", {}).items():
                print(
                    f"fold {row['fold']} {row['name']} {name}: fixture-mark "
                    f"total_pnl={comparison['total_pnl']} "
                    f"n_events={comparison['n_events']} n_orders={comparison['n_orders']}",
                    file=sys.stderr,
                )
        print(json.dumps(summary, separators=(",", ":")))
        return 0
    if args.command == "shadow":
        import tempfile
        from .shadow import run_shadow_report

        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        ledger_dir = tempfile.mkdtemp(prefix="paper-shadow-")
        out_path = Path(args.out) if args.out else None
        report = run_shadow_report(
            fixtures=fixtures,
            ledger_dir=ledger_dir,
            out_path=out_path,
        )
        print(json.dumps(report, separators=(",", ":")))
        return 0
    if args.command == "rails":
        import tempfile

        from .smoke import run_rails

        ledger_dir = tempfile.mkdtemp(prefix="paper-rails-")
        report = run_rails(ledger_dir=ledger_dir)
        print(f"rails params_sha256={report.get('params_sha256')} ok={report.get('ok')}", file=sys.stderr)
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command == "smoke":
        import tempfile

        from .smoke import run_smoke

        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        ledger_dir = tempfile.mkdtemp(prefix="paper-smoke-")
        report = run_smoke(fixtures=fixtures, ledger_dir=ledger_dir, write_artifact=False)
        print(f"smoke params_sha256={report.get('params_sha256')} ok={report.get('ok')}", file=sys.stderr)
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
