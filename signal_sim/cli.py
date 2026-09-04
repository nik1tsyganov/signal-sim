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
from .live_feeds import LiveFeedConfigError, missing_live_feed_keys, pull_live_feeds
from .ledger import WRITE_REFUSED, inspect_ledger
from .paper import (
    LiveEndpointError,
    PaperSubmitRefused,
    missing_paper_keys,
    paper_broker_client,
    paper_host,
    paper_submit_enabled,
    require_paper_submit,
)
from .performance import (
    default_snapshot_path,
    paper_performance_snapshot,
    write_paper_performance,
)
from .baseline import (
    default_baseline_path,
    run_baseline_compare,
    write_baseline_compare,
)
from .decision import (
    build_go_nogo,
    decision_submit_block,
    default_decision_path,
    write_go_nogo,
)
from .rebalance import apply_local_rebalance, proposed_rebalance, submit_paper_rebalance
from .research import research_artifact_path, run_research
from .runtime_env import paper_submit_flag, runtime_env_status
from .telemetry import (
    build_telemetry_pack,
    default_telemetry_path,
    write_telemetry_pack,
)
from .sim import resolve_mark_book_path
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
    feeds = commands.add_parser(
        "feeds",
        help="live intel counts and ticker histogram (no raw payload dump)",
    )
    feeds.add_argument(
        "--live",
        action="store_true",
        help="pull Quiver and World Monitor live (requires keys)",
    )
    paper_account = commands.add_parser(
        "paper-account",
        help="read-only Alpaca paper account/positions/clock smoke",
    )
    paper_account.add_argument(
        "--dry-run",
        action="store_true",
        help="also validate a sample paper-order payload (does not POST)",
    )
    rebalance = commands.add_parser(
        "rebalance",
        help="print proposed paper rebalance tickets; --apply-local is local-only; --submit-paper POSTs paper",
    )
    rebalance.add_argument(
        "--fixtures",
        action="store_true",
        help="load local fixture events and marks (required; the only supported target book)",
    )
    rebalance.add_argument(
        "--marks",
        help="fixture mark book JSON or alias: liquid (default), two-name",
    )
    rebalance.add_argument(
        "--rank",
        action="store_true",
        help="size from rank_candidates instead of the cluster-drift stub",
    )
    rebalance.add_argument(
        "--intensity",
        action="store_true",
        help="apply declared Hawkes intensity overlay on the drift book",
    )
    rebalance.add_argument(
        "--live",
        action="store_true",
        help="use today's research book (or compute it) and prefer paper IEX marks",
    )
    rebalance.add_argument(
        "--apply-local",
        action="store_true",
        help="record fixture-mark tickets on the local paper ledger (no broker POST)",
    )
    rebalance.add_argument(
        "--ledger",
        help="sqlite ledger path (required with --apply-local; unused for print-only)",
    )
    rebalance.add_argument(
        "--submit-paper",
        action="store_true",
        help="POST sized tickets to Alpaca paper (requires flag=1; default --limit 1)",
    )
    rebalance.add_argument(
        "--limit",
        type=int,
        default=1,
        help="max paper tickets to POST with --submit-paper (default 1; smallest notional first; no all-flag, use a high number)",
    )
    rebalance.add_argument(
        "--force-submit",
        action="store_true",
        help="owner override: POST paper even if today's go/no-go is HOLD/WAIT_OPEN/NO_GO (still paper rails)",
    )
    paper_submit = commands.add_parser(
        "paper-submit",
        help="POST one tiny Alpaca paper order (requires flag=1, paper host, keys)",
    )
    paper_submit.add_argument("--symbol", required=True, help="universe ticker, e.g. SPY")
    paper_submit.add_argument(
        "--side",
        choices=("buy", "sell"),
        default="buy",
        help="order side (default buy)",
    )
    paper_submit.add_argument("--qty", help="share quantity (use this or --notional)")
    paper_submit.add_argument("--notional", help="dollar notional (use this or --qty)")
    paper_submit.add_argument(
        "--client-order-id",
        help="idempotency key (max 48 chars; default is a stable paper-submit key)",
    )
    paper_cancel = commands.add_parser(
        "paper-cancel",
        help="DELETE Alpaca paper orders (requires flag=1, paper host, keys)",
    )
    paper_cancel.add_argument("--order-id", help="one paper order UUID to cancel")
    paper_cancel.add_argument(
        "--open",
        action="store_true",
        help="cancel open paper orders (uses --limit; default 1)",
    )
    paper_cancel.add_argument(
        "--limit",
        type=int,
        default=1,
        help="max open orders to DELETE with --open (default 1; no all-flag)",
    )
    ledger = commands.add_parser(
        "ledger",
        aliases=["paper-ledger"],
        help="read-only inspect of a local paper ledger (no POST, no write)",
    )
    ledger.add_argument(
        "--ledger",
        help="sqlite ledger path (required)",
    )
    ledger.add_argument(
        "--fixtures",
        action="store_true",
        help="label mark kinds and print fixture-mark MTM versus fixtures/marks (not alpha)",
    )
    ledger.add_argument(
        "--marks",
        help="fixture mark book JSON or alias: liquid (default), two-name",
    )
    ledger.add_argument(
        "--write",
        action="store_true",
        help="refused; inspect is read-only and does not write the ledger",
    )
    paper_performance = commands.add_parser(
        "paper-performance",
        aliases=["paper-snapshot"],
        help="read-only Alpaca paper equity/cash/positions/orders/fills snapshot",
    )
    paper_performance.add_argument(
        "--write",
        action="store_true",
        help="write dated JSON under docs/performance/ (paper snapshot, not alpha)",
    )
    paper_performance.add_argument(
        "--out",
        help="snapshot path (implies write; default docs/performance/YYYY-MM-DD.json)",
    )
    telemetry = commands.add_parser(
        "telemetry",
        help="daily paper telemetry pack (research + paper snapshot; read-only)",
    )
    telemetry.add_argument(
        "--write",
        action="store_true",
        help="write dated JSON under docs/telemetry/ (paper pack, not alpha)",
    )
    telemetry.add_argument(
        "--out",
        help="telemetry path (implies write; default docs/telemetry/YYYY-MM-DD.json)",
    )
    telemetry.add_argument(
        "--md",
        action="store_true",
        help="also write a short markdown summary next to the JSON",
    )
    commands.add_parser(
        "runtime-env",
        help="print Runtime Secret / env presence only (never values)",
    )
    go_nogo = commands.add_parser(
        "go-nogo",
        aliases=["decision-check"],
        help="daily paper go/no-go checklist (research + snapshot; --live feed health)",
    )
    go_nogo.add_argument(
        "--live",
        action="store_true",
        help="check live Quiver/World Monitor health (fail closed if keys missing)",
    )
    go_nogo.add_argument(
        "--out",
        help="write the JSON here (default: docs/decision/YYYY-MM-DD.json)",
    )
    go_nogo.add_argument(
        "--md",
        action="store_true",
        help="also write a short markdown summary next to the JSON",
    )
    baseline = commands.add_parser(
        "baseline-compare",
        help="walk-forward conviction vs equal-weight top-K on frozen fixture marks (not alpha)",
    )
    baseline.add_argument(
        "--fixtures",
        action="store_true",
        help="load the frozen baseline series (required; the only supported input)",
    )
    baseline.add_argument(
        "--write",
        action="store_true",
        help="write dated JSON under docs/baseline/ (fixture-mark compare, not alpha)",
    )
    baseline.add_argument(
        "--out",
        help="compare path (implies write; default docs/baseline/YYYY-MM-DD.json)",
    )
    research = commands.add_parser(
        "research",
        help="daily live research book: intel universe, rank/diagnose, proposed targets",
    )
    research.add_argument(
        "--live",
        action="store_true",
        help="pull Quiver + World Monitor and write docs/research/YYYY-MM-DD.json",
    )
    research.add_argument(
        "--out",
        help="write the JSON here (default: docs/research/YYYY-MM-DD.json)",
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
        "rebalance",
        "baseline-compare",
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
    if args.command == "feeds":
        if not args.live:
            print("feeds requires --live", file=sys.stderr)
            return 2
        try:
            report = pull_live_feeds()
        except LiveFeedConfigError as error:
            print(str(error), file=sys.stderr)
            return 2
        report = dict(report)
        report["runtime_env"] = runtime_env_status()
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command == "paper-account":
        missing = missing_paper_keys()
        if missing:
            print(
                "paper-account missing env: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        if paper_submit_enabled():
            print(
                "SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1; remote paper POST still "
                "requires paper-submit or rebalance --submit-paper. "
                "paper-account stays read-only.",
                file=sys.stderr,
            )
        try:
            client = paper_broker_client(paper_host())
            proposal = None
            if args.dry_run:
                proposal = {
                    "ticker": "NVDA",
                    "side": "buy",
                    "idempotency_key": "paper-account-dry-run",
                }
            report = client.read_smoke(proposal)
        except LiveEndpointError as error:
            print(str(error), file=sys.stderr)
            return 2
        except NotImplementedError as error:
            print(str(error), file=sys.stderr)
            return 2
        report = dict(report)
        report["submit_flag"] = paper_submit_flag()
        report["runtime_env"] = runtime_env_status()
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command == "rebalance":
        live = getattr(args, "live", False)
        intensity = getattr(args, "intensity", False)
        if (intensity or live) and getattr(args, "rank", False):
            print(
                "rebalance --intensity/--live requires the drift book (omit --rank)",
                file=sys.stderr,
            )
            return 2
        if live:
            missing_intel = missing_live_feed_keys()
            if missing_intel:
                print(
                    "rebalance --live missing env: " + ", ".join(missing_intel),
                    file=sys.stderr,
                )
                return 2
        missing = missing_paper_keys()
        if missing:
            print(
                "rebalance missing env: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        apply_local = getattr(args, "apply_local", False)
        submit_paper = getattr(args, "submit_paper", False)
        ledger = getattr(args, "ledger", None)
        if apply_local and submit_paper:
            print(
                "rebalance --apply-local and --submit-paper are separate; use one",
                file=sys.stderr,
            )
            return 2
        if apply_local and not ledger:
            print("rebalance --apply-local requires --ledger", file=sys.stderr)
            return 2
        if submit_paper:
            try:
                require_paper_submit(explicit=True)
            except (PaperSubmitRefused, LiveEndpointError, ValueError) as error:
                print(str(error), file=sys.stderr)
                return 2
            if not getattr(args, "force_submit", False):
                blocked = decision_submit_block()
                if blocked:
                    print(blocked, file=sys.stderr)
                    return 2
        elif paper_submit_enabled():
            print(
                "SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1; remote paper POST still "
                "requires --submit-paper. --apply-local writes the local "
                "ledger only.",
                file=sys.stderr,
            )
        try:
            client = paper_broker_client(paper_host())
            mark_book_path = resolve_mark_book_path(args.marks) if args.marks else None
            fixtures = Path(__file__).resolve().parent.parent / "fixtures"
            report = proposed_rebalance(
                fixtures=fixtures,
                mark_book_path=mark_book_path,
                client=client,
                signal="rank" if getattr(args, "rank", False) else "drift",
                intensity=intensity or live,
                live=live,
                prefer_paper_marks=bool(live or submit_paper),
            )
            if apply_local:
                report = apply_local_rebalance(
                    report,
                    ledger_path=ledger,
                    fixtures=fixtures,
                )
            elif submit_paper:
                report = submit_paper_rebalance(
                    report,
                    client,
                    limit=getattr(args, "limit", 1),
                    explicit=True,
                )
        except LiveFeedConfigError as error:
            print(str(error), file=sys.stderr)
            return 2
        except PaperSubmitRefused as error:
            print(str(error), file=sys.stderr)
            return 2
        except LiveEndpointError as error:
            print(str(error), file=sys.stderr)
            return 2
        except NotImplementedError as error:
            print(str(error), file=sys.stderr)
            return 2
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        report = dict(report)
        report["submit_flag"] = paper_submit_flag()
        report["runtime_env"] = runtime_env_status()
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command == "paper-submit":
        missing = missing_paper_keys()
        if missing:
            print("paper-submit missing env: " + ", ".join(missing), file=sys.stderr)
            return 2
        qty = getattr(args, "qty", None)
        notional = getattr(args, "notional", None)
        if (qty in (None, "")) == (notional in (None, "")):
            print("paper-submit requires exactly one of --qty or --notional", file=sys.stderr)
            return 2
        try:
            require_paper_submit(explicit=True)
            client = paper_broker_client(paper_host())
            account = client.account()
            clock = client.clock()
            key = getattr(args, "client_order_id", None)
            if not key:
                size_label = f"q:{qty}" if qty not in (None, "") else f"n:{notional}"
                key = f"ps:{args.symbol}:{args.side}:{size_label}"
            proposal = {
                "symbol": args.symbol,
                "side": args.side,
                "client_order_id": key,
            }
            if qty not in (None, ""):
                proposal["qty"] = qty
            else:
                proposal["notional"] = notional
            order = client.post_paper_order(proposal, explicit=True)
            positions = client.positions()
            orders = client.orders()
        except (PaperSubmitRefused, LiveEndpointError, ValueError, RuntimeError, NotImplementedError) as error:
            print(str(error), file=sys.stderr)
            return 2
        report = {
            "mode": "alpaca-paper-submit",
            "ok": True,
            "submitted": True,
            "order_post": "paper",
            "submit_flag": paper_submit_flag(),
            "clock": clock,
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
            "order": order,
            "orders": orders,
            "positions": {
                "n": len(positions),
                "symbols": {
                    str(row.get("symbol")): str(row.get("qty") or "0")
                    for row in positions
                    if row.get("symbol")
                },
            },
            "runtime_env": runtime_env_status(),
            "note": (
                "Alpaca paper POST only. Not live money. Kill by setting "
                "SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0."
            ),
        }
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command == "paper-cancel":
        missing = missing_paper_keys()
        if missing:
            print("paper-cancel missing env: " + ", ".join(missing), file=sys.stderr)
            return 2
        order_id = getattr(args, "order_id", None)
        cancel_open = bool(getattr(args, "open", False))
        if (order_id in (None, "")) == (not cancel_open):
            print("paper-cancel requires exactly one of --order-id or --open", file=sys.stderr)
            return 2
        try:
            require_paper_submit(explicit=True)
            client = paper_broker_client(paper_host())
            if cancel_open:
                result = client.cancel_open_paper_orders(
                    explicit=True,
                    limit=int(getattr(args, "limit", 1)),
                )
            else:
                cancelled = client.cancel_paper_order(order_id, explicit=True)
                result = {
                    "cancelled": [cancelled],
                    "errors": [],
                    "n_cancelled": 1,
                    "n_errors": 0,
                }
        except (PaperSubmitRefused, LiveEndpointError, ValueError, RuntimeError, NotImplementedError) as error:
            print(str(error), file=sys.stderr)
            return 2
        report = {
            "mode": "alpaca-paper-cancel",
            "ok": result.get("n_errors", 0) == 0,
            "submitted": False,
            "cancelled": True,
            "order_post": "disabled",
            "order_delete": "paper",
            "submit_flag": paper_submit_flag(),
            "n_cancelled": result.get("n_cancelled"),
            "n_errors": result.get("n_errors"),
            "orders": result.get("cancelled"),
            "errors": result.get("errors"),
            "runtime_env": runtime_env_status(),
            "note": (
                "Alpaca paper DELETE only. Not live money. Not a submit. "
                "Kill by setting SIGNAL_SIM_ALPACA_PAPER_SUBMIT=0. "
                "Reprint rebalance --fixtures --live before the next submit."
            ),
        }
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command in {"ledger", "paper-ledger"}:
        ledger = getattr(args, "ledger", None)
        if not ledger:
            print("ledger inspect requires --ledger", file=sys.stderr)
            return 2
        if getattr(args, "write", False):
            print(WRITE_REFUSED, file=sys.stderr)
            return 2
        if paper_submit_enabled():
            print(
                "SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1 is unused for ledger inspect; "
                "this path is read-only and does not POST.",
                file=sys.stderr,
            )
        fixtures = None
        mark_book_path = getattr(args, "marks", None)
        if getattr(args, "fixtures", False) or mark_book_path:
            fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        try:
            report = inspect_ledger(
                ledger,
                fixtures=fixtures,
                mark_book_path=mark_book_path,
            )
        except FileNotFoundError as error:
            print(str(error), file=sys.stderr)
            return 2
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        mtm = report.get("mtm") or {}
        if mtm:
            print(
                f"ledger inspect: n_orders={report.get('n_orders')} "
                f"n_fills={report.get('n_fills')} "
                f"fixture-mark total_pnl={mtm.get('total_pnl')} (not alpha)",
                file=sys.stderr,
            )
        else:
            print(
                f"ledger inspect: n_orders={report.get('n_orders')} "
                f"n_fills={report.get('n_fills')} (read-only)",
                file=sys.stderr,
            )
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command in {"paper-performance", "paper-snapshot"}:
        missing = missing_paper_keys()
        if missing:
            print(
                "paper-performance missing env: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        if paper_submit_enabled():
            print(
                "SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1 is unused for paper-performance; "
                "this path is read-only and does not POST.",
                file=sys.stderr,
            )
        write = bool(getattr(args, "write", False) or getattr(args, "out", None))
        try:
            client = paper_broker_client(paper_host())
            report = paper_performance_snapshot(client)
            if write:
                out = Path(args.out) if args.out else default_snapshot_path()
                written = write_paper_performance(report, out)
                report = dict(report)
                report["snapshot_path"] = str(written)
                report["write_note"] = (
                    "Dated paper snapshot JSON. Paper account figures only. Not alpha."
                )
        except LiveEndpointError as error:
            print(str(error), file=sys.stderr)
            return 2
        except NotImplementedError as error:
            print(str(error), file=sys.stderr)
            return 2
        except RuntimeError as error:
            print(str(error), file=sys.stderr)
            return 2
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        report = dict(report)
        report["submit_flag"] = paper_submit_flag()
        report["runtime_env"] = runtime_env_status()
        summary = report.get("summary") or {}
        print(
            f"paper-performance: equity={summary.get('equity')} "
            f"cash={summary.get('cash')} n_positions={summary.get('n_positions')} "
            f"n_open_orders={summary.get('n_open_orders')} "
            f"n_fills={summary.get('n_fills')} (paper, not alpha)",
            file=sys.stderr,
        )
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command == "telemetry":
        if paper_submit_enabled():
            print(
                "SIGNAL_SIM_ALPACA_PAPER_SUBMIT=1 is unused for telemetry; "
                "this path is read-only and does not POST.",
                file=sys.stderr,
            )
        write = bool(getattr(args, "write", False) or getattr(args, "out", None))
        client = None
        missing = missing_paper_keys()
        try:
            if not missing:
                client = paper_broker_client(paper_host())
            report = build_telemetry_pack(client=client)
            if write:
                out = Path(args.out) if args.out else default_telemetry_path()
                written = write_telemetry_pack(
                    report,
                    out,
                    markdown=bool(getattr(args, "md", False)),
                )
                report = dict(report)
                report["telemetry_path"] = str(written)
        except LiveEndpointError as error:
            print(str(error), file=sys.stderr)
            return 2
        except (NotImplementedError, RuntimeError, ValueError) as error:
            print(str(error), file=sys.stderr)
            return 2
        report = dict(report)
        report["submit_flag"] = paper_submit_flag()
        report["runtime_env"] = runtime_env_status()
        print(
            f"telemetry: date={report.get('date')} equity={report.get('equity')} "
            f"cash={report.get('cash')} gross={report.get('gross')} "
            f"cash_reserve_frac={report.get('cash_reserve_frac')} "
            f"equity_delta={report.get('equity_delta')} (paper, not alpha)",
            file=sys.stderr,
        )
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command == "runtime-env":
        report = runtime_env_status()
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 2
    if args.command == "research":
        if not args.live:
            print("research requires --live", file=sys.stderr)
            return 2
        missing_intel = missing_live_feed_keys()
        if missing_intel:
            print("research --live missing env: " + ", ".join(missing_intel), file=sys.stderr)
            return 2
        out = Path(args.out) if args.out else research_artifact_path()
        try:
            report = run_research(out_path=out)
        except LiveFeedConfigError as error:
            print(str(error), file=sys.stderr)
            return 2
        except (ValueError, NotImplementedError) as error:
            print(str(error), file=sys.stderr)
            return 2
        report = dict(report)
        report["runtime_env"] = runtime_env_status()
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    if args.command in {"go-nogo", "decision-check"}:
        live = bool(getattr(args, "live", False))
        live_feeds = None
        missing_intel: list[str] = []
        keys_present = None
        if live:
            missing_intel = missing_live_feed_keys()
            keys_present = not missing_intel
            if not missing_intel:
                try:
                    live_feeds = pull_live_feeds()
                except LiveFeedConfigError as error:
                    print(str(error), file=sys.stderr)
                    return 2
        try:
            report = build_go_nogo(
                live=live,
                feeds=live_feeds,
                live_keys_missing=missing_intel,
                live_keys_present=keys_present,
            )
            out = Path(args.out) if args.out else default_decision_path()
            written = write_go_nogo(report, out, markdown=bool(getattr(args, "md", False)))
            report = dict(report)
            report["decision_path"] = str(written)
        except (LiveFeedConfigError, ValueError, NotImplementedError) as error:
            print(str(error), file=sys.stderr)
            return 2
        report = dict(report)
        report["submit_flag"] = paper_submit_flag()
        report["runtime_env"] = runtime_env_status()
        print(
            f"go-nogo: verdict={report.get('verdict')} "
            f"recommend_submit={report.get('recommend_submit')} "
            f"equity_delta={report.get('equity_delta')} (paper, not alpha)",
            file=sys.stderr,
        )
        print(json.dumps(report, separators=(",", ":")))
        if missing_intel:
            return 2
        return 0 if report.get("ok") is True else 1
    if args.command == "baseline-compare":
        write = bool(getattr(args, "write", False) or getattr(args, "out", None))
        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        try:
            report = run_baseline_compare(fixtures=fixtures)
            if write:
                out = Path(args.out) if args.out else default_baseline_path()
                written = write_baseline_compare(report, out)
                report = dict(report)
                report["baseline_path"] = str(written)
        except ValueError as error:
            print(str(error), file=sys.stderr)
            return 2
        report = dict(report)
        report["runtime_env"] = runtime_env_status()
        print(
            f"baseline-compare: conviction_pnl={report.get('equity_delta_conviction')} "
            f"equal_pnl={report.get('equity_delta_equal')} "
            f"(fixture-mark, not alpha, not fitted)",
            file=sys.stderr,
        )
        print(json.dumps(report, separators=(",", ":")))
        return 0 if report.get("ok") is True else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
