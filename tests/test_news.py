import json
import os
import shutil
import tempfile
import unittest

from signal_sim.sources.news import load_events


class TestNewsSources(unittest.TestCase):
    def setUp(self):
        self.fixtures_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "fixtures", "news"))

    def _temp_dir(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return tmp

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

    def test_list_payload_is_flattened(self):
        tmp = self._temp_dir()
        with open(os.path.join(tmp, "batch.json"), "w", encoding="utf-8") as handle:
            json.dump(
                [
                    {"id": "n-1", "source": "trendradar", "ticker": "NVDA"},
                    {"id": "n-2", "source": "worldmonitor", "ticker": "XLE"},
                ],
                handle,
            )

        events = load_events(tmp)

        self.assertEqual([event["id"] for event in events], ["n-1", "n-2"])

    def test_bad_json_raises(self):
        tmp = self._temp_dir()
        with open(os.path.join(tmp, "broken.json"), "w", encoding="utf-8") as handle:
            handle.write("{not json")

        with self.assertRaises(json.JSONDecodeError):
            load_events(tmp)

    def test_poison_named_file_is_skipped(self):
        tmp = self._temp_dir()
        with open(os.path.join(tmp, "lookahead_poison.json"), "w", encoding="utf-8") as handle:
            json.dump({"id": "should-skip", "source": "trendradar"}, handle)

        self.assertEqual(load_events(tmp), [])

    def test_missing_dir_loads_nothing(self):
        self.assertEqual(load_events(os.path.join(self.fixtures_dir, "no-such-dir")), [])


if __name__ == "__main__":
    unittest.main()


