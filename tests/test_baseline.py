"""Conviction vs equal-weight walk-forward. Fixture marks only. Not alpha."""

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from signal_sim import cli
from signal_sim.baseline import (
    DEFAULT_SERIES,
    compare_walkforward,
    load_baseline_series,
    mark_book_equity,
    run_baseline_compare,
    write_baseline_compare,
)
from signal_sim.conviction import equal_weight_targets


REPO = Path(__file__).resolve().parent.parent
EQUAL_WEIGHT_ARTIFACT = REPO / "docs" / "research" / "2026-09-04-equal-weight.json"
CONVICTION_ARTIFACT = REPO / "docs" / "research" / "2026-09-04.json"


class EqualWeightSizerTests(unittest.TestCase):
    def test_top_k_share_gross_evenly_under_name_cap(self):
        rows = [
            {"ticker": "NVDA", "score": 12.0},
            {"ticker": "XLE", "score": 6.0},
            {"ticker": "MSFT", "score": 3.0},
        ]
        targets, skipped = equal_weight_targets(
            rows, horizon_hours=24.0, max_name_frac=1.0, max_gross_invest=0.8
        )
        self.assertEqual(skipped, [])
        fracs = [row["target_frac"] for row in targets]
        self.assertEqual(len(set(round(frac, 12) for frac in fracs)), 1)
        self.assertAlmostEqual(sum(fracs), 0.8)
        capped, _ = equal_weight_targets(rows, horizon_hours=24.0)
        self.assertTrue(all(row["target_frac"] <= 0.2 + 1e-12 for row in capped))


class BaselineCompareTests(unittest.TestCase):
    def test_fixture_series_conviction_differs_from_equal_weight(self):
        report = run_baseline_compare(root=REPO, fixtures=REPO / "fixtures")
        self.assertEqual(report["mode"], "baseline-compare")
        self.assertTrue(report["not_alpha"])
        self.assertTrue(report["not_fitted"])
        self.assertTrue(report["paper_only"])
        self.assertEqual(report["source"], "fixtures")
        self.assertGreaterEqual(report["n_steps"], 2)
        self.assertTrue(report["live_history_thin"])
        self.assertIn("accumulate", report["live_history_note"].lower())
        conv = report["equity_delta_conviction"]
        eqw = report["equity_delta_equal"]
        self.assertIsInstance(conv, float)
        self.assertIsInstance(eqw, float)
        self.assertNotAlmostEqual(conv, eqw)
        self.assertEqual(
            report["delta_conviction_minus_equal"],
            report["conviction"]["ending_equity"] - report["equal_weight"]["ending_equity"],
        )
        self.assertEqual(len(report["conviction"]["equity_curve"]), report["n_steps"])
        dumped = json.dumps(report)
        self.assertNotIn("headline", dumped.lower())

    def test_later_step_marks_do_not_change_earlier_equity(self):
        steps = load_baseline_series(DEFAULT_SERIES)
        first = compare_walkforward(steps)
        mutated = copy.deepcopy(steps)
        for ticker, mark in mutated[-1]["marks"].items():
            mark["exit_px"] = mark["exit_px"] * 4
            mutated[-1]["marks"][ticker] = mark
        second = compare_walkforward(mutated)
        self.assertAlmostEqual(
            first["conviction"]["equity_curve"][0]["ending_equity"],
            second["conviction"]["equity_curve"][0]["ending_equity"],
        )
        self.assertNotAlmostEqual(
            first["conviction"]["ending_equity"],
            second["conviction"]["ending_equity"],
        )

    def test_embargo_violation_fails_closed(self):
        raw = json.loads(DEFAULT_SERIES.read_text(encoding="utf-8"))
        raw["steps"][1]["decision_at"] = "2026-09-02T18:30:00Z"
        tmp = Path(tempfile.mkdtemp()) / "series.json"
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "embargo"):
            load_baseline_series(tmp)

    def test_future_print_cannot_be_a_mark_kind_other_than_fixture(self):
        book = [{"ticker": "NVDA", "target_frac": 0.2}]
        marked = mark_book_equity(
            book,
            {"NVDA": {"entry_px": 100.0, "exit_px": 110.0}},
            starting_equity=100000.0,
        )
        self.assertAlmostEqual(marked["total_pnl"], 2000.0)
        skipped = mark_book_equity(book, {}, starting_equity=100000.0)
        self.assertEqual(skipped["skipped"][0]["reason"], "no_mark")
        self.assertAlmostEqual(skipped["ending_equity"], 100000.0)

    def test_equal_weight_freeze_is_a_usable_one_day_book(self):
        raw = json.loads(EQUAL_WEIGHT_ARTIFACT.read_text(encoding="utf-8"))
        conv = json.loads(CONVICTION_ARTIFACT.read_text(encoding="utf-8"))
        self.assertNotEqual(
            [row["ticker"] for row in raw["proposed_book"]["targets"]],
            [row["ticker"] for row in conv["proposed_book"]["targets"]],
        )
        steps = load_baseline_series(DEFAULT_SERIES)
        self.assertGreaterEqual(len(steps), 2)
        self.assertLess(steps[0]["decision_at"], steps[1]["decision_at"])

    def test_cli_requires_fixtures_and_writes(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = cli.main(["baseline-compare"])
        self.assertEqual(code, 2)
        self.assertIn("requires --fixtures", error.getvalue())

        tmp = Path(tempfile.mkdtemp()) / "baseline.json"
        printed = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(printed), redirect_stderr(err):
            code = cli.main(["baseline-compare", "--fixtures", "--out", str(tmp)])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertTrue(payload["not_fitted"])
        self.assertTrue(tmp.is_file())
        written = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertEqual(written["mode"], "baseline-compare")
        write_baseline_compare(payload, tmp)
        self.assertTrue(tmp.is_file())


class BaselineLookaheadTests(unittest.TestCase):
    def test_steps_must_expand(self):
        raw = json.loads(DEFAULT_SERIES.read_text(encoding="utf-8"))
        raw["steps"][1]["decision_at"] = raw["steps"][0]["decision_at"]
        raw["steps"][1]["exit_at"] = "2026-09-04T21:00:00Z"
        tmp = Path(tempfile.mkdtemp()) / "series.json"
        tmp.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "expand"):
            load_baseline_series(tmp)


if __name__ == "__main__":
    unittest.main()
