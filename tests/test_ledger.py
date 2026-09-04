"""Read-only local ledger inspect. Built from apply-local fixture fills."""

import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from signal_sim import cli
from signal_sim.ledger import inspect_ledger
from signal_sim.rebalance import apply_local_rebalance, proposed_rebalance
from signal_sim.sim import load_mark_book


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"


def _empty_account(**overrides):
    row = {
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "100000",
        "equity": "100000",
        "buying_power": "200000",
        "trading_blocked": False,
        "account_blocked": False,
        "pattern_day_trader": False,
        "shorting_enabled": True,
    }
    row.update(overrides)
    return row


def _clock():
    return {
        "timestamp": "2026-09-04T12:00:00Z",
        "is_open": False,
        "next_open": "2026-09-08T13:30:00Z",
        "next_close": "2026-09-08T20:00:00Z",
    }


class _FakeMarkClient:
    def __init__(self, paper_marks=None):
        self.paper_marks = paper_marks or {}

    def account(self):
        return _empty_account()

    def positions(self):
        return []

    def clock(self):
        return _clock()

    def sizing_marks(self, symbols):
        return {
            ticker: dict(row)
            for ticker, row in self.paper_marks.items()
            if ticker in set(symbols)
        }


def _apply_local_ledger(tmp: str) -> tuple[str, dict]:
    ledger = str(Path(tmp) / "apply-local.sqlite")
    report = proposed_rebalance(
        fixtures=FIXTURES,
        account=_empty_account(),
        positions=[],
        clock=_clock(),
        client=_FakeMarkClient(
            {
                "AAPL": {
                    "entry_px": 220.5,
                    "kind": "last_trade",
                    "source": "alpaca_paper_data",
                }
            }
        ),
    )
    applied = apply_local_rebalance(
        report,
        ledger_path=ledger,
        fixtures=FIXTURES,
        kill_root=tmp,
    )
    return ledger, applied


