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
from signal_sim.indicators import load_sectors, load_universe
from signal_sim.sim import MAX_GROSS_FRAC, _inventory, load_mark_book, load_mark_path, run_fixture_path, run_fixture_replay
from signal_sim.sizer import size_targets
from signal_sim.paper import submit_paper_order


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
        self.assertEqual(set(book["marks"]), {"NVDA", "XLE"})
        self.assertNotIn("AAPL", book["marks"])
        liquid = load_mark_book(FIXTURES / "marks" / "liquid.json")
        self.assertEqual(
            set(liquid["marks"]),
            {"NVDA", "MSFT", "XLE", "XOM", "DIS", "NFLX", "SPY", "QQQ"},
        )
        self.assertTrue(all(row["kind"] == "fixture_mark" for row in liquid["marks"].values()))
        self.assertTrue(all(row["source"] == "fixture" for row in liquid["marks"].values()))
        self.assertNotEqual(liquid["marks"]["DIS"]["entry_px"], 100.0)
        self.assertNotEqual(liquid["marks"]["SPY"]["entry_px"], 100.0)
        self.assertNotIn(100.0, [row["entry_px"] for row in liquid["marks"].values()])
        sectors = load_sectors()
        for names in sectors.values():
            self.assertTrue(set(names) & set(liquid["marks"]))
        self.assertTrue(set(load_universe()) - set(liquid["marks"]))
        self.assertGreaterEqual(book["cost_bps"], 0)
        self.assertGreater(book["decision_delay_hours"], 0)
        self.assertLess(book["decision_at"], book["fill_at"])
        self.assertLess(book["fill_at"], book["exit_at"])

    def test_vendor_tagged_mark_is_rejected(self):
        from signal_sim.sim import _parse_mark_book

        raw = json.loads((FIXTURES / "marks" / "universe.json").read_text(encoding="utf-8"))
        raw["marks"]["NVDA"]["source"] = "yahoo"
        with self.assertRaisesRegex(ValueError, "fixture_mark"):
            _parse_mark_book(raw, FIXTURES / "marks" / "universe.json")


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
        self.assertIn("NVDA", [row["ticker"] for row in summary["candidates"]])
        self.assertEqual({row["ticker"] for row in summary["orders"]}, {"NVDA", "XLE"})
        self.assertIn({"ticker": "DIS", "reason": "no_mark"}, summary["refusals"])
        self.assertIn({"ticker": "SPY", "reason": "no_mark"}, summary["refusals"])
        self.assertIn({"ticker": "MSFT", "reason": "no_mark"}, summary["refusals"])
        self.assertIn({"ticker": "XOM", "reason": "no_mark"}, summary["refusals"])
        self.assertIn({"ticker": "NFLX", "reason": "no_mark"}, summary["refusals"])
        self.assertIn({"ticker": "QQQ", "reason": "no_mark"}, summary["refusals"])
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
        self.assertAlmostEqual(
            summary["ending_equity"],
            summary["cash"] + math.fsum(row["shares"] * row["exit_px"] for row in summary["positions"]),
        )
        self.assertTrue(math.isfinite(summary["total_pnl"]))
        self.assertTrue(math.isfinite(summary["hawkes_log_likelihood"]))
        self.assertEqual(summary["stats"]["n_orders"], 2)
        self.assertEqual(summary["stats"]["n_positions"], 2)
        self.assertGreaterEqual(summary["stats"]["n_refusals"], 2)
        self.assertEqual(summary["stats"]["n_winners"], 1)
        self.assertEqual(summary["stats"]["n_losers"], 1)
        self.assertAlmostEqual(summary["stats"]["hit_rate"], 0.5)
        self.assertAlmostEqual(summary["stats"]["turnover"], 0.2)
        self.assertAlmostEqual(summary["stats"]["max_name_frac"], book["size_frac"])
        self.assertEqual(summary["stats"]["hawkes_n_arrivals"], 1)
        self.assertEqual(summary["fill_rule"], "decision-time fixture mark; size_frac of starting_cash")
        self.assertLess(summary["decision_at"], summary["fill_at"])
        self.assertTrue(all(row["filled_at"] == summary["fill_at"] for row in summary["orders"]))
        self.assertEqual(summary["stats"]["cost_bps"], 0.0)
        self.assertGreaterEqual(summary["stats"]["n_clusters"], 1)

        connection = sqlite3.connect(self.ledger)
        try:
            orders = connection.execute("SELECT ticker, status FROM orders ORDER BY ticker").fetchall()
            fill_times = connection.execute("SELECT filled_at FROM fills").fetchall()
            fills = connection.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
            account = connection.execute("SELECT starting_cash, total_pnl FROM account").fetchone()
            positions = connection.execute("SELECT ticker FROM positions ORDER BY ticker").fetchall()
            history = connection.execute(
                "SELECT step, ending_equity, total_pnl, fill_at FROM account_history ORDER BY step"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(orders, [("NVDA", "filled"), ("XLE", "filled")])
        self.assertEqual({row[0] for row in fill_times}, {summary["fill_at"]})
        self.assertEqual(fills, 2)
        self.assertEqual(account[0], book["starting_cash"])
        self.assertAlmostEqual(account[1], summary["total_pnl"])
        self.assertEqual([row[0] for row in positions], ["NVDA", "XLE"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0][0], 1)
        self.assertAlmostEqual(history[0][1], summary["ending_equity"])
        self.assertAlmostEqual(history[0][2], summary["total_pnl"])
        self.assertEqual(history[0][3], summary["fill_at"])

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
        self.assertIn({"ticker": "XLE", "reason": "no_mark"}, summary["refusals"])

    def test_held_name_without_mark_fails_closed(self):
        run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
        )
        book = load_mark_book()
        book = dict(book)
        book["marks"] = dict(book["marks"])
        del book["marks"]["NVDA"]
        with self.assertRaisesRegex(ValueError, "held ticker missing fixture mark: 'NVDA'"):
            run_fixture_replay(
                fixtures=FIXTURES,
                ledger_path=self.ledger,
                audit_path=self.audit,
                kill_root=self.tmp,
                mark_book=book,
                candidates=[{"ticker": "XLE", "score": 1, "news_breakout": 1, "insider_confirm": 0}],
            )

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
            candidates=[
                {"ticker": "NVDA", "score": 2, "news_breakout": 1, "insider_confirm": 1},
                {"ticker": "XLE", "score": 1, "news_breakout": 1, "insider_confirm": 0},
            ],
        )
        self.assertEqual([row["ticker"] for row in summary["orders"]], ["NVDA"])
        self.assertTrue(all(row["reason"] == "gross_frac_cap" for row in summary["refusals"]))
        self.assertIn("XLE", [row["ticker"] for row in summary["refusals"]])
        self.assertAlmostEqual(summary["gross_frac"], 0.6)

    def test_kill_file_refuses_every_order(self):
        Path(self.tmp, "KILL").write_text("stop", encoding="utf-8")
        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            candidates=[
                {"ticker": "NVDA", "score": 1, "news_breakout": 1, "insider_confirm": 0},
                {"ticker": "XLE", "score": 1, "news_breakout": 1, "insider_confirm": 0},
            ],
        )
        self.assertEqual(summary["orders"], [])
        self.assertEqual(summary["positions"], [])
        self.assertEqual(summary["total_pnl"], 0.0)
        self.assertTrue(summary["refusals"])
        self.assertTrue(all("R3" in row["reason"] for row in summary["refusals"]))

    def test_prints_after_decision_are_not_used(self):
        book = load_mark_book()
        book = dict(book)
        book["decision_at"] = book["decision_at"].replace(year=2020)
        empty = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=os.path.join(self.tmp, "early.sqlite"),
            audit_path=os.path.join(self.tmp, "early.audit"),
            kill_root=self.tmp,
            mark_book=book,
        )
        self.assertEqual(empty["orders"], [])
        self.assertEqual(empty["candidates"], [])

        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
        )
        connection = sqlite3.connect(self.ledger)
        try:
            raw_ids = connection.execute(
                "SELECT event_ids FROM orders WHERE ticker = 'NVDA'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertNotIn("fx-nvda-late", json.loads(raw_ids))
        self.assertNotIn("fx-nvda-trade-date", json.loads(raw_ids))
        self.assertEqual(summary["stats"]["hawkes_n_arrivals"], 1)

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
        self.assertEqual({row["ticker"] for row in first["orders"]}, {"NVDA", "XLE"})
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
            candidates=[
                {"ticker": "NVDA", "score": 1, "news_breakout": 1, "insider_confirm": 0},
                {"ticker": "XLE", "score": 1, "news_breakout": 1, "insider_confirm": 0},
            ],
        )
        self.assertEqual([row["ticker"] for row in summary["orders"]], ["NVDA"])
        self.assertTrue(all(row["reason"] == "cash_constraint" for row in summary["refusals"]))
        self.assertIn("XLE", [row["ticker"] for row in summary["refusals"]])

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

    def test_name_without_mark_is_skipped_not_filled(self):
        book = load_mark_book()
        book = dict(book)
        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            mark_book=book,
            candidates=[{"ticker": "AAPL", "score": 1, "news_breakout": 1, "insider_confirm": 0}],
        )
        self.assertEqual(summary["orders"], [])
        self.assertEqual(summary["refusals"], [{"ticker": "AAPL", "reason": "no_mark"}])

    def test_every_universe_name_has_mark_or_no_mark_skip(self):
        universe = load_universe()
        book = load_mark_book()
        marked = {
            ticker
            for ticker, row in book["marks"].items()
            if not row.get("unused")
        }
        self.assertTrue(marked)
        self.assertTrue(set(universe) - marked)
        for ticker in universe:
            ledger = os.path.join(self.tmp, f"{ticker}.sqlite")
            audit = os.path.join(self.tmp, f"{ticker}.audit")
            summary = run_fixture_replay(
                fixtures=FIXTURES,
                ledger_path=ledger,
                audit_path=audit,
                kill_root=self.tmp,
                candidates=[{"ticker": ticker, "score": 1, "news_breakout": 1, "insider_confirm": 0}],
            )
            if ticker in marked:
                self.assertEqual([row["ticker"] for row in summary["orders"]], [ticker], ticker)
                self.assertNotEqual(summary["orders"][0]["fill_px"], 100.0, ticker)
                self.assertEqual(summary["refusals"], [], ticker)
            else:
                self.assertEqual(summary["orders"], [], ticker)
                self.assertEqual(summary["refusals"], [{"ticker": ticker, "reason": "no_mark"}], ticker)

    def test_liquid_marks_fill_each_sector(self):
        summary = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            mark_book_path=FIXTURES / "marks" / "liquid.json",
        )
        filled = {row["ticker"] for row in summary["orders"]}
        self.assertEqual(filled, {"NVDA", "MSFT", "XLE", "XOM", "DIS", "NFLX", "SPY", "QQQ"})
        self.assertEqual(summary["refusals"], [])
        self.assertNotIn(100.0, [row["fill_px"] for row in summary["orders"]])
        sectors = load_sectors()
        for names in sectors.values():
            self.assertTrue(filled & set(names))

    def test_cost_bps_reduces_ending_equity_by_declared_fees(self):
        book = load_mark_book()
        book = dict(book)
        book["cost_bps"] = 10.0
        zero = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=os.path.join(self.tmp, "zero.sqlite"),
            audit_path=os.path.join(self.tmp, "zero.audit"),
            kill_root=self.tmp,
        )
        taxed = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=self.ledger,
            audit_path=self.audit,
            kill_root=self.tmp,
            mark_book=book,
        )
        expected_fees = 2 * book["starting_cash"] * book["size_frac"] * 10.0 / 10000.0
        self.assertAlmostEqual(taxed["stats"]["fees"], expected_fees)
        self.assertAlmostEqual(taxed["ending_equity"], zero["ending_equity"] - expected_fees)
        self.assertLess(taxed["cash"], zero["cash"])


