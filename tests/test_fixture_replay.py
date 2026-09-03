"""Replay fixture ranks through the paper-order choke at the fixture book mark.

This is a harness check, not a strategy or a market backtest. The fill price
comes from fixtures/marks/universe.json (a research fixture, not a vendor feed).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from signal_sim.cli import load_fixture_events
from signal_sim.indicators import rank_candidates
from signal_sim.paper import submit_paper_order
from signal_sim.sim import load_mark_book


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class FixtureReplayTests(unittest.TestCase):
    def test_top_fixture_candidate_fills_at_book_entry_not_a_magic_hundred(self):
        events = load_fixture_events(FIXTURES)
        candidates = rank_candidates(events)
        self.assertTrue(candidates)
        top = candidates[0]
        book = load_mark_book()
        mark = book["marks"][top["ticker"]]
        self.assertFalse(mark.get("unused"))
        self.assertNotEqual(mark["entry_px"], 100.0)
        event_ids = [event.id for event in events if event.ticker == top["ticker"]]
        self.assertTrue(event_ids)

        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        result = submit_paper_order(
            {
                "ticker": top["ticker"],
                "side": "buy",
                "size_frac": 0.1,
                "event_ids": event_ids,
                "idempotency_key": "fixture-replay-1",
            },
            ledger_path=os.path.join(tmp, "ledger.sqlite"),
            audit_path=os.path.join(tmp, "audit.jsonl"),
            mark_px=mark["entry_px"],
            kill_root=tmp,
        )

        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["ticker"], top["ticker"])
        self.assertEqual(result["fill_px"], mark["entry_px"])
        self.assertEqual(result["fill_px"], 178.5)


if __name__ == "__main__":
    unittest.main()
