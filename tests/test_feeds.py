import json
import unittest
from unittest import mock

from signal_sim.sources.worldmonitor import live as worldmonitor_live
from signal_sim.sources.altdata import QuiverSource


class WorldMonitorTests(unittest.TestCase):
    @mock.patch("signal_sim.sources.worldmonitor.read_env")
    def test_missing_key_raises(self, mock_read_env):
        mock_read_env.return_value = None
        with self.assertRaisesRegex(ValueError, "WORLD_MONITOR_KEY is missing"):
            worldmonitor_live()

    @mock.patch("signal_sim.sources.worldmonitor.urllib.request.urlopen")
    @mock.patch("signal_sim.sources.worldmonitor.read_env")
    def test_live_maps_payload(self, mock_read_env, mock_urlopen):
        mock_read_env.return_value = "fake-key"
        
        mock_response = mock.MagicMock()
        mock_response.read.return_value = json.dumps([{
            "id": "wm-123",
            "ticker": "AAPL",
            "occurred_at": "2026-09-02T10:00:00Z"
        }]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        events = worldmonitor_live()
        
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.source, "worldmonitor")
        self.assertEqual(event.kind, "intel_brief")
        self.assertEqual(event.ticker, "AAPL")
        self.assertEqual(event.id, "wm-123")
        self.assertIsNotNone(event.observed_at)
        
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.get_header("X-worldmonitor-key"), "fake-key")


class QuiverSourceTests(unittest.TestCase):
    @mock.patch("signal_sim.secrets.read_env")
    @mock.patch("urllib.request.urlopen")
    def test_live_maps_payload(self, mock_urlopen, mock_read_env):
        mock_read_env.return_value = "fake-key"
        
        mock_response = mock.MagicMock()
        mock_response.read.return_value = json.dumps([{
            "id": "quiver-123",
            "ticker": "AAPL",
            "representative": "Alice",
            "transaction": "Purchase",
            "amount_range_usd": [1000, 5000],
            "trade_date": "2026-08-01T00:00:00Z",
            "report_date": "2026-08-10T00:00:00Z"
        }]).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response
        
        source = QuiverSource()
        events = source.live()
        
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["source"], "quiver")
        self.assertEqual(event["kind"], "congress_trade")
        self.assertEqual(event["ticker"], "AAPL")
        self.assertEqual(event["occurred_at"], "2026-08-01T00:00:00Z")
        self.assertEqual(event["filed_at"], "2026-08-10T00:00:00Z")
        self.assertEqual(event["rank_at"], "2026-08-10T00:00:00Z")
        
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.get_header("Authorization"), "Bearer fake-key")

if __name__ == "__main__":
    unittest.main()
