import io
import json
import math
import random
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from signal_sim import cli
from signal_sim.events import Event


UTC = timezone.utc


def event(event_id, observed_at, *, kind="news", ticker="NVDA", occurred_at=None):
    return Event.from_dict(
        {
            "id": event_id,
            "source": "fixture",
            "kind": kind,
            "ticker": ticker,
            "entities": [ticker],
            "headline": "Fixture event",
            "url": f"https://example.invalid/{event_id}",
            "occurred_at": occurred_at or observed_at,
            "filed_at": observed_at if kind in {"insider", "congress_trade"} else None,
            "observed_at": observed_at,
            "confidence": 1.0,
            "raw_ref": f"fixture:{event_id}",
        }
    )


class HawkesIntensityTests(unittest.TestCase):
    def test_event_causes_a_finite_upward_intensity_jump(self):
        from signal_sim.hawkes import intensity_at

        arrival = datetime(2026, 9, 2, 10, tzinfo=UTC)
        events = [event("news-1", arrival)]

        before = intensity_at(events, arrival - timedelta(microseconds=1))
        after = intensity_at(events, arrival + timedelta(microseconds=1))

        self.assertTrue(math.isfinite(after))
        self.assertGreater(after, before)

    def test_future_observation_is_excluded_and_occurred_at_is_ignored(self):
        from signal_sim.hawkes import intensity_at

        when = datetime(2026, 9, 2, 11, tzinfo=UTC)
        past = event(
            "past",
            datetime(2026, 9, 2, 10, tzinfo=UTC),
            occurred_at="2030-01-01T00:00:00Z",
        )
        future = event(
            "future",
            datetime(2026, 9, 2, 12, tzinfo=UTC),
            occurred_at="2000-01-01T00:00:00Z",
        )

        self.assertEqual(intensity_at([past, future], when), intensity_at([past], when))

    def test_existing_positive_rank_feature_strengthens_the_mark(self):
        from signal_sim.hawkes import intensity_at

        arrival = datetime(2026, 9, 2, 10, tzinfo=UTC)
        default_event = event("default", arrival)
        marked_event = event("marked", arrival)
        marked_event.news_breakout = 3
        when = arrival + timedelta(minutes=1)

        self.assertGreater(
            intensity_at([marked_event], when),
            intensity_at([default_event], when),
        )


class HawkesLikelihoodTests(unittest.TestCase):
    def test_likelihood_prefers_cluster_when_seeded_shuffle_moves_exciting_mark(self):
        from signal_sim.hawkes import log_likelihood

        start = datetime(2026, 9, 2, 10, tzinfo=UTC)
        offsets = [0.0, 0.1, 0.2, 4.0]
        clustered = [
            event(f"event-{index}", start + timedelta(hours=offset))
            for index, offset in enumerate(offsets)
        ]
        clustered[0].news_breakout = 4
        shuffled_offsets = list(offsets)
        random.Random(7).shuffle(shuffled_offsets)
        self.assertCountEqual(shuffled_offsets, offsets)
        self.assertNotEqual(shuffled_offsets[0], offsets[0])
        shuffled = [
            event(f"event-{index}", start + timedelta(hours=offset))
            for index, offset in enumerate(shuffled_offsets)
        ]
        shuffled[0].news_breakout = 4
        end = start + timedelta(hours=5)

        self.assertGreater(
            log_likelihood(clustered, start=start, end=end),
            log_likelihood(shuffled, start=start, end=end),
        )


class HawkesCliTests(unittest.TestCase):
    def test_intensity_fixtures_prints_one_value_per_universe_ticker(self):
        fixture_events = [
            event("nvda", "2026-09-02T10:00:00Z", ticker="NVDA"),
            event("xle", "2026-09-02T11:00:00Z", ticker="XLE"),
        ]
        output = io.StringIO()

        with patch.object(
            cli, "load_fixture_events", return_value=fixture_events
        ) as load, redirect_stdout(output):
            exit_code = cli.main(["intensity", "--fixtures"])

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(set(payload), {"NVDA", "XLE", "DIS"})
        self.assertTrue(all(math.isfinite(value) for value in payload.values()))
        self.assertGreater(payload["NVDA"], payload["XLE"])
        self.assertEqual(payload["XLE"], payload["DIS"])
        load.assert_called_once()


if __name__ == "__main__":
    unittest.main()
