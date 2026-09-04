"""Recorded World Monitor intel features. observed_at only. Not a rank input."""

import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from signal_sim.diagnose import fixture_diagnostics
from signal_sim.drift import fixture_drift_book
from signal_sim.events import Event
from signal_sim.fixture_load import load_fixture_events
from signal_sim.indicators import intel_features, rank_candidates
from signal_sim.sim import load_mark_book
from signal_sim.sources.worldmonitor import load_recorded, live


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
UTC = timezone.utc


def event(**overrides):
    values = {
        "id": "wm-test",
        "source": "worldmonitor",
        "kind": "intel_brief",
        "ticker": "MSFT",
        "entities": ["Microsoft"],
        "headline": "",
        "url": "",
        "occurred_at": "2026-09-01T00:00:00Z",
        "filed_at": None,
        "observed_at": "2026-09-02T09:00:00Z",
        "confidence": 1.0,
        "raw_ref": "worldmonitor:us_intel",
    }
    values.update(overrides)
    return Event.from_dict(values)


class IntelFeatureTests(unittest.TestCase):
    def test_orders_on_observed_at_never_occurred_at(self):
        when = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)
        late_seen = event(
            occurred_at="2026-09-01T00:00:00Z",
            observed_at="2026-09-02T09:00:00Z",
        )
        features = intel_features([late_seen], when)
        self.assertNotIn("MSFT", features)

    def test_recorded_brief_uses_file_clock_and_no_http(self):
        with mock.patch("signal_sim.sources.worldmonitor.urllib.request.urlopen") as urlopen:
            events = load_recorded(FIXTURES)
        urlopen.assert_not_called()
        self.assertTrue(events)
        when = datetime(2026, 9, 2, 10, 15, tzinfo=UTC)
        for row in events:
            self.assertLessEqual(row.observed_at, when)
            self.assertEqual(row.kind, "intel_brief")
            self.assertEqual(row.source, "worldmonitor")
        tickers = {row.ticker for row in events}
        self.assertTrue({"MSFT", "XOM"}.issubset(tickers))
        features = intel_features(events, when)
        self.assertEqual(features["MSFT"]["intel_brief"], 1)
        self.assertEqual(features["MSFT"]["wm_intel"], 1)
        self.assertEqual(features["XOM"]["chokepoint"], 0)

    def test_recorded_intel_attaches_to_drift_and_diagnose_without_changing_rank(self):
        events = load_fixture_events(FIXTURES)
        decision = load_mark_book()["decision_at"]
        before = rank_candidates(events, window_end=decision)
        book = fixture_drift_book(FIXTURES)
        after = rank_candidates(events, window_end=decision)
        self.assertEqual(before, after)
        msft = next(row for row in book["targets"] if row["ticker"] == "MSFT")
        xom = next(row for row in book["targets"] if row["ticker"] == "XOM")
        xle = next(row for row in book["targets"] if row["ticker"] == "XLE")
        nvda = next(row for row in book["targets"] if row["ticker"] == "NVDA")
        self.assertEqual(msft["intel_brief"], 1)
        self.assertEqual(msft["wm_intel"], 1)
        self.assertEqual(xom["intel_brief"], 1)
        self.assertEqual(xle["intel_brief"], 1)
        self.assertEqual(nvda["intel_brief"], 0)
        self.assertIn("MSFT", book["intel"])
        diag = fixture_diagnostics(events)
        self.assertEqual(diag["intel"]["MSFT"]["wm_intel"], 1)
        self.assertGreaterEqual(diag["stats"]["n_intel"], 3)
        self.assertNotIn("sharpe", json_dump(book))

    def test_intel_flags_do_not_change_sized_frac(self):
        from signal_sim.sizer import size_targets

        book = fixture_drift_book(FIXTURES)
        msft = next(row for row in book["targets"] if row["ticker"] == "MSFT")
        stripped = dict(msft)
        stripped.pop("intel_brief")
        stripped.pop("wm_intel")
        stripped.pop("chokepoint")
        with_keys, _ = size_targets([msft], size_frac=0.1, horizon_hours=7.75)
        without_keys, _ = size_targets([stripped], size_frac=0.1, horizon_hours=7.75)
        self.assertEqual(with_keys[0]["target_frac"], without_keys[0]["target_frac"])

    def test_live_raises_without_key_and_skips_http(self):
        with mock.patch("signal_sim.sources.worldmonitor.read_env", return_value=None):
            with mock.patch("signal_sim.sources.worldmonitor.urllib.request.urlopen") as urlopen:
                with self.assertRaisesRegex(ValueError, "WORLD_MONITOR_KEY is missing"):
                    live()
            urlopen.assert_not_called()


def json_dump(payload):
    import json

    return json.dumps(payload).lower()


if __name__ == "__main__":
    unittest.main()
