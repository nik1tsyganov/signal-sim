"""Congress/insider confirms for drift/sizer. filed_at/observed_at only."""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from signal_sim.drift import fixture_drift_book
from signal_sim.events import Event
from signal_sim.indicators import filed_confirm_features, rank_candidates
from signal_sim.safety import LookaheadError
from signal_sim.sources.altdata import QuiverSource, load_events


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
UTC = timezone.utc


def event(**overrides):
    values = {
        "id": "c-1",
        "source": "fixture",
        "kind": "congress_trade",
        "ticker": "NVDA",
        "entities": ["NVIDIA"],
        "headline": "",
        "url": "",
        "occurred_at": "2026-07-15T00:00:00Z",
        "filed_at": "2026-08-10T21:00:00Z",
        "observed_at": "2026-08-11T14:02:00Z",
        "confidence": 0.9,
        "raw_ref": "fixture:c-1",
    }
    values.update(overrides)
    return Event.from_dict(values)


class ConfirmFeatureTests(unittest.TestCase):
    def test_orders_on_observed_at_never_trade_date(self):
        when = datetime(2026, 8, 20, tzinfo=UTC)
        early_trade = event(
            id="early-trade",
            occurred_at="2026-07-15T00:00:00Z",
            filed_at="2026-08-21T21:00:00Z",
            observed_at="2026-08-22T14:00:00Z",
        )
        features = filed_confirm_features([early_trade], when)
        self.assertNotIn("NVDA", features)

    def test_fixture_nvda_has_insider_and_congress_confirms(self):
        book = fixture_drift_book(FIXTURES)
        nvda = next(row for row in book["targets"] if row["ticker"] == "NVDA")
        self.assertEqual(nvda["insider_confirm"], 1)
        self.assertEqual(nvda["congress_confirm"], 1)
        xle = next(row for row in book["targets"] if row["ticker"] == "XLE")
        self.assertEqual(xle["insider_confirm"], 0)
        self.assertEqual(xle["congress_confirm"], 0)

    def test_confirms_do_not_change_rank(self):
        from signal_sim.fixture_load import load_fixture_events
        from signal_sim.sim import load_mark_book

        events = load_fixture_events(FIXTURES)
        decision = load_mark_book()["decision_at"]
        before = rank_candidates(events, window_end=decision)
        fixture_drift_book(FIXTURES)
        after = rank_candidates(events, window_end=decision)
        self.assertEqual(before, after)

    def test_lookahead_poison_still_fails(self):
        with self.assertRaises(LookaheadError):
            load_events(str(FIXTURES / "altdata"))

    def test_quiver_live_raises_without_key(self):
        with mock.patch("signal_sim.sources.altdata.read_env", return_value=None):
            with mock.patch("signal_sim.sources.altdata.urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(NotImplementedError, r"no verified key \+ terms"):
                    QuiverSource().live()
            urlopen.assert_not_called()

    def test_confirms_do_not_change_sized_frac(self):
        from signal_sim.sizer import size_targets

        book = fixture_drift_book(FIXTURES)
        nvda = next(row for row in book["targets"] if row["ticker"] == "NVDA")
        self.assertEqual(nvda["insider_confirm"], 1)
        self.assertEqual(nvda["congress_confirm"], 1)
        stripped = dict(nvda)
        stripped.pop("insider_confirm")
        stripped.pop("congress_confirm")
        with_keys, _ = size_targets([nvda], size_frac=0.1, horizon_hours=7.75)
        without_keys, _ = size_targets([stripped], size_frac=0.1, horizon_hours=7.75)
        self.assertEqual(with_keys[0]["target_frac"], without_keys[0]["target_frac"])
        self.assertEqual(with_keys[0]["insider_confirm"], 1)
        self.assertEqual(with_keys[0]["congress_confirm"], 1)
        self.assertNotIn("insider_confirm", without_keys[0])


if __name__ == "__main__":
    unittest.main()
