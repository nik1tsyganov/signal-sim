"""TrendRadar fixture flags. observed_at only. No GPL client."""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from signal_sim.diagnose import fixture_diagnostics
from signal_sim.drift import fixture_drift_book
from signal_sim.events import Event
from signal_sim.fixture_load import load_fixture_events
from signal_sim.indicators import rank_candidates, trendradar_features
from signal_sim.sim import load_mark_book


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
UTC = timezone.utc


def event(**overrides):
    values = {
        "id": "tr-test",
        "source": "trendradar",
        "kind": "news",
        "ticker": "NVDA",
        "entities": ["NVIDIA"],
        "headline": "",
        "url": "",
        "occurred_at": "2026-09-02T09:00:00Z",
        "filed_at": None,
        "observed_at": "2026-09-02T10:10:00Z",
        "confidence": 1.0,
        "raw_ref": "tr-hotspot-test",
    }
    values.update(overrides)
    return Event.from_dict(values)


class TrendRadarFeatureTests(unittest.TestCase):
    def test_orders_on_observed_at_never_occurred_at(self):
        when = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)
        late = event(
            occurred_at="2026-09-02T09:00:00Z",
            observed_at="2026-09-02T10:10:00Z",
        )
        self.assertNotIn("NVDA", trendradar_features([late], when))

    def test_fixture_nvda_has_trendradar_flag_without_changing_rank_or_size(self):
        events = load_fixture_events(FIXTURES)
        decision = load_mark_book()["decision_at"]
        before = rank_candidates(events, window_end=decision)
        book = fixture_drift_book(FIXTURES)
        after = rank_candidates(events, window_end=decision)
        self.assertEqual(before, after)
        nvda = next(row for row in book["targets"] if row["ticker"] == "NVDA")
        xle = next(row for row in book["targets"] if row["ticker"] == "XLE")
        self.assertEqual(nvda["trendradar"], 1)
        self.assertEqual(xle["trendradar"], 0)
        self.assertEqual(book["trendradar"]["NVDA"]["trendradar"], 1)
        diag = fixture_diagnostics(events)
        self.assertEqual(diag["trendradar"]["NVDA"]["trendradar"], 1)
        self.assertGreaterEqual(diag["stats"]["n_trendradar"], 1)
        from signal_sim.sizer import size_targets

        stripped = dict(nvda)
        stripped.pop("trendradar")
        with_keys, _ = size_targets([nvda], size_frac=0.1, horizon_hours=7.75)
        without_keys, _ = size_targets([stripped], size_frac=0.1, horizon_hours=7.75)
        self.assertEqual(with_keys[0]["target_frac"], without_keys[0]["target_frac"])

    def test_package_has_no_trendradar_live_client(self):
        news = Path(__file__).resolve().parent.parent / "signal_sim" / "sources" / "news.py"
        source = news.read_text(encoding="utf-8")
        self.assertNotIn("urlopen", source)
        self.assertNotIn("def live", source)


if __name__ == "__main__":
    unittest.main()
