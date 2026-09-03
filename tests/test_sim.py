"""Round-trip: fixture rank → submit_paper_order → fill → fixture mark-to-market PnL."""

import io
import json
import math
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from signal_sim import cli
from signal_sim.indicators import load_universe
from signal_sim.sim import MAX_GROSS_FRAC, load_mark_book, run_fixture_replay
from signal_sim.sizer import size_targets


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"


def _expected_buy_pnl(starting_cash, size_frac, fill_px, exit_px):
    return starting_cash * size_frac / fill_px * (exit_px - fill_px)


class MarkBookTests(unittest.TestCase):
    def test_checked_in_book_covers_universe_and_exit_after_decision(self):
        book = load_mark_book()
        self.assertEqual(book["source"], "fixture")
        self.assertGreater(book["exit_at"], book["decision_at"])
        self.assertLessEqual(book["size_frac"], MAX_GROSS_FRAC)
        self.assertEqual(set(book["marks"]), {"NVDA", "XLE", "DIS"})


class ReplayRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = os.path.join(self.tmp, "ledger.sqlite")
        self.audit = os.path.join(self.tmp, "audit.jsonl")

    def test_signal_order_fill_pnl_round_trip(self):
        book = load_mark_book()
        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
        )

        self.assertEqual(summary["mode"], "local-paper-replay")
        self.assertEqual(summary["mark_source"], "fixture")
        self.assertEqual([row["ticker"] for row in summary["candidates"]], ["NVDA", "XLE"])
        self.assertEqual([row["ticker"] for row in summary["orders"]], ["NVDA", "XLE"])
        self.assertEqual(summary["refusals"], [])
        self.assertEqual(len(summary["positions"]), 2)

        expected = {
            ticker: _expected_buy_pnl(
                book["starting_cash"],
                book["size_frac"],
                book["marks"][ticker]["entry_px"],
                book["marks"][ticker]["exit_px"],
            )
            for ticker in ("NVDA", "XLE")
        }
        by_ticker = {row["ticker"]: row for row in summary["positions"]}
        self.assertAlmostEqual(by_ticker["NVDA"]["fill_px"], 178.5)
        self.assertAlmostEqual(by_ticker["NVDA"]["exit_px"], 180.0)
        self.assertAlmostEqual(by_ticker["NVDA"]["pnl"], expected["NVDA"])
        self.assertAlmostEqual(by_ticker["XLE"]["fill_px"], 90.0)
        self.assertAlmostEqual(by_ticker["XLE"]["exit_px"], 88.5)
        self.assertAlmostEqual(by_ticker["XLE"]["pnl"], expected["XLE"])
        self.assertAlmostEqual(summary["total_pnl"], expected["NVDA"] + expected["XLE"])
        self.assertAlmostEqual(
            summary["ending_equity"],
            book["starting_cash"] + summary["total_pnl"],
        )
        self.assertTrue(math.isfinite(summary["total_pnl"]))

        connection = sqlite3.connect(self.ledger)
        try:
            orders = connection.execute("SELECT ticker, status FROM orders ORDER BY ticker").fetchall()
            fills = connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
            account = connection.execute("SELECT starting_cash, total_pnl FROM account").fetchone()
            positions = connection.execute("SELECT ticker FROM positions ORDER BY ticker").fetchall()
        finally:
            connection.close()
        self.assertEqual(orders, [("NVDA", "filled"), ("XLE", "filled")])
        self.assertEqual(fills, 2)
        self.assertEqual(account[0], book["starting_cash"])
        self.assertAlmostEqual(account[1], summary["total_pnl"])
        self.assertEqual([row[0] for row in positions], ["NVDA", "XLE"])

        audit_lines = [
            json.loads(line)
            for line in Path(self.audit).read_text(encoding="utf-8").splitlines()
            if line
        ]
        self.assertEqual([line["outcome"] for line in audit_lines], ["filled", "filled"])

    def test_missing_mark_is_refused_not_invented(self):
        book = load_mark_book()
        book = dict(book)
        book["marks"] = dict(book["marks"])
        del book["marks"]["XLE"]
        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            mark_book=book,
        )
        self.assertEqual([row["ticker"] for row in summary["orders"]], ["NVDA"])
        self.assertEqual(summary["refusals"], [{"ticker": "XLE", "reason": "missing_fixture_mark"}])

    def test_gross_frac_cap_skips_later_candidates(self):
        book = load_mark_book()
        book = dict(book)
        book["size_frac"] = 0.6
        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            mark_book=book,
        )
        self.assertEqual([row["ticker"] for row in summary["orders"]], ["NVDA"])
        self.assertEqual(summary["refusals"], [{"ticker": "XLE", "reason": "gross_frac_cap"}])
        self.assertAlmostEqual(summary["gross_frac"], 0.6)

    def test_kill_file_refuses_every_order(self):
        Path(self.tmp, "KILL").write_text("stop", encoding="utf-8")
        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
        )
        self.assertEqual(summary["orders"], [])
        self.assertEqual(summary["positions"], [])
        self.assertEqual(summary["total_pnl"], 0.0)
        self.assertTrue(summary["refusals"])
        self.assertTrue(all("R3" in row["reason"] for row in summary["refusals"]))

    def test_decision_before_last_observation_is_rejected(self):
        book = load_mark_book()
        book = dict(book)
        book["decision_at"] = book["decision_at"].replace(year=2020)
        with self.assertRaisesRegex(ValueError, "decision_at must not precede"):
            run_fixture_replay(
                fixtures=FIXTURES,
                ledger_path=self.ledger,
                audit_path=self.audit,
                kill_root=self.tmp,
                mark_book=book,
            )

    def test_second_replay_at_target_places_no_new_orders(self):
        first = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
        )
        second = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
        )
        self.assertEqual([row["ticker"] for row in first["orders"]], ["NVDA", "XLE"])
        self.assertEqual(second["orders"], [])
        self.assertEqual({row["ticker"] for row in second["positions"]}, {"NVDA", "XLE"})

    def test_name_leaving_rank_is_closed(self):
        run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
        )
        closed = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            candidates=[{"ticker": "NVDA", "score": 2, "news_breakout": 1, "insider_confirm": 1}],
        )
        self.assertEqual([row["ticker"] for row in closed["orders"]], ["XLE"])
        self.assertEqual(closed["orders"][0]["side"], "sell")
        self.assertEqual([row["ticker"] for row in closed["positions"]], ["NVDA"])

    def test_cash_constraint_skips_unaffordable_open(self):
        book = load_mark_book()
        book = dict(book)
        book["starting_cash"] = 1000.0
        book["size_frac"] = 0.6
        book["max_gross_frac"] = 2.0
        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            mark_book=book,
        )
        self.assertEqual([row["ticker"] for row in summary["orders"]], ["NVDA"])
        self.assertEqual(summary["refusals"], [{"ticker": "XLE", "reason": "cash_constraint"}])

    def test_drawdown_halt_blocks_new_buys(self):
        book = load_mark_book()
        book = dict(book)
        book["max_drawdown"] = 0.01
        book["marks"] = dict(book["marks"])
        book["marks"]["NVDA"] = {"entry_px": 100.0, "exit_px": 50.0}
        first = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            mark_book=book,
            candidates=[{"ticker": "NVDA", "score": 1, "news_breakout": 1, "insider_confirm": 0}],
        )
        self.assertLess(first["total_pnl"], -0.01 * book["starting_cash"])
        second = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            mark_book=book,
            candidates=[
                {"ticker": "NVDA", "score": 1, "news_breakout": 1, "insider_confirm": 0},
                {"ticker": "XLE", "score": 1, "news_breakout": 1, "insider_confirm": 0},
            ],
        )
        self.assertTrue(second["drawdown_halt"])
        self.assertEqual(second["orders"], [])
        self.assertIn({"ticker": "XLE", "reason": "drawdown_halt"}, second["refusals"])

    def test_replay_accepts_a_larger_frozen_universe(self):
        universe = load_universe()
        self.assertGreater(len(universe), 3)
        self.assertTrue({"NVDA", "XLE", "DIS", "SPY", "XOM"}.issubset(set(universe)))
        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            universe=universe,
        )
        self.assertEqual({row["ticker"] for row in summary["orders"]}, {"NVDA", "XLE"})