def _digest_path(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


def _order_rows(ledger: str) -> list[tuple]:
    connection = sqlite3.connect(ledger)
    try:
        return connection.execute(
            "SELECT o.ticker, o.side, o.size_frac, o.status, f.price, "
            "COALESCE(f.cost, 0), f.filled_at "
            "FROM orders o JOIN fills f ON f.order_id = o.order_id "
            "ORDER BY o.ticker"
        ).fetchall()
    finally:
        connection.close()


class LedgerInspectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger, self.applied = _apply_local_ledger(self.tmp)

    def test_inspect_reads_apply_local_counts_symbols_sides_qtys(self):
        report = inspect_ledger(self.ledger, fixtures=FIXTURES)
        fills = _order_rows(self.ledger)
        self.assertEqual(report["mode"], "paper-ledger-inspect")
        self.assertTrue(report["read_only"])
        self.assertFalse(report["submitted"])
        self.assertEqual(report["order_post"], "disabled")
        self.assertGreater(self.applied["n_applied"], 0)
        self.assertEqual(report["n_orders"], self.applied["n_applied"])
        self.assertEqual(report["n_fills"], self.applied["n_applied"])
        self.assertEqual(report["n_orders"], len(fills))
        self.assertIn("NVDA", {row["symbol"] for row in report["orders"]})
        self.assertNotIn("AAPL", {row["symbol"] for row in report["orders"]})
        book = load_mark_book()
        allocation = float(report["allocation"])
        by_ticker = {row["symbol"]: row for row in report["orders"]}
        for ticker, side, size_frac, status, price, cost, filled_at in fills:
            row = by_ticker[ticker]
            self.assertEqual(row["side"], side)
            self.assertEqual(row["status"], status)
            self.assertAlmostEqual(row["size_frac"], float(size_frac))
            self.assertAlmostEqual(row["fill_px"], float(price))
            self.assertAlmostEqual(row["cost"], float(cost))
            self.assertEqual(row["filled_at"], filled_at)
            self.assertAlmostEqual(row["qty"], allocation * float(size_frac) / float(price))
            self.assertEqual(row["mark_kind"], "fixture_mark")
            self.assertEqual(row["mark_source"], "fixture")
            self.assertAlmostEqual(row["fill_px"], book["marks"][ticker]["entry_px"])

    def test_inspect_fixture_mtm_is_labeled_plumbing_not_alpha(self):
        report = inspect_ledger(self.ledger, fixtures=FIXTURES)
        self.assertIn("not alpha", report["note"].lower())
        self.assertIn("fixture-mark", report["note"].lower())
        mtm = report["mtm"]
        self.assertEqual(mtm["kind"], "fixture-mark")
        self.assertIn("not alpha", mtm["note"].lower())
        self.assertIn("fixture", mtm["note"].lower())
        self.assertFalse(mtm.get("alpha", False))
        book = load_mark_book()
        expected = 0.0
        for row in report["orders"]:
            mark = book["marks"][row["symbol"]]
            expected += row["qty"] * (mark["exit_px"] - row["fill_px"])
        self.assertAlmostEqual(mtm["total_pnl"], expected)
        self.assertAlmostEqual(mtm["ending_equity"], report["allocation"] + expected)
        self.assertGreaterEqual(mtm["n_winners"] + mtm["n_losers"], 1)

    def test_inspect_without_fixtures_omits_mtm_and_mark_labels(self):
        report = inspect_ledger(self.ledger)
        self.assertIsNone(report.get("mtm"))
        self.assertGreater(report["n_fills"], 0)
        for row in report["orders"]:
            self.assertIsNone(row.get("mark_kind"))
            self.assertIsNone(row.get("mark_source"))

    def test_inspect_does_not_mutate_ledger(self):
        before = _digest_path(self.ledger)
        tables_before = _order_rows(self.ledger)
        report = inspect_ledger(self.ledger, fixtures=FIXTURES)
        self.assertTrue(report["read_only"])
        self.assertEqual(_digest_path(self.ledger), before)
        self.assertEqual(_order_rows(self.ledger), tables_before)
        self.assertFalse(self._has_account_table())

    def test_write_flag_is_refused_and_does_not_mutate(self):
        before = _digest_path(self.ledger)
        with self.assertRaisesRegex(ValueError, "read-only"):
            inspect_ledger(self.ledger, fixtures=FIXTURES, write=True)
        self.assertEqual(_digest_path(self.ledger), before)

    def test_missing_ledger_raises(self):
        missing = str(Path(self.tmp) / "missing.sqlite")
        with self.assertRaisesRegex(FileNotFoundError, "ledger"):
            inspect_ledger(missing)

    def _has_account_table(self) -> bool:
        connection = sqlite3.connect(self.ledger)
        try:
            names = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        finally:
            connection.close()
        return "account" in names or "positions" in names


class LedgerCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger, self.applied = _apply_local_ledger(self.tmp)

    def test_cli_requires_ledger(self):
        error = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(error):
            code = cli.main(["ledger"])
        self.assertEqual(code, 2)
        self.assertIn("requires --ledger", error.getvalue())

    def test_cli_missing_file_exits_2(self):
        error = io.StringIO()
        missing = str(Path(self.tmp) / "nope.sqlite")
        with redirect_stdout(io.StringIO()), redirect_stderr(error):
            code = cli.main(["ledger", "--ledger", missing])
        self.assertEqual(code, 2)
        self.assertIn("ledger", error.getvalue())

    def test_cli_prints_apply_local_book_and_optional_mtm(self):
        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.alpaca_paper.urllib.request.urlopen") as urlopen, redirect_stdout(
            printed
        ), redirect_stderr(error):
            code = cli.main(
                ["ledger", "--ledger", self.ledger, "--fixtures"]
            )
        self.assertEqual(code, 0)
        urlopen.assert_not_called()
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["mode"], "paper-ledger-inspect")
        self.assertEqual(payload["n_orders"], self.applied["n_applied"])
        self.assertEqual(payload["n_fills"], self.applied["n_applied"])
        self.assertIn("NVDA", {row["symbol"] for row in payload["orders"]})
        self.assertTrue(all(row["mark_kind"] == "fixture_mark" for row in payload["orders"]))
        self.assertEqual(payload["mtm"]["kind"], "fixture-mark")
        self.assertIn("not alpha", payload["note"].lower())
        self.assertIn("fixture-mark", error.getvalue().lower())
        self.assertIn("n_orders=", error.getvalue())

    def test_paper_ledger_alias_is_the_same_read_only_command(self):
        printed = io.StringIO()
        with redirect_stdout(printed), redirect_stderr(io.StringIO()):
            code = cli.main(["paper-ledger", "--ledger", self.ledger, "--fixtures"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["mode"], "paper-ledger-inspect")
        self.assertTrue(payload["read_only"])

    def test_cli_is_read_only_and_does_not_post(self):
        before = _digest_path(self.ledger)
        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.alpaca_paper.urllib.request.urlopen") as urlopen, mock.patch(
            "signal_sim.paper.read_env", return_value="unused"
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["ledger", "--ledger", self.ledger, "--fixtures"])
        self.assertEqual(code, 0)
        urlopen.assert_not_called()
        self.assertEqual(_digest_path(self.ledger), before)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["read_only"])
        self.assertFalse(payload["submitted"])
        self.assertEqual(payload["order_post"], "disabled")
        dumped = printed.getvalue() + error.getvalue()
        self.assertNotIn("/v2/orders", dumped)

    def test_submit_flag_is_unused_for_trading(self):
        before = _digest_path(self.ledger)
        calls = []

        def env(name):
            if name == "SIGNAL_SIM_ALPACA_PAPER_SUBMIT":
                return "1"
            return None

        printed = io.StringIO()
        error = io.StringIO()
        with mock.patch("signal_sim.runtime_env.read_env", side_effect=env), mock.patch(
            "signal_sim.paper.read_env", side_effect=env
        ), mock.patch(
            "signal_sim.alpaca_paper.urllib.request.urlopen",
            side_effect=lambda *args, **kwargs: calls.append(args) or (_ for _ in ()).throw(
                AssertionError("inspect must not open HTTP")
            ),
        ), redirect_stdout(printed), redirect_stderr(error):
            code = cli.main(["ledger", "--ledger", self.ledger, "--fixtures"])
        self.assertEqual(code, 0)
        self.assertEqual(calls, [])
        self.assertEqual(_digest_path(self.ledger), before)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["submit_flag"], "1")
        self.assertFalse(payload["submitted"])
        self.assertTrue(payload["read_only"])
        self.assertIn("unused", error.getvalue().lower())

    def test_cli_write_flag_exits_2_without_mutating(self):
        before = _digest_path(self.ledger)
        error = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(error):
            code = cli.main(
                ["ledger", "--ledger", self.ledger, "--fixtures", "--write"]
            )
        self.assertEqual(code, 2)
        self.assertIn("read-only", error.getvalue())
        self.assertEqual(_digest_path(self.ledger), before)

    def test_cli_does_not_require_paper_keys(self):
        printed = io.StringIO()
        with mock.patch("signal_sim.paper.read_env", return_value=None), redirect_stdout(
            printed
        ), redirect_stderr(io.StringIO()):
            code = cli.main(["ledger", "--ledger", self.ledger])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["n_orders"], self.applied["n_applied"])
        self.assertIsNone(payload.get("mtm"))


if __name__ == "__main__":
    unittest.main()
