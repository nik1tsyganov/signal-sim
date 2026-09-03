import unittest
from unittest import mock
import datetime

from signal_sim.sources.worldmonitor import live

class TestWMExpand(unittest.TestCase):
    @mock.patch("signal_sim.sources.worldmonitor.urllib.request.urlopen")
    @mock.patch("signal_sim.sources.worldmonitor.read_env")
    def test_live_nvda_and_chokepoints(self, mock_read_env, mock_urlopen):
        mock_read_env.return_value = "fake-key"
        
        class ContextManagerMock:
            def __init__(self, content):
                self.content = content
            def __enter__(self):
                cm = mock.MagicMock()
                cm.read.return_value = self.content
                return cm
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
                
        def side_effect(req):
            self.assertEqual(req.get_header("User-agent"), "signal-sim-paper/0.1")
            self.assertEqual(req.get_header("X-worldmonitor-key"), "fake-key")
            
            if "get-country-intel-brief" in req.full_url:
                return ContextManagerMock(b'{"brief": "nvidia is doing well", "generatedAt": "2026-09-03T00:00:00Z"}')
            elif "get-chokepoint-status" in req.full_url:
                return ContextManagerMock(b'{"chokepoints": [{"fetchedAt": "2026-09-03T01:00:00Z", "congestionLevel": "High"}]}')
            return ContextManagerMock(b'{}')

        mock_urlopen.side_effect = side_effect
        
        events = live()
        self.assertEqual(len(events), 2)
        
        intel_event = [e for e in events if e.ticker == "NVDA"][0]
        self.assertEqual(intel_event.occurred_at, datetime.datetime(2026, 9, 3, 0, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(intel_event.kind, "intel_brief")
        self.assertEqual(intel_event.source, "worldmonitor")
        
        cp_event = [e for e in events if e.ticker == "XLE"][0]
        self.assertEqual(cp_event.occurred_at, datetime.datetime(2026, 9, 3, 1, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(cp_event.kind, "intel_brief")

    @mock.patch("signal_sim.sources.worldmonitor.urllib.request.urlopen")
    @mock.patch("signal_sim.sources.worldmonitor.read_env")
    def test_live_dis_and_future_observed(self, mock_read_env, mock_urlopen):
        mock_read_env.return_value = "fake-key"
        class ContextManagerMock:
            def __init__(self, content):
                self.content = content
            def __enter__(self):
                cm = mock.MagicMock()
                cm.read.return_value = self.content
                return cm
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
                
        def side_effect(req):
            if "get-country-intel-brief" in req.full_url:
                return ContextManagerMock(b'{"brief": "Disney", "generatedAt": "2099-01-01T00:00:00Z"}')
            elif "get-chokepoint-status" in req.full_url:
                return ContextManagerMock(b'{"chokepoints": []}')
            return ContextManagerMock(b'{}')

        mock_urlopen.side_effect = side_effect
        
        events = live()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].ticker, "DIS")
        self.assertEqual(events[0].occurred_at, datetime.datetime(2099, 1, 1, 0, 0, tzinfo=datetime.timezone.utc))
        self.assertEqual(events[0].observed_at, datetime.datetime(2099, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)) # max(now, occurred_at)

    @mock.patch("signal_sim.sources.worldmonitor.urllib.request.urlopen")
    @mock.patch("signal_sim.sources.worldmonitor.read_env")
    def test_live_skip_unknown(self, mock_read_env, mock_urlopen):
        mock_read_env.return_value = "fake-key"
        class ContextManagerMock:
            def __init__(self, content):
                self.content = content
            def __enter__(self):
                cm = mock.MagicMock()
                cm.read.return_value = self.content
                return cm
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
                
        def side_effect(req):
            if "get-country-intel-brief" in req.full_url:
                return ContextManagerMock(b'{"brief": "Nothing about the specific companies.", "generatedAt": "2026-09-03T00:00:00Z"}')
            elif "get-chokepoint-status" in req.full_url:
                return ContextManagerMock(b'{"chokepoints": []}')
            return ContextManagerMock(b'{}')

        mock_urlopen.side_effect = side_effect
        
        events = live()
        self.assertEqual(len(events), 0)

if __name__ == "__main__":
    unittest.main()
