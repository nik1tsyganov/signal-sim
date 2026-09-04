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
        self.assertTrue({"NVDA", "XLE", "DIS"}.issubset(set(payload)))
        self.assertGreaterEqual(len(payload), 3)
        self.assertTrue(all(math.isfinite(value) for value in payload.values()))
        self.assertGreater(payload["NVDA"], payload["XLE"])
        self.assertEqual(payload["XLE"], payload["DIS"])
        load.assert_called_once()


class DiagnoseCliTests(unittest.TestCase):
    def test_diagnose_fixtures_prints_intensity_and_clusters(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(["diagnose", "--fixtures"])
        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["mode"], "local-paper-diagnose")
        self.assertIn("not a ranking input", payload["note"].lower())
        self.assertIn("decision_at", payload["note"])
        self.assertEqual(payload["cut"], "decision_at")
        self.assertEqual(payload["when"], payload["decision_at"])
        self.assertEqual(payload["decision_at"], "2026-09-02T10:15:00Z")
        self.assertGreaterEqual(payload["stats"]["n_events_after_decision"], 1)
        self.assertLess(payload["stats"]["n_events"], payload["stats"]["n_events"] + payload["stats"]["n_events_after_decision"])
        self.assertTrue({"NVDA", "XLE", "DIS", "SPY"}.issubset(set(payload["intensity"])))
        self.assertTrue(all(math.isfinite(value) for value in payload["intensity"].values()))
        self.assertGreaterEqual(len(payload["online_clusters"]), 1)
        self.assertTrue(math.isfinite(payload["hawkes_log_likelihood"]))
        self.assertGreaterEqual(payload["stats"]["n_clusters"], 1)
        self.assertNotIn("candidates", payload)
        self.assertNotIn("sharpe", json.dumps(payload).lower())

    def test_diagnose_does_not_change_rank_output(self):
        before = io.StringIO()
        with redirect_stdout(before):
            self.assertEqual(cli.main(["rank", "--fixtures"]), 0)
        diagnose = io.StringIO()
        with redirect_stdout(diagnose):
            self.assertEqual(cli.main(["diagnose", "--fixtures"]), 0)
        after = io.StringIO()
        with redirect_stdout(after):
            self.assertEqual(cli.main(["rank", "--fixtures"]), 0)
        self.assertEqual(json.loads(before.getvalue()), json.loads(after.getvalue()))
        self.assertNotEqual(
            json.loads(diagnose.getvalue()).get("mode"),
            "local-paper-replay",
        )

    def test_mutated_intensity_does_not_change_rank_or_replay_fills(self):
        import os
        import shutil
        import tempfile
        from pathlib import Path

        from signal_sim.cli import load_fixture_events
        from signal_sim.diagnose import fixture_diagnostics
        from signal_sim.indicators import rank_candidates
        from signal_sim.sim import load_mark_book, run_fixture_replay

        fixtures = Path(__file__).resolve().parent.parent / "fixtures"
        events = load_fixture_events(fixtures)
        decision_at = load_mark_book()["decision_at"]
        rank_before = rank_candidates(events, window_end=decision_at)
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        first = run_fixture_replay(
            fixtures=fixtures,
            ledger_path=os.path.join(tmp, "before.sqlite"),
            audit_path=os.path.join(tmp, "before.audit"),
            kill_root=tmp,
        )
        fills_before = [(row["ticker"], row["side"], row["fill_px"]) for row in first["orders"]]

        def inflated(_events, _when, **_kwargs):
            return 99.0

        with patch("signal_sim.diagnose.intensity_at", side_effect=inflated), patch(
            "signal_sim.hawkes.intensity_at", side_effect=inflated
        ):
            rank_after = rank_candidates(events, window_end=decision_at)
            second = run_fixture_replay(
                fixtures=fixtures,
                ledger_path=os.path.join(tmp, "after.sqlite"),
                audit_path=os.path.join(tmp, "after.audit"),
                kill_root=tmp,
            )
            diagnose = fixture_diagnostics(events)

        fills_after = [(row["ticker"], row["side"], row["fill_px"]) for row in second["orders"]]
        self.assertEqual(rank_before, rank_after)
        self.assertEqual(fills_before, fills_after)
        self.assertEqual(fills_before, [("NVDA", "buy", 178.5), ("XLE", "buy", 90.0)])
        self.assertTrue(all(value == 99.0 for value in diagnose["intensity"].values()))
        self.assertNotIn("sharpe", json.dumps(diagnose).lower())


if __name__ == "__main__":
    unittest.main()
