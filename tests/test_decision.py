"""Daily go/no-go checklist. No network. Not alpha."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from signal_sim import cli
from signal_sim.decision import (
    BLOCKING_VERDICTS,
    build_go_nogo,
    decision_submit_block,
    recommend_submit_for,
    write_go_nogo,
)


WHEN = datetime(2026, 9, 4, 16, 0, tzinfo=timezone.utc)


def _research(**overrides):
    raw = {
        "date": "2026-09-04",
        "research_at": "2026-09-04T16:00:00Z",
        "proposed_book": {
            "targets": [
                {"ticker": "NVDA", "score": 10.0, "target_frac": 0.2},
                {"ticker": "XLE", "score": 5.0, "target_frac": 0.12},
            ],
            "max_gross_invest": 0.8,
            "book_gross": 0.32,
        },
        "feeds": {"quiver": {"n": 4, "tickers": {"NVDA": 4}}, "worldmonitor": {"n": 2}},
        "universe": {"operating": ["NVDA", "XLE"]},
    }
    raw.update(overrides)
    return raw


def _performance(**overrides):
    raw = {
        "account": {"cash": "20000", "equity": "100000"},
        "positions": {"n": 0, "symbols": {}, "rows": []},
        "open_orders": [],
        "n_open_orders": 0,
        "clock": {"is_open": True},
        "summary": {"clock_is_open": True, "equity": "100000", "cash": "20000"},
        "label": "paper",
    }
    raw.update(overrides)
    return raw


class GoNoGoVerdictTests(unittest.TestCase):
    def test_trade_when_book_is_off_target_and_clock_open(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=_research(),
            performance=_performance(),
        )
        self.assertEqual(report["verdict"], "TRADE")
        self.assertTrue(report["recommend_submit"])
        self.assertTrue(report["ok"])
        self.assertTrue(report["not_alpha"])
        self.assertTrue(report["paper_only"])
        self.assertFalse(report["submitted"])
        self.assertTrue(any(row["reason"] == "weight_band" for row in report["off_target"]))
        self.assertIn(report["verdict"], ("TRADE", "HOLD", "WAIT_OPEN", "NO_GO"))

    def test_hold_when_open_orders_exist(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=_research(),
            performance=_performance(n_open_orders=3, open_orders=[{"symbol": "NVDA"}]),
        )
        self.assertEqual(report["verdict"], "HOLD")
        self.assertFalse(report["recommend_submit"])
        self.assertTrue(report["ok"])
        self.assertEqual(report["n_open_orders"], 3)
        self.assertTrue(any("open paper orders" in row for row in report["reasons"]))

    def test_no_go_when_research_missing(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=None,
            performance=_performance(),
            feeds={"quiver": {"n": 4}, "worldmonitor": {"n": 2}},
        )
        self.assertEqual(report["verdict"], "NO_GO")
        self.assertFalse(report["recommend_submit"])
        self.assertFalse(report["ok"])
        self.assertTrue(any("missing today's research" in row for row in report["reasons"]))

    def test_no_go_when_feeds_are_dead(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=_research(feeds={"quiver": {"n": 0}, "worldmonitor": {"n": 0}}),
            performance=_performance(),
        )
        self.assertEqual(report["verdict"], "NO_GO")
        self.assertFalse(report["recommend_submit"])
        self.assertTrue(any("unhealthy" in row for row in report["reasons"]))

    def test_no_go_when_live_keys_missing(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=_research(),
            performance=_performance(),
            live=True,
            live_keys_missing=["QUIVER_API_KEY", "WORLD_MONITOR_KEY"],
            live_keys_present=False,
        )
        self.assertEqual(report["verdict"], "NO_GO")
        self.assertTrue(any("keys missing" in row for row in report["reasons"]))
        self.assertFalse(report["feeds"]["keys_ok"])

    def test_wait_open_when_clock_closed(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=_research(),
            performance=_performance(clock={"is_open": False}, summary={"clock_is_open": False}),
        )
        self.assertEqual(report["verdict"], "WAIT_OPEN")
        self.assertFalse(report["recommend_submit"])
        self.assertTrue(report["ok"])
        self.assertIs(report["clock_is_open"], False)

    def test_hold_when_on_target(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=_research(),
            performance=_performance(
                account={"cash": "68000", "equity": "100000"},
                positions={
                    "n": 2,
                    "symbols": {"NVDA": "1", "XLE": "1"},
                    "rows": [
                        {"symbol": "NVDA", "market_value": "20000", "qty": "1"},
                        {"symbol": "XLE", "market_value": "12000", "qty": "1"},
                    ],
                },
            ),
        )
        self.assertEqual(report["verdict"], "HOLD")
        self.assertFalse(report["recommend_submit"])
        self.assertEqual(report["off_target"], [])

    def test_soft_drawdown_warns_without_blocking(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=_research(),
            performance=_performance(account={"cash": "1000", "equity": "90000"}),
        )
        self.assertTrue(report["equity_warn"])
        self.assertNotEqual(report["verdict"], "NO_GO")
        self.assertTrue(any("soft_dd" in row for row in report["reasons"]))

    def test_open_orders_beat_closed_clock(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=_research(),
            performance=_performance(
                n_open_orders=1,
                open_orders=[{"symbol": "NVDA"}],
                clock={"is_open": False},
            ),
        )
        self.assertEqual(report["verdict"], "HOLD")

    def test_recommend_submit_is_exhaustive(self):
        self.assertTrue(recommend_submit_for("TRADE"))
        for verdict in BLOCKING_VERDICTS:
            self.assertFalse(recommend_submit_for(verdict))
        with self.assertRaises(ValueError):
            recommend_submit_for("MAYBE")


class GoNoGoIoTests(unittest.TestCase):
    def test_write_and_cli_alias(self):
        tmp = Path(tempfile.mkdtemp())
        out = tmp / "2026-09-04.json"
        report = build_go_nogo(
            root=tmp,
            when=WHEN,
            research=_research(),
            performance=_performance(),
        )
        written = write_go_nogo(report, out, markdown=True)
        self.assertTrue(written.is_file())
        self.assertTrue(written.with_suffix(".md").is_file())
        self.assertIn("Not alpha", written.with_suffix(".md").read_text(encoding="utf-8"))

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.cli.build_go_nogo", return_value=report), mock.patch(
            "signal_sim.cli.default_decision_path", return_value=out
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["decision-check", "--out", str(out), "--md"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["verdict"], "TRADE")
        self.assertIn("go-nogo:", error.getvalue())

    def test_cli_live_missing_keys_exits_2(self):
        report = build_go_nogo(
            root=Path(tempfile.mkdtemp()),
            when=WHEN,
            research=_research(),
            performance=_performance(),
            live=True,
            live_keys_missing=["QUIVER_API_KEY"],
            live_keys_present=False,
        )
        tmp = Path(tempfile.mkdtemp()) / "decision.json"
        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch(
            "signal_sim.cli.missing_live_feed_keys", return_value=["QUIVER_API_KEY"]
        ), mock.patch("signal_sim.cli.build_go_nogo", return_value=report), redirect_stdout(
            printed
        ), redirect_stderr(error):
            code = cli.main(["go-nogo", "--live", "--out", str(tmp)])
        self.assertEqual(code, 2)

    def test_submit_block_message(self):
        tmp = Path(tempfile.mkdtemp())
        report = build_go_nogo(
            root=tmp,
            when=WHEN,
            research=_research(),
            performance=_performance(n_open_orders=2, open_orders=[{}]),
        )
        write_go_nogo(report, tmp / "docs" / "decision" / "2026-09-04.json")
        blocked = decision_submit_block(root=tmp, when=WHEN)
        self.assertIsNotNone(blocked)
        self.assertIn("HOLD", blocked)
        self.assertIn("--force-submit", blocked)
        self.assertIsNone(
            decision_submit_block(
                root=tmp,
                when=WHEN,
                artifact={"verdict": "TRADE", "recommend_submit": True},
            )
        )


class RebalanceDecisionGateTests(unittest.TestCase):
    def test_submit_paper_refuses_hold_without_force(self):
        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.cli.missing_paper_keys", return_value=[]), mock.patch(
            "signal_sim.cli.require_paper_submit"
        ), mock.patch(
            "signal_sim.cli.decision_submit_block",
            return_value="rebalance --submit-paper refused: today's go/no-go verdict is HOLD",
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["rebalance", "--fixtures", "--submit-paper"])
        self.assertEqual(code, 2)
        self.assertIn("HOLD", error.getvalue())
        self.assertEqual(printed.getvalue(), "")

    def test_force_submit_skips_decision_block(self):
        calls = []

        def env(name):
            if name == "SIGNAL_SIM_ALPACA_PAPER_SUBMIT":
                return "1"
            mapping = {
                "ALPACA_PAPER_API_KEY": "PA123HIDE",
                "ALPACA_PAPER_API_SECRET": "paper-secret",
                "ALPACA_PAPER_API_BASE_URL": "https://paper-api.alpaca.markets",
            }
            return mapping.get(name)

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", side_effect=env), mock.patch(
            "signal_sim.runtime_env.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.cli.decision_submit_block",
            side_effect=AssertionError("force-submit must not consult the block"),
        ), mock.patch(
            "signal_sim.cli.proposed_rebalance",
            return_value={
                "ok": True,
                "tickets": [],
                "mode": "paper-rebalance-dry-run",
            },
        ), mock.patch(
            "signal_sim.cli.submit_paper_rebalance",
            return_value={
                "ok": True,
                "submitted": True,
                "order_post": "paper",
                "n_paper_submitted": 0,
                "tickets": [],
            },
        ), mock.patch(
            "signal_sim.cli.paper_broker_client", return_value=object()
        ), mock.patch("signal_sim.cli.paper_host", return_value="https://paper-api.alpaca.markets"), redirect_stdout(
            printed
        ), redirect_stderr(error):
            code = cli.main(
                ["rebalance", "--fixtures", "--submit-paper", "--force-submit", "--limit", "1"]
            )
            calls.append(code)
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["submitted"])


if __name__ == "__main__":
    unittest.main()
