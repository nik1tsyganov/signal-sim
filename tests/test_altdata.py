import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from signal_sim.safety import (
    KILL_ROOT,
    PAPER_ONLY,
    LookaheadError,
    assert_event_timestamps,
    kill_switch_ok,
)
from signal_sim.sources.altdata import QuiverSource, load_events

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
FIXTURES_DIR = os.path.join(REPO_ROOT, "fixtures", "altdata")
GOOD_FIXTURES = ("congress_nvda.json", "insider_nvda.json")
POISON_FIXTURE = "lookahead_poison.json"
FORBIDDEN_BROKER_FRAGMENTS = (
    "alpaca.markets",
    "interactivebrokers",
    "tradier",
    "tradestation",
    ":7496",
    ":4001",
)


def honest_event(**overrides):
    values = {
        "id": "ptr-test-1",
        "source": "house-clerk",
        "kind": "congress_trade",
        "ticker": "NVDA",
        "person": "Rep. Example Member",
        "chamber": "house",
        "transaction": "purchase",
        "amount_range_usd": [1001, 15000],
        "occurred_at": "2026-07-15T00:00:00Z",
        "filed_at": "2026-08-10T21:00:00Z",
        "observed_at": "2026-08-11T14:02:00Z",
        "raw_ref": "fixture:test:1",
    }
    values.update(overrides)
    return values


class AltDataLoaderTests(unittest.TestCase):
    def _fixture_dir(self, *filenames):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for name in filenames:
            shutil.copy(os.path.join(FIXTURES_DIR, name), tmp)
        return tmp

    def _dir_with_event(self, event):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with open(os.path.join(tmp, "event.json"), "w", encoding="utf-8") as f:
            json.dump([event], f)
        return tmp

    def test_good_fixtures_load_ticker_mapped_events(self):
        events = load_events(self._fixture_dir(*GOOD_FIXTURES))

        self.assertEqual(len(events), 4)
        for event in events:
            self.assertEqual(event["ticker"], "NVDA")
            self.assertIn(event["transaction"], ("purchase", "sale"))
            low, high = event["amount_range_usd"]
            self.assertLessEqual(low, high)
        self.assertEqual(
            {event["kind"] for event in events}, {"congress_trade", "insider"}
        )

    def test_congress_events_cover_both_chambers(self):
        events = load_events(self._fixture_dir("congress_nvda.json"))
        self.assertEqual(
            {event["chamber"] for event in events}, {"house", "senate"}
        )

    def test_insider_events_include_chair_activity(self):
        events = load_events(self._fixture_dir("insider_nvda.json"))
        self.assertIn("chair", {event["role"] for event in events})

    def test_rank_at_is_filed_at_never_occurred_at(self):
        events = load_events(self._fixture_dir(*GOOD_FIXTURES))
        for event in events:
            self.assertEqual(event["rank_at"], event["filed_at"])
            self.assertNotEqual(event["rank_at"], event["occurred_at"])

    def test_events_sorted_by_filed_at(self):
        events = load_events(self._fixture_dir(*GOOD_FIXTURES))
        filed = [datetime.fromisoformat(event["filed_at"]) for event in events]
        self.assertEqual(filed, sorted(filed))

    def test_lookahead_poison_fixture_is_rejected(self):
        with self.assertRaises(LookaheadError):
            load_events(self._fixture_dir(POISON_FIXTURE))

    def test_missing_required_field_is_rejected(self):
        event = honest_event()
        del event["amount_range_usd"]
        with self.assertRaises(ValueError):
            load_events(self._dir_with_event(event))

    def test_inverted_amount_range_is_rejected(self):
        event = honest_event(amount_range_usd=[15000, 1001])
        with self.assertRaises(ValueError):
            load_events(self._dir_with_event(event))

    def test_nan_amount_bound_is_rejected(self):
        event = honest_event(amount_range_usd=[0, float("nan")])
        with self.assertRaises(ValueError):
            load_events(self._dir_with_event(event))

    def test_boolean_amount_bound_is_rejected(self):
        event = honest_event(amount_range_usd=[False, 1])
        with self.assertRaises(ValueError):
            load_events(self._dir_with_event(event))

    def test_missing_dir_loads_nothing(self):
        self.assertEqual(load_events(os.path.join(REPO_ROOT, "no-such-dir")), [])


