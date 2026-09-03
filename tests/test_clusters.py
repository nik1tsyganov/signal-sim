"""Online news clusters are rebuilt at decision time. They are not a signal."""

import unittest
from datetime import datetime, timezone

from signal_sim.clusters import online_clusters
from signal_sim.events import Event


UTC = timezone.utc


def event(event_id, observed_at, *, ticker="NVDA", kind="news"):
    return Event.from_dict(
        {
            "id": event_id,
            "source": "fixture",
            "kind": kind,
            "ticker": ticker,
            "entities": [ticker],
            "headline": "Fixture event",
            "url": f"https://example.invalid/{event_id}",
            "occurred_at": observed_at,
            "filed_at": None,
            "observed_at": observed_at,
            "confidence": 1.0,
            "raw_ref": f"fixture:{event_id}",
        }
    )


class OnlineClusterTests(unittest.TestCase):
    def test_future_prints_are_excluded_and_same_day_prints_cluster(self):
        decision = datetime(2026, 9, 2, 10, 15, tzinfo=UTC)
        events = [
            event("n1", "2026-09-02T09:00:00Z"),
            event("n2", "2026-09-02T10:00:00Z"),
            event("late", "2026-09-02T11:00:00Z"),
            event("xle", "2026-09-02T10:15:00Z", ticker="XLE", kind="intel_brief"),
            event("old", "2026-09-01T12:00:00Z"),
        ]
        clusters = online_clusters(events, decision)
        by_key = {(row["ticker"], row["day"]): row for row in clusters}
        self.assertEqual(by_key[("NVDA", "2026-09-02")]["size"], 2)
        self.assertEqual(by_key[("XLE", "2026-09-02")]["size"], 1)
        self.assertEqual(by_key[("NVDA", "2026-09-01")]["size"], 1)
        self.assertNotIn(("NVDA", "2026-09-02-late"), by_key)
        self.assertTrue(all(row["last_seen_at"] <= decision.isoformat().replace("+00:00", "Z") for row in clusters))