class SizerTests(unittest.TestCase):
    def test_emits_signed_long_targets_and_horizon(self):
        targets, skipped = size_targets(
            [
                {"ticker": "NVDA", "score": 2},
                {"ticker": "XLE", "score": 1},
                {"ticker": "SPY", "score": 1},
            ],
            size_frac=0.1,
            horizon_hours=34.75,
        )
        self.assertEqual(skipped, [])
        self.assertEqual([row["ticker"] for row in targets], ["NVDA", "XLE", "SPY"])
        self.assertTrue(all(row["target_frac"] == 0.1 for row in targets))
        self.assertTrue(all(row["side"] == "buy" for row in targets))
        self.assertTrue(all(row["horizon_hours"] == 34.75 for row in targets))

    def test_gross_cap_skips_without_a_three_name_ceiling(self):
        names = [{"ticker": name, "score": 1} for name in ("NVDA", "XLE", "DIS", "SPY", "QQQ")]
        targets, skipped = size_targets(names, size_frac=0.3, horizon_hours=24.0, max_gross_frac=1.0)
        self.assertEqual(len(targets), 3)
        self.assertEqual(len(skipped), 2)
        self.assertEqual([row["reason"] for row in skipped], ["gross_frac_cap", "gross_frac_cap"])


class ReplayCliTests(unittest.TestCase):
    def test_replay_requires_fixtures_flag(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = cli.main(["replay"])
        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("requires --fixtures", error.getvalue())

    def test_replay_fixtures_prints_round_trip_summary(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = os.path.join(tmp, "cli-ledger.sqlite")
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(["replay", "--fixtures", "--ledger", ledger])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "local-paper-replay")
        self.assertIn("total_pnl", payload)
        self.assertEqual({row["ticker"] for row in payload["orders"]}, {"NVDA", "XLE"})
        rendered = json.dumps(payload).lower()
        for host in ("api." + "alpaca.markets", "local" + "host"):
            self.assertNotIn(host, rendered)


if __name__ == "__main__":
    unittest.main()