class QuiverSourceTests(unittest.TestCase):
    def test_live_raises_without_key_and_terms(self):
        with self.assertRaisesRegex(NotImplementedError, r"no verified key \+ terms"):
            QuiverSource().live()


class SafetyRailTests(unittest.TestCase):
    def test_paper_only_is_true(self):
        self.assertIs(PAPER_ONLY, True)

    def test_kill_switch_ok_when_no_flag(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.assertTrue(kill_switch_ok(tmp))

    def test_kill_switch_refuses_when_flag_present(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with open(os.path.join(tmp, "KILL"), "w", encoding="utf-8") as f:
            f.write("stop")
        self.assertFalse(kill_switch_ok(tmp))

    def test_kill_switch_fails_closed_on_missing_root(self):
        self.assertFalse(kill_switch_ok(os.path.join(REPO_ROOT, "no-such-dir")))

    def test_kill_switch_fails_closed_on_unreadable_check(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch("signal_sim.safety.os.stat", side_effect=PermissionError):
            self.assertFalse(kill_switch_ok(tmp))

    def test_kill_switch_default_root_is_repo_root_not_cwd(self):
        self.assertTrue(os.path.samefile(KILL_ROOT, REPO_ROOT))

    def test_honest_timestamps_pass(self):
        occurred, filed, observed = assert_event_timestamps(honest_event())
        self.assertLessEqual(occurred, filed)
        self.assertLessEqual(filed, observed)

    def test_observed_before_filed_is_lookahead(self):
        event = honest_event(
            filed_at="2026-08-30T21:00:00Z", observed_at="2026-08-05T12:00:00Z"
        )
        with self.assertRaises(LookaheadError):
            assert_event_timestamps(event)

    def test_filed_before_occurred_is_lookahead(self):
        event = honest_event(
            occurred_at="2026-08-20T00:00:00Z", filed_at="2026-08-10T21:00:00Z"
        )
        with self.assertRaises(LookaheadError):
            assert_event_timestamps(event)

    def test_every_poison_event_is_individually_rejected(self):
        path = os.path.join(FIXTURES_DIR, POISON_FIXTURE)
        with open(path, "r", encoding="utf-8-sig") as f:
            poison_events = json.load(f)
        self.assertGreaterEqual(len(poison_events), 1)
        for event in poison_events:
            with self.assertRaises(LookaheadError):
                assert_event_timestamps(event)

    def test_naive_or_date_only_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            assert_event_timestamps(honest_event(filed_at="2026-08-10"))

    def test_missing_timestamp_is_rejected(self):
        event = honest_event()
        del event["filed_at"]
        with self.assertRaises(ValueError):
            assert_event_timestamps(event)

    def _assert_no_broker_fragments(self, scan_dir, suffix):
        scanned = 0
        for root, _dirs, files in os.walk(scan_dir):
            for name in files:
                if not name.endswith(suffix):
                    continue
                path = os.path.join(root, name)
                with open(path, "r", encoding="utf-8-sig") as f:
                    source = f.read().lower()
                for fragment in FORBIDDEN_BROKER_FRAGMENTS:
                    self.assertNotIn(fragment, source, f"{fragment!r} in {path}")
                scanned += 1
        self.assertGreater(scanned, 0, f"no {suffix} files scanned in {scan_dir}")

    def test_no_live_broker_hostnames_in_package(self):
        self._assert_no_broker_fragments(os.path.join(REPO_ROOT, "signal_sim"), ".py")

    def test_no_live_broker_hostnames_in_fixtures(self):
        self._assert_no_broker_fragments(FIXTURES_DIR, ".json")


if __name__ == "__main__":
    unittest.main()
