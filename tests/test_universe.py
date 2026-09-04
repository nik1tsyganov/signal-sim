"""Operating universe expansion from live intel tickers."""

import unittest

from signal_sim.events import Event
from signal_sim.indicators import UNIVERSE
from signal_sim.universe import (
    INTEL_TOP_N,
    expand_operating_universe,
    is_tradable_ticker,
    load_liquid_allowlist,
)


def _event(ticker, source="quiver", kind="news"):
    return Event.from_dict(
        {
            "id": f"{source}-{ticker}",
            "source": source,
            "kind": kind,
            "ticker": ticker,
            "entities": [ticker],
            "headline": "SECRET HEADLINE",
            "url": "https://example.invalid/pii",
            "occurred_at": "2026-09-04T16:00:00Z",
            "filed_at": None,
            "observed_at": "2026-09-04T16:00:00Z",
            "confidence": 1.0,
            "raw_ref": "raw-pii-ref",
        }
    )


class UniverseExpandTests(unittest.TestCase):
    def test_allowlist_covers_fixture_and_liquid_extras(self):
        allowlist = load_liquid_allowlist()
        self.assertTrue(set(UNIVERSE).issubset(set(allowlist)))
        self.assertIn("TSLA", allowlist)
        self.assertGreater(len(allowlist), len(UNIVERSE))

    def test_garbage_tickers_are_rejected(self):
        self.assertTrue(is_tradable_ticker("TSLA"))
        self.assertFalse(is_tradable_ticker("tsla"))
        self.assertFalse(is_tradable_ticker("BRK.B"))
        self.assertFalse(is_tradable_ticker("BTC-USD"))
        self.assertFalse(is_tradable_ticker("XXXXXX"))
        self.assertFalse(is_tradable_ticker(""))

    def test_operating_universe_is_fixture_union_top_n_intel(self):
        events = [
            _event("TSLA"),
            _event("TSLA"),
            _event("AMD"),
            {"ticker": "JPM"},
            {"ticker": "BTC-USD"},
            {"ticker": "not-a-ticker"},
            _event("NVDA"),
        ]
        expanded = expand_operating_universe(events, top_n=2)
        self.assertEqual(expanded["fixture"], list(UNIVERSE))
        self.assertEqual(expanded["intel"], ["TSLA", "AMD"])
        self.assertTrue(set(UNIVERSE).issubset(set(expanded["operating"])))
        self.assertIn("TSLA", expanded["operating"])
        self.assertIn("AMD", expanded["operating"])
        self.assertNotIn("BTC-USD", expanded["operating"])
        self.assertNotIn("JPM", expanded["intel"])
        self.assertEqual(len(expanded["operating"]), len(UNIVERSE) + 2)

    def test_cap_drops_lower_count_intel_names(self):
        events = [_event("TSLA")] * 5 + [_event("AMD")] * 3 + [_event("AVGO")]
        expanded = expand_operating_universe(events, top_n=1)
        self.assertEqual(expanded["intel"], ["TSLA"])
        self.assertTrue(expanded["capped"])
        self.assertEqual(expanded["top_n"], 1)
        self.assertNotIn("AVGO", expanded["operating"])
        self.assertEqual(INTEL_TOP_N, 12)
