"""Daily research artifact: expanded universe and a book rebalance can consume."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from signal_sim import cli
from signal_sim.events import Event
from signal_sim.indicators import UNIVERSE
from signal_sim.rebalance import proposed_rebalance
from signal_sim.research import (
    load_research_artifact,
    run_research,
    write_paper_performance,
)
from signal_sim.universe import load_liquid_allowlist


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"


def _event(ticker, source="quiver", kind="news", event_id=None):
    return Event.from_dict(
        {
            "id": event_id or f"{source}-{ticker}",
            "source": source,
            "kind": kind,
            "ticker": ticker,
            "entities": [ticker],
            "headline": "SECRET HEADLINE about a person",
            "url": "https://example.invalid/pii",
            "occurred_at": "2026-09-04T16:00:00Z",
            "filed_at": "2026-09-04T16:00:00Z" if kind in {"congress_trade", "insider"} else None,
            "observed_at": "2026-09-04T16:00:00Z",
            "confidence": 1.0,
            "raw_ref": "raw-pii-ref",
        }
    )


def _empty_account():
    return {
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "100000",
        "equity": "100000",
        "buying_power": "200000",
        "trading_blocked": False,
        "account_blocked": False,
    }


def _clock():
    return {
        "timestamp": "2026-09-04T12:00:00Z",
        "is_open": False,
        "next_open": "2026-09-08T13:30:00Z",
        "next_close": "2026-09-08T20:00:00Z",
    }


class ResearchArtifactTests(unittest.TestCase):
    def test_research_writes_book_and_skips_pii(self):
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "2026-09-04.json"
        events = [
            _event("TSLA", kind="congress_trade"),
            _event("TSLA", kind="news", event_id="tsla-news"),
            _event("NVDA", kind="insider"),
        ]
        report = run_research(
            fixtures=FIXTURES,
            live_events=events,
            when=datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc),
            out_path=out,
        )
        self.assertTrue(out.is_file())
        self.assertEqual(report["mode"], "daily-research")
        self.assertEqual(report["signal"], "research-live")
        self.assertIn("TSLA", report["universe"]["operating"])
        self.assertIn("TSLA", report["universe"]["intel"])
        self.assertTrue(set(UNIVERSE).issubset(set(report["universe"]["operating"])))
        tickers = {row["ticker"] for row in report["rank"]}
        self.assertIn("TSLA", tickers)
        self.assertIn("NVDA", tickers)
        proposed = {row["ticker"] for row in report["proposed_book"]["targets"]}
        self.assertTrue(proposed)
        dumped = json.dumps(report)
        self.assertNotIn("SECRET HEADLINE", dumped)
        self.assertNotIn("example.invalid", dumped)
        self.assertNotIn("raw-pii-ref", dumped)
        self.assertNotIn('"person"', dumped)
        loaded = load_research_artifact(out)
        self.assertEqual(loaded["proposed_book"]["n_targets"], report["proposed_book"]["n_targets"])

    def test_rebalance_live_uses_research_targets(self):
        research = run_research(
            fixtures=FIXTURES,
            live_events=[
                _event("TSLA", kind="congress_trade"),
                _event("AMD", kind="news"),
            ],
            when=datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc),
        )
        report = proposed_rebalance(
            fixtures=FIXTURES,
            account=_empty_account(),
            positions=[],
            clock=_clock(),
            live=True,
            research=research,
        )
        self.assertEqual(report["signal"], "research-live")
        self.assertEqual(report["research_date"], research["date"])
        wanted = {row["ticker"] for row in research["proposed_book"]["targets"]}
        ticket_names = {row["symbol"] for row in report["tickets"]}
        skipped = {row["ticker"] for row in report["skipped"]}
        self.assertTrue(wanted)
        self.assertTrue(wanted <= (ticket_names | skipped))

    def test_cli_research_writes_without_pii(self):
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "research.json"

        def env(name):
            values = {
                "QUIVER_API_KEY": "quiver-key",
                "WORLD_MONITOR_KEY": "wm-key",
            }
            return values.get(name)

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.live_feeds.read_env", side_effect=env), mock.patch(
            "signal_sim.research.fetch_live_feed_payloads",
            return_value=(
                [_event("TSLA", kind="congress_trade")],
                [_event("AMD", source="worldmonitor", kind="intel_brief")],
            ),
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["research", "--live", "--out", str(out)])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["ok"])
        self.assertTrue(out.is_file())
        dumped = printed.getvalue() + error.getvalue() + out.read_text(encoding="utf-8")
        self.assertNotIn("SECRET HEADLINE", dumped)
        self.assertNotIn("example.invalid", dumped)
        self.assertIn("TSLA", payload["universe"]["operating"])

    def test_cli_research_requires_live(self):
        error = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(error):
            code = cli.main(["research"])
        self.assertEqual(code, 2)
        self.assertIn("requires --live", error.getvalue())

    def test_paper_performance_write_is_sanitized(self):
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "paper.json"

        class _Client:
            def account(self):
                return {
                    "status": "ACTIVE",
                    "currency": "USD",
                    "cash": "1",
                    "equity": "2",
                    "buying_power": "3",
                    "trading_blocked": False,
                    "account_blocked": False,
                    "account_number": "PA123HIDE",
                }

            def positions(self):
                return [{"symbol": "SPY", "qty": "1", "side": "long"}]

            def clock(self):
                return {"is_open": False, "timestamp": "2026-09-04T12:00:00Z"}

            def orders(self, *, status="open", limit=50):
                return [{"symbol": "SPY", "id": "ord-1", "status": "new"}]

        report = write_paper_performance(
            _Client(),
            out_path=out,
            when=datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(out.is_file())
        self.assertEqual(report["positions"]["symbols"]["SPY"], "1")
        self.assertEqual(report["open_orders"]["n"], 1)
        dumped = json.dumps(report)
        self.assertNotIn("PA123HIDE", dumped)


class QuiverAcceptTests(unittest.TestCase):
    def test_live_accepts_allowlisted_name_outside_fixture(self):
        from signal_sim.sources.altdata import live

        payloads = {
            "congresstrading": [
                {
                    "Amount": 1001,
                    "ReportDate": "2026-08-10",
                    "Representative": "Hidden Person",
                    "Ticker": "TSLA",
                    "Transaction": "Purchase",
                    "TransactionDate": "2026-07-15",
                }
            ]
        }

        def urlopen(request):
            class _Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self):
                    return json.dumps(payloads["congresstrading"]).encode("utf-8")

            return _Response()

        with mock.patch("signal_sim.sources.altdata.read_env", return_value="k"), mock.patch(
            "signal_sim.sources.altdata.urllib.request.urlopen", side_effect=urlopen
        ):
            default = live()
            expanded = live(accept=load_liquid_allowlist())
        self.assertEqual(default, [])
        self.assertEqual(len(expanded), 1)
        self.assertEqual(expanded[0]["ticker"], "TSLA")
        self.assertEqual(expanded[0]["kind"], "congress_trade")
        self.assertNotIn("Hidden Person", json.dumps({"ticker": expanded[0]["ticker"]}))
