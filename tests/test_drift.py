"""Fixture-only cluster-drift stub. Not alpha. Rank stays unchanged."""

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

from signal_sim import cli
from signal_sim.clusters import online_clusters
from signal_sim.drift import cluster_state, drift_targets, fixture_drift_book
from signal_sim.events import Event
from signal_sim.fixture_load import load_fixture_events
from signal_sim.indicators import rank_candidates
from signal_sim.sim import load_mark_book, run_fixture_replay


REPO = Path(__file__).resolve().parent.parent
FIXTURES = REPO / "fixtures"
UTC = timezone.utc
PRINTED = {"AAPL", "CMCSA", "CVX", "DIS", "MSFT", "NFLX", "NVDA", "QQQ", "SPY", "XLE", "XLK", "XOM"}
NO_PRINT = {"AMZN", "GOOGL", "META"}
LIQUID_FILLS = {"NVDA", "MSFT", "XLE", "XOM", "DIS", "NFLX", "SPY", "QQQ"}


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


class ClusterDriftStubTests(unittest.TestCase):
    def test_larger_cluster_gets_larger_target_frac(self):
        when = datetime(2026, 9, 2, 10, 15, tzinfo=UTC)
        events = [
            event("n1", "2026-09-02T09:00:00Z"),
            event("n2", "2026-09-02T10:00:00Z"),
            event("xle", "2026-09-02T10:00:00Z", ticker="XLE"),
        ]
        targets = {row["ticker"]: row for row in drift_targets(events, when=when, size_frac=0.1, horizon_hours=34.75)}
        self.assertGreater(targets["NVDA"]["cluster_size"], targets["XLE"]["cluster_size"])
        self.assertGreater(targets["NVDA"]["target_frac"], targets["XLE"]["target_frac"])
        self.assertEqual(targets["NVDA"]["side"], "buy")
        self.assertEqual(targets["NVDA"]["horizon_hours"], 34.75)

    def test_late_prints_do_not_enter_state(self):
        when = datetime(2026, 9, 2, 10, 15, tzinfo=UTC)
        events = [
            event("early", "2026-09-02T10:00:00Z"),
            event("late", "2026-09-02T11:00:00Z"),
        ]
        state = cluster_state(events, when)
        self.assertEqual(state["NVDA"]["cluster_size"], 1)
        self.assertEqual(online_clusters(events, when)[0]["size"], 1)

    def test_fixture_book_uses_prints_at_decision_and_skips_no_print(self):
        book = fixture_drift_book(FIXTURES)
        self.assertEqual(book["mode"], "local-paper-drift")
        self.assertIn("not a fitted", book["note"].lower())
        self.assertIn("not alpha", book["note"].lower())
        tickers = {row["ticker"] for row in book["targets"]}
        self.assertEqual(tickers, PRINTED)
        self.assertTrue(tickers.isdisjoint(NO_PRINT))
        nvda = next(row for row in book["targets"] if row["ticker"] == "NVDA")
        self.assertEqual(nvda["side"], "buy")
        self.assertGreater(nvda["target_frac"], 0)
        self.assertAlmostEqual(book["horizon_hours"], 34.75)
        self.assertNotIn("sharpe", json.dumps(book).lower())
        self.assertNotIn("yahoo", json.dumps(book).lower())

    def test_late_fixture_nvda_print_does_not_change_targets(self):
        book = load_mark_book()
        events = load_fixture_events(FIXTURES)
        late = [event for event in events if event.id in {"fixture-nvda-late", "tr-late"} or "late" in event.id]
        self.assertTrue(any(event.observed_at > book["decision_at"] for event in events if event.ticker == "NVDA"))
        at_cut = drift_targets(
            [event for event in events if event.observed_at <= book["decision_at"]],
            when=book["decision_at"],
            size_frac=book["size_frac"],
            horizon_hours=34.75,
        )
        with_late = drift_targets(
            events,
            when=book["decision_at"],
            size_frac=book["size_frac"],
            horizon_hours=34.75,
        )
        self.assertEqual(at_cut, with_late)
        self.assertTrue(late or any(event.observed_at > book["decision_at"] for event in events))

    def test_scoring_does_not_change_rank(self):
        events = load_fixture_events(FIXTURES)
        book = load_mark_book()
        before = rank_candidates(events, window_end=book["decision_at"])
        fixture_drift_book(FIXTURES)
        after = rank_candidates(events, window_end=book["decision_at"])
        self.assertEqual(before, after)

    def test_cli_requires_fixtures(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = cli.main(["drift"])
        self.assertEqual(exit_code, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("requires --fixtures", error.getvalue())

    def test_cli_fixtures_prints_target_book(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(["drift", "--fixtures"])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "local-paper-drift")
        self.assertEqual({row["ticker"] for row in payload["targets"]}, PRINTED)
        self.assertTrue(all(row["horizon_hours"] == payload["horizon_hours"] for row in payload["targets"]))

    def test_replay_drift_fills_liquid_from_target_book(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        ledger = os.path.join(tmp, "drift-ledger.sqlite")
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(["replay", "--fixtures", "--drift", "--ledger", ledger])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["signal"], "cluster-drift-stub")
        self.assertEqual({row["ticker"] for row in payload["orders"]}, LIQUID_FILLS)
        self.assertEqual(
            {row["ticker"] for row in payload["refusals"]},
            {"AAPL", "CMCSA", "CVX", "XLK"},
        )
        self.assertTrue(all(row["reason"] == "no_mark" for row in payload["refusals"]))
        self.assertNotIn("sharpe", json.dumps(payload).lower())

    def test_default_replay_stays_on_rank_not_drift(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(
                ["replay", "--fixtures", "--ledger", os.path.join(tmp, "rank-ledger.sqlite")]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertNotIn("signal", payload)
        self.assertEqual({row["ticker"] for row in payload["orders"]}, LIQUID_FILLS)

    def test_replay_path_with_drift_opens_adds_and_reduces_across_sectors(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = cli.main(
                [
                    "replay",
                    "--fixtures",
                    "--path",
                    "--drift",
                    "--ledger",
                    os.path.join(tmp, "drift-path.sqlite"),
                ]
            )
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "local-paper-path")
        self.assertEqual(payload["signal"], "cluster-drift-stub")
        self.assertEqual(len(payload["position_history"]), 3)
        first = set(payload["position_history"][0]["held"])
        second = set(payload["position_history"][1]["held"])
        third = set(payload["position_history"][2]["held"])
        self.assertTrue({"NVDA", "MSFT"} & first)
        self.assertTrue({"XLE", "XOM"} & first)
        self.assertTrue({"DIS", "NFLX"} & first)
        self.assertTrue({"SPY", "QQQ"} & first)
        self.assertTrue(second)
        self.assertTrue(third)
        self.assertTrue(first - second, "later drift must reduce or close a step-1 name")
        self.assertTrue(third - second, "a later step must add a name across the liquid book")
        self.assertTrue({"MSFT", "NFLX"} <= second)
        self.assertTrue({"SPY", "XLE"} <= third)
        self.assertNotIn("sharpe", json.dumps(payload).lower())
        self.assertNotIn("yahoo", json.dumps(payload).lower())

    def test_intensity_flag_attaches_diagnose_feature_and_never_raises_size(self):
        plain = fixture_drift_book(FIXTURES)
        featured = fixture_drift_book(FIXTURES, intensity=True)
        self.assertNotIn("intensity", json.dumps(plain))
        self.assertIn("not a fit", featured["intensity_note"].lower())
        by_plain = {row["ticker"]: row["target_frac"] for row in plain["targets"]}
        by_feat = {row["ticker"]: row for row in featured["targets"]}
        self.assertEqual(set(by_plain), set(by_feat))
        for ticker, frac in by_plain.items():
            self.assertIn("intensity", by_feat[ticker])
            self.assertLessEqual(by_feat[ticker]["intensity_scale"], 1.0)
        from signal_sim.sizer import size_targets

        sized_plain, _ = size_targets(
            plain["targets"], size_frac=0.1, horizon_hours=34.75
        )
        sized_int, _ = size_targets(
            featured["targets"], size_frac=0.1, horizon_hours=34.75
        )
        plain_frac = {row["ticker"]: row["target_frac"] for row in sized_plain}
        int_frac = {row["ticker"]: row["target_frac"] for row in sized_int}
        for ticker, frac in plain_frac.items():
            if ticker in int_frac:
                self.assertLessEqual(int_frac[ticker], frac)

    def test_intensity_overlay_does_not_change_rank(self):
        events = load_fixture_events(FIXTURES)
        book = load_mark_book()
        before = rank_candidates(events, window_end=book["decision_at"])
        fixture_drift_book(FIXTURES, intensity=True)
        after = rank_candidates(events, window_end=book["decision_at"])
        self.assertEqual(before, after)

    def test_replay_intensity_requires_drift(self):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            exit_code = cli.main(["replay", "--fixtures", "--intensity"])
        self.assertEqual(exit_code, 2)
        self.assertIn("requires --drift", error.getvalue())

    def test_mutated_drift_does_not_change_rank(self):
        events = load_fixture_events(FIXTURES)
        book = load_mark_book()
        before = rank_candidates(events, window_end=book["decision_at"])
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        run_fixture_replay(
            fixtures=FIXTURES,
            ledger_path=os.path.join(tmp, "unused.sqlite"),
            candidates=fixture_drift_book(FIXTURES)["targets"],
        )
        after = rank_candidates(events, window_end=book["decision_at"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
