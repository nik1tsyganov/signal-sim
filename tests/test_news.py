import unittest
import os
import json
from signal_sim.sources.news import load_events

class TestNewsSources(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "fixtures", "news"))

    def test_load_events(self):
        events = load_events(self.fixtures_dir)
        
        # Expect at least two events (one from each source)
        self.assertGreaterEqual(len(events), 2)
        
        trendradar_events = [e for e in events if e["source"] == "trendradar"]
        self.assertGreaterEqual(len(trendradar_events), 1)
        nvda_event = next(e for e in trendradar_events if e["ticker"] == "NVDA")
        self.assertEqual(nvda_event["kind"], "news")
        self.assertEqual(nvda_event["id"], "tr-1")
        
        wm_events = [e for e in events if e["source"] == "worldmonitor"]
        self.assertGreaterEqual(len(wm_events), 1)
        xle_event = next(e for e in wm_events if e["ticker"] == "XLE")
        self.assertEqual(xle_event["kind"], "intel_brief")
        self.assertEqual(xle_event["id"], "wm-1")
        self.assertIn("confidence", xle_event)
        self.assertIn("url", xle_event)

if __name__ == "__main__":
    unittest.main()


