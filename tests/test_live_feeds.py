"""Live intel CLI: counts and ticker histogram only."""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from signal_sim import cli
from signal_sim.events import Event
from signal_sim.live_feeds import (
    LiveFeedConfigError,
    missing_live_feed_keys,
    pull_live_feeds,
    ticker_histogram,
)


def _event(ticker, source="worldmonitor", kind="intel_brief"):
    return Event.from_dict(
        {
            "id": f"{source}-{ticker}",
            "source": source,
            "kind": kind,
            "ticker": ticker,
            "entities": ["should-not-print"],
            "headline": "SECRET HEADLINE about a person",
            "url": "https://example.invalid/pii",
            "occurred_at": "2026-09-02T10:00:00Z",
            "filed_at": None,
            "observed_at": "2026-09-02T10:00:00Z",
            "confidence": 1.0,
            "raw_ref": "raw-pii-ref",
        }
    )


class LiveFeedUnitTests(unittest.TestCase):
    def test_histogram_counts_tickers_only(self):
        events = [
            _event("NVDA"),
            _event("NVDA"),
            {"ticker": "XLE", "person": "Rep. Hidden", "headline": "nope"},
        ]
        self.assertEqual(ticker_histogram(events), {"NVDA": 2, "XLE": 1})

    def test_missing_keys_raise_before_http(self):
        with mock.patch("signal_sim.live_feeds.read_env", return_value=None), mock.patch(
            "signal_sim.sources.altdata.live"
        ) as quiver, mock.patch("signal_sim.sources.worldmonitor.live") as world:
            with self.assertRaises(LiveFeedConfigError) as error:
                pull_live_feeds()
            self.assertEqual(
                error.exception.missing, ["QUIVER_API_KEY", "WORLD_MONITOR_KEY"]
            )
            quiver.assert_not_called()
            world.assert_not_called()

    def test_pull_summarizes_without_raw_fields(self):
        quiver_events = [
            {
                "ticker": "NVDA",
                "person": "Rep. Hidden",
                "headline": "insider buy",
                "raw_ref": "quiver:secret",
            },
            _event("MSFT", source="quiver", kind="gov_contract"),
        ]
        world_events = [_event("XLE"), _event("DIS")]
        with mock.patch("signal_sim.live_feeds.read_env", return_value="present"), mock.patch(
            "signal_sim.sources.altdata.live", return_value=quiver_events
        ), mock.patch("signal_sim.sources.worldmonitor.live", return_value=world_events):
            report = pull_live_feeds()
        self.assertTrue(report["ok"])
        self.assertEqual(report["mode"], "live-intel")
        self.assertEqual(report["quiver"]["n"], 2)
        self.assertEqual(report["quiver"]["tickers"], {"MSFT": 1, "NVDA": 1})
        self.assertEqual(report["worldmonitor"]["n"], 2)
        dumped = json.dumps(report)
        self.assertNotIn("Rep. Hidden", dumped)
        self.assertNotIn("SECRET HEADLINE", dumped)
        self.assertNotIn("insider buy", dumped)
        self.assertNotIn("example.invalid", dumped)
        self.assertNotIn("raw-pii-ref", dumped)
        self.assertNotIn("should-not-print", dumped)


class LiveFeedCliTests(unittest.TestCase):
    def test_requires_live_flag(self):
        error = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(error):
            code = cli.main(["feeds"])
        self.assertEqual(code, 2)
        self.assertIn("requires --live", error.getvalue())

    def test_missing_keys_exit_2(self):
        error = io.StringIO()
        with mock.patch("signal_sim.live_feeds.read_env", return_value=None), redirect_stdout(
            io.StringIO()
        ), redirect_stderr(error):
            code = cli.main(["feeds", "--live"])
        self.assertEqual(code, 2)
        self.assertIn("QUIVER_API_KEY", error.getvalue())
        self.assertIn("WORLD_MONITOR_KEY", error.getvalue())

    def test_live_prints_counts_and_histogram(self):
        printed = io.StringIO()
        with mock.patch("signal_sim.live_feeds.read_env", return_value="present"), mock.patch(
            "signal_sim.sources.altdata.live",
            return_value=[{"ticker": "NVDA", "person": "Rep. Hidden"}],
        ), mock.patch(
            "signal_sim.sources.worldmonitor.live", return_value=[_event("XLE")]
        ), redirect_stdout(printed), redirect_stderr(io.StringIO()):
            code = cli.main(["feeds", "--live"])
        self.assertEqual(code, 0)
        payload = json.loads(printed.getvalue())
        self.assertEqual(payload["quiver"]["n"], 1)
        self.assertEqual(payload["worldmonitor"]["tickers"], {"XLE": 1})
        self.assertNotIn("Rep. Hidden", printed.getvalue())
        self.assertNotIn("SECRET HEADLINE", printed.getvalue())


def _live_intel_keys_present():
    return bool(os.environ.get("QUIVER_API_KEY", "").strip()) and bool(
        os.environ.get("WORLD_MONITOR_KEY", "").strip()
    )


@unittest.skipUnless(_live_intel_keys_present(), "QUIVER_API_KEY/WORLD_MONITOR_KEY not set")
class LiveIntelIntegrationTests(unittest.TestCase):
    def test_pull_live_feeds_returns_counts(self):
        self.assertEqual(missing_live_feed_keys(), [])
        report = pull_live_feeds()
        self.assertTrue(report["ok"])
        self.assertIsInstance(report["quiver"]["n"], int)
        self.assertGreaterEqual(report["quiver"]["n"], 0)
        self.assertIsInstance(report["worldmonitor"]["n"], int)
        self.assertGreaterEqual(report["worldmonitor"]["n"], 0)
        self.assertIsInstance(report["quiver"]["tickers"], dict)
        dumped = json.dumps(report)
        self.assertNotIn("person", dumped)
        self.assertNotIn("headline", dumped)
        self.assertNotIn("raw_ref", dumped)


if __name__ == "__main__":
    unittest.main()