class InventoryCostTests(unittest.TestCase):
    def test_two_buys_use_volume_weighted_average_cost(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = os.path.join(tmp, "ledger.sqlite")
        audit = os.path.join(tmp, "audit.jsonl")
        starting = 1000.0
        for key, px, frac in (("a", 100.0, 0.1), ("b", 200.0, 0.1)):
            submit_paper_order(
                {
                    "ticker": "NVDA",
                    "side": "buy",
                    "size_frac": frac,
                    "event_ids": [f"e-{key}"],
                    "idempotency_key": key,
                },
                ledger_path=ledger,
                audit_path=audit,
                mark_px=px,
                kill_root=tmp,
            )
        held, cash, _pnl = _inventory(ledger, starting)
        self.assertAlmostEqual(held["NVDA"]["shares"], 1.0 + 0.5)
        self.assertAlmostEqual(held["NVDA"]["fill_px"], (100.0 * 1.0 + 200.0 * 0.5) / 1.5)
        self.assertAlmostEqual(held["NVDA"]["size_frac"], 0.2)
        self.assertAlmostEqual(cash, 800.0)

    def test_vwap_close_sells_held_shares_not_summed_size_frac(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = os.path.join(tmp, "ledger.sqlite")
        audit = os.path.join(tmp, "audit.jsonl")
        starting = 1000.0
        for key, px, frac, when in (
            ("a", 100.0, 0.1, "2026-09-02T10:00:00Z"),
            ("b", 200.0, 0.1, "2026-09-02T10:30:00Z"),
        ):
            submit_paper_order(
                {
                    "ticker": "NVDA",
                    "side": "buy",
                    "size_frac": frac,
                    "event_ids": [f"e-{key}"],
                    "idempotency_key": key,
                },
                ledger_path=ledger,
                audit_path=audit,
                mark_px=px,
                kill_root=tmp,
                filled_at=when,
            )
        book = load_mark_book()
        book = dict(book)
        book["starting_cash"] = starting
        book["marks"] = dict(book["marks"])
        book["marks"]["NVDA"] = {"entry_px": 150.0, "exit_px": 150.0}
        closed = run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=ledger,
            audit_path=audit,
            kill_root=tmp,
            mark_book=book,
            candidates=[],
        )
        self.assertEqual([row["ticker"] for row in closed["orders"]], ["NVDA"])
        self.assertEqual(closed["orders"][0]["side"], "sell")
        self.assertAlmostEqual(closed["orders"][0]["size_frac"], 1.5 * 150.0 / starting)
        self.assertEqual(closed["positions"], [])
        realized = 1.5 * (150.0 - (100.0 * 1.0 + 200.0 * 0.5) / 1.5)
        held, cash, _pnl = _inventory(ledger, starting)
        self.assertEqual(held, {})
        self.assertAlmostEqual(cash, starting + realized)
        self.assertAlmostEqual(closed["cash"], starting + realized)
        self.assertAlmostEqual(closed["ending_equity"], starting + realized)
        self.assertAlmostEqual(closed["total_pnl"], realized)
        self.assertAlmostEqual(closed["stats"]["realized_pnl"], realized)
        self.assertAlmostEqual(closed["stats"]["unrealized_pnl"], 0.0)


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

    def test_max_name_frac_skips_without_a_three_name_ceiling(self):
        names = [{"ticker": name, "score": 1} for name in ("NVDA", "XLE", "DIS", "SPY", "QQQ")]
        targets, skipped = size_targets(
            names, size_frac=0.6, horizon_hours=24.0, max_gross_frac=1.0, max_name_frac=0.5
        )
        self.assertEqual(targets, [])
        self.assertEqual(len(skipped), 5)
        self.assertTrue(all(row["reason"] == "max_name_frac" for row in skipped))


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
        self.assertEqual(payload["stats"]["n_winners"], 1)
        self.assertEqual(payload["stats"]["n_losers"], 1)
        self.assertAlmostEqual(payload["stats"]["hit_rate"], 0.5)
        self.assertAlmostEqual(payload["stats"]["turnover"], 0.2)
        self.assertEqual(payload["stats"]["hawkes_n_arrivals"], 1)
        self.assertEqual(payload["fill_rule"], "decision-time fixture mark; size_frac of starting_cash")
        rendered = json.dumps(payload).lower()
        for host in ("api." + "alpaca.markets", "local" + "host"):
            self.assertNotIn(host, rendered)

    def test_replay_path_prints_equity_curve(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = os.path.join(tmp, "path-ledger.sqlite")
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(["replay", "--fixtures", "--path", "--ledger", ledger])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "local-paper-path")
        self.assertEqual(len(payload["steps"]), 3)
        self.assertEqual(len(payload["equity_curve"]), 3)
        self.assertTrue(math.isfinite(payload["worst_drawdown"]))
        self.assertTrue(math.isfinite(payload["total_pnl"]))

    def test_replay_marks_liquid_fills_four_names(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = os.path.join(tmp, "liquid-ledger.sqlite")
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(
                [
                    "replay",
                    "--fixtures",
                    "--marks",
                    str(FIXTURES / "marks" / "liquid.json"),
                    "--ledger",
                    ledger,
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(
            {row["ticker"] for row in payload["orders"]},
            {"NVDA", "MSFT", "XLE", "XOM", "DIS", "NFLX", "SPY", "QQQ"},
        )
        self.assertEqual(payload["refusals"], [])
        self.assertNotIn(100.0, [row["fill_px"] for row in payload["orders"]])


class MarkPathTests(unittest.TestCase):
    def test_checked_in_path_is_three_forward_steps(self):
        books = load_mark_path()
        self.assertEqual(len(books), 3)
        self.assertLess(books[0]["decision_at"], books[0]["exit_at"])
        self.assertLessEqual(books[0]["exit_at"], books[1]["decision_at"])
        self.assertLessEqual(books[1]["exit_at"], books[2]["decision_at"])
        self.assertTrue({"NVDA", "XOM", "DIS", "QQQ", "MSFT", "NFLX", "SPY"}.issubset(set(books[0]["marks"])))

    def test_path_replay_opens_adds_and_closes_across_four_names(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = os.path.join(tmp, "ledger.sqlite")
        audit = os.path.join(tmp, "audit.jsonl")
        summary = run_fixture_path(
            fixtures=FIXTURES,
            ledger_path=ledger,
            audit_path=audit,
            kill_root=tmp,
        )
        self.assertEqual(summary["mode"], "local-paper-path")
        self.assertEqual(len(summary["steps"]), 3)
        first_tickers = {row["ticker"] for row in summary["steps"][0]["orders"]}
        self.assertEqual(first_tickers, {"NVDA", "XOM", "DIS", "QQQ"})
        self.assertIn({"ticker": "AAPL", "reason": "no_mark"}, summary["steps"][0]["refusals"])
        sectors = load_sectors()
        self.assertTrue(first_tickers & set(sectors["tech"]))
        self.assertTrue(first_tickers & set(sectors["energy"]))
        self.assertTrue(first_tickers & set(sectors["media"]))
        self.assertTrue(first_tickers & set(sectors["etf"]))
        second_sides = {(row["ticker"], row["side"]) for row in summary["steps"][1]["orders"]}
        self.assertIn(("XOM", "sell"), second_sides)
        self.assertIn(("DIS", "sell"), second_sides)
        self.assertIn(("MSFT", "buy"), second_sides)
        self.assertIn(("NFLX", "buy"), second_sides)
        self.assertIn({"ticker": "AAPL", "reason": "no_mark"}, summary["steps"][1]["refusals"])
        third_tickers = {row["ticker"] for row in summary["steps"][2]["orders"]}
        self.assertIn("NVDA", third_tickers)
        self.assertIn("SPY", third_tickers)
        self.assertEqual({row["ticker"] for row in summary["steps"][2]["positions"]}, {"MSFT", "SPY"})
        self.assertIn({"ticker": "AAPL", "reason": "no_mark"}, summary["steps"][2]["refusals"])
        self.assertLess(summary["steps"][0]["fill_at"], summary["steps"][1]["fill_at"])
        self.assertLess(summary["steps"][1]["fill_at"], summary["steps"][2]["fill_at"])
        self.assertTrue(
            all(row["filled_at"] == summary["steps"][0]["fill_at"] for row in summary["steps"][0]["orders"])
        )
        self.assertEqual(len(summary["equity_curve"]), 3)
        self.assertAlmostEqual(
            summary["ending_equity"],
            summary["starting_cash"] + summary["total_pnl"],
        )
        self.assertAlmostEqual(summary["ending_equity"], summary["equity_curve"][-1])
        last = summary["steps"][-1]
        self.assertAlmostEqual(
            last["ending_equity"],
            last["cash"] + math.fsum(row["shares"] * row["exit_px"] for row in last["positions"]),
        )
        self.assertNotIn("sharpe", json.dumps(summary).lower())
        self.assertLessEqual(summary["worst_drawdown"], 0.0)
        connection = sqlite3.connect(ledger)
        try:
            history = connection.execute(
                "SELECT step, ending_equity, total_pnl, fill_at FROM account_history ORDER BY step"
            ).fetchall()
            snapshot = connection.execute(
                "SELECT ending_equity, total_pnl FROM account"
            ).fetchall()
            held = connection.execute("SELECT ticker FROM positions ORDER BY ticker").fetchall()
        finally:
            connection.close()
        self.assertEqual([row[0] for row in history], [1, 2, 3])
        self.assertEqual(len(snapshot), 1)
        self.assertAlmostEqual(snapshot[0][0], summary["ending_equity"])
        self.assertAlmostEqual(snapshot[0][1], summary["total_pnl"])
        for row, equity, step in zip(history, summary["equity_curve"], summary["steps"], strict=True):
            self.assertAlmostEqual(row[1], equity)
            self.assertAlmostEqual(row[1], step["ending_equity"])
            self.assertAlmostEqual(row[2], step["total_pnl"])
            self.assertEqual(row[3], step["fill_at"])
        self.assertEqual([row[0] for row in held], ["MSFT", "SPY"])


if __name__ == "__main__":
    unittest.main()
