"""Replay fixture ranks through the paper-order choke at a caller-supplied mark.

This is a harness check, not a strategy or a market backtest. The fill price is
an explicit constant so the test cannot be read as a PnL result.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from signal_sim.cli import load_fixture_events
from signal_sim.indicators import rank_candidates
from signal_sim.paper import submit_paper_order


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
SUPPLIED_MARK = 100.0


class FixtureReplayTests(unittest.TestCase):
    def test_top_fixture_candidate_can_fill_at_supplied_mark(self):
        events = load_fixture_events(FIXTURES)
        candidates = rank_candidates(events)
        self.assertTrue(candidates)
        top = candidates[0]
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
            mark_px=SUPPLIED_MARK,
            kill_root=tmp,
        )

        self.assertEqual(result["status"], "filled")
        self.assertEqual(result["ticker"], top["ticker"])
        self.assertEqual(result["fill_px"], SUPPLIED_MARK)


if __name__ == "__main__":
    unittest.main()
