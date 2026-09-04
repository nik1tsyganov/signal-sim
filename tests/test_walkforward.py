"""Expanding fixture-mark walk-forward. Not a parameter search."""

import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from signal_sim import cli
from signal_sim.events import Event
from signal_sim.fixture_load import load_fixture_events
from signal_sim.walkforward import (
    assert_no_future_prints,
    fold_events,
    load_walkforward_folds,
    run_fixture_walkforward,
)


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
UTC = timezone.utc


def event(event_id, observed_at, **overrides):
    values = {
        "id": event_id,
        "source": "fixture",
        "kind": "news",
        "ticker": "NVDA",
        "entities": ["NVDA"],
        "headline": "fixture",
        "url": "",
        "occurred_at": observed_at,
        "filed_at": None,
        "observed_at": observed_at,
        "confidence": 1.0,
        "raw_ref": f"fixture:{event_id}",
    }
    values.update(overrides)
    return Event.from_dict(values)


class WalkForwardTests(unittest.TestCase):
    def test_later_print_leaking_into_earlier_fold_fails(self):
        decision = datetime(2026, 9, 2, 10, 15, tzinfo=UTC)
        late = event("fx-nvda-late", "2026-09-04T14:00:00Z")
        with self.assertRaisesRegex(ValueError, "later print leaked"):
            assert_no_future_prints([late], decision)

    def test_fold_events_excludes_later_prints(self):
        decision = datetime(2026, 9, 2, 10, 15, tzinfo=UTC)
        admitted = fold_events(
            [
                event("early", "2026-09-02T10:00:00Z"),
                event("late", "2026-09-02T11:00:00Z"),
            ],
            decision,
        )
        self.assertEqual([row.id for row in admitted], ["early"])

    def test_two_expanding_folds_report_separate_fixture_mark_pnl(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        summary = run_fixture_walkforward(fixtures=FIXTURES, ledger_dir=tmp)
        self.assertEqual(summary["mode"], "local-paper-walkforward")
        self.assertIn("not used to search", summary["note"].lower())
        self.assertEqual(summary["n_folds"], 2)
        first, second = summary["folds"]
        self.assertLess(first["decision_at"], second["decision_at"])
        self.assertLess(first["exit_at"], second["decision_at"])
        self.assertGreater(first["embargo_hours"], first["horizon_hours"])
        self.assertIn("fixture-mark", first["pnl_note"])
        self.assertIn("total_pnl", first)
        self.assertIn("total_pnl", second)
        self.assertNotEqual(first["total_pnl"], second["total_pnl"])
        self.assertGreater(second["n_events"], first["n_events"])
        self.assertTrue(math_isfinite(first["total_pnl"]))
        rendered = json.dumps(summary).lower()
        self.assertNotIn("sharpe", rendered)
        self.assertNotIn("best_fold", rendered)
        self.assertNotIn("yahoo", rendered)

    def test_fixture_late_prints_do_not_enter_fold_one(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        summary = run_fixture_walkforward(fixtures=FIXTURES, ledger_dir=tmp)
        fold1_ids = set(summary["folds"][0]["event_ids"])
        self.assertNotIn("fx-nvda-late", fold1_ids)
        self.assertNotIn("fx-nvda-trade-date", fold1_ids)
        self.assertNotIn("fx-msft-path-2", fold1_ids)
        self.assertNotIn("fx-spy-path-3", fold1_ids)
        fold2_ids = set(summary["folds"][1]["event_ids"])
        self.assertTrue({"fx-msft-path-2", "fx-nflx-path-2", "fx-spy-path-3", "fx-xle-path-3"} <= fold2_ids)
        self.assertNotIn("fx-nvda-late", fold2_ids)
        events = {event.id: event for event in load_fixture_events(FIXTURES)}
        decision = load_walkforward_folds()[0]["decision_at"]
        for event_id in fold1_ids:
            self.assertLessEqual(events[event_id].observed_at, decision, event_id)
        for event_id in summary["folds"][0]["order_event_ids"]:
            self.assertLessEqual(events[event_id].observed_at, decision, event_id)
            self.assertNotIn("late", event_id)
            self.assertNotIn("path", event_id)
        connection = sqlite3.connect(str(Path(tmp) / "fold-1.sqlite"))
        try:
            cited = [
                event_id
                for (raw,) in connection.execute("SELECT event_ids FROM orders")
                for event_id in json.loads(raw)
            ]
        finally:
            connection.close()
        self.assertTrue(cited)
        self.assertTrue(set(cited).isdisjoint({"fx-nvda-late", "fx-nvda-trade-date", "fx-msft-path-2"}))

    def test_embargo_rejects_a_fold_that_starts_too_soon(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        raw = json.loads((FIXTURES / "marks" / "walkforward.json").read_text(encoding="utf-8"))
        raw["folds"][1]["decision_at"] = "2026-09-02T18:30:00Z"
        raw["folds"][1]["exit_at"] = "2026-09-02T21:00:00Z"
        path = Path(tmp) / "too-soon.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "embargo"):
            load_walkforward_folds(path)

    def test_cli_requires_fixtures_and_prints_per_fold_pnl(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            self.assertEqual(cli.main(["walkforward"]), 2)
        self.assertIn("requires --fixtures", error.getvalue())
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        printed = io.StringIO()
        with redirect_stdout(printed):
            self.assertEqual(cli.main(["walkforward", "--fixtures"]), 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(len(payload["folds"]), 2)
        self.assertTrue(all("pnl_note" in row for row in payload["folds"]))
        self.assertNotIn("combined_pnl", payload)
        self.assertNotIn("sharpe", json.dumps(payload).lower())


def math_isfinite(value):
    return value == value and value not in {float("inf"), float("-inf")}


if __name__ == "__main__":
    unittest.main()
