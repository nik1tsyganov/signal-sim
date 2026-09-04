"""Cheap signed tone. No LLM. Sign may be negative."""

import unittest
from datetime import datetime, timezone

from signal_sim.events import Event
from signal_sim.sentiment import (
    batch_new_print_tones,
    mean_signed_news,
    signed_print,
)
from signal_sim.sells import decision_pnl_frac, select_close_reason


UTC = timezone.utc


def _news(ticker, headline, event_id, observed="2026-09-04T16:00:00Z"):
    return Event.from_dict(
        {
            "id": event_id,
            "source": "quiver",
            "kind": "news",
            "ticker": ticker,
            "entities": [ticker],
            "headline": headline,
            "url": "",
            "occurred_at": observed,
            "filed_at": None,
            "observed_at": observed,
            "confidence": 1.0,
            "raw_ref": f"quiver:{event_id}",
        }
    )


class SignedPrintTests(unittest.TestCase):
    def test_vendor_and_lexicon_can_be_negative(self):
        self.assertEqual(signed_print({"Sentiment": "Negative"}), -1.0)
        self.assertEqual(signed_print({"transaction": "sale"}), -1.0)
        self.assertEqual(signed_print({"transaction": "purchase"}), 1.0)
        self.assertLess(signed_print(_news("NVDA", "lawsuit crash probe", "n1")), 0)
        self.assertGreater(signed_print(_news("NVDA", "upgrade beat award", "n2")), 0)
        self.assertIsNone(signed_print(_news("NVDA", "Fixture headline", "n3")))

    def test_mean_signed_cuts_at_decision_and_skips_future(self):
        when = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
        events = [
            _news("NVDA", "upgrade beat", "pos"),
            _news("NVDA", "lawsuit crash", "neg", observed="2026-09-04T18:00:00Z"),
            _news("MSFT", "dump plunge", "msft"),
        ]
        tones = mean_signed_news(events, when, universe=("NVDA", "MSFT"))
        self.assertGreater(tones["NVDA"], 0)
        self.assertLess(tones["MSFT"], 0)

    def test_batch_caps_new_prints_since_last_cut(self):
        until = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
        since = datetime(2026, 9, 3, 16, 0, tzinfo=UTC)
        events = [
            _news("NVDA", "upgrade beat", "old", observed="2026-09-03T12:00:00Z"),
            _news("NVDA", "lawsuit crash", "new1"),
            _news("MSFT", "dump plunge", "new2"),
        ]
        rows = batch_new_print_tones(events, until=until, since=since, cap_n=1)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("headline", rows[0])
        self.assertIn(rows[0]["ticker"], {"NVDA", "MSFT"})


class SellSelectorTests(unittest.TestCase):
    def test_priority_and_horizon_clock(self):
        now = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
        entry = datetime(2026, 9, 3, 15, 0, tzinfo=UTC)
        reason = select_close_reason(
            in_book=True,
            score=2.0,
            min_score=1.0,
            decay_floor=0.5,
            entry_score=8.0,
            now=now,
            entry_decision_at=entry,
            horizon_hours=24.0,
            pnl_frac=-0.10,
            soft_stop=0.08,
        )
        self.assertEqual(reason, "soft_stop")
        horizon = select_close_reason(
            in_book=True,
            score=8.0,
            min_score=1.0,
            decay_floor=0.5,
            entry_score=8.0,
            now=now,
            entry_decision_at=entry,
            horizon_hours=24.0,
            pnl_frac=-0.01,
            soft_stop=0.08,
        )
        self.assertEqual(horizon, "horizon_exit")

    def test_soft_stop_mtm_from_decision_marks(self):
        self.assertAlmostEqual(
            decision_pnl_frac(entry_px=100.0, mark_px=92.0, shares=10.0),
            -0.08,
        )
        self.assertIsNone(decision_pnl_frac(entry_px=None, mark_px=92.0, shares=10.0))


if __name__ == "__main__":
    unittest.main()
